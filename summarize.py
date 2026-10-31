"""Summarize + importance-score non-junk mail with Claude. Runs a few calls in parallel.

Every AI call in the app goes through call_claude_json / call_claude here. They use the Claude
API (claude_api.py) when ANTHROPIC_API_KEY is set, and otherwise the local `claude` CLI on the
user's subscription. MAILMIND_AI=api or =cli picks one explicitly.
Usage: python3 summarize.py [limit] [workers]
"""
import sys
import os
import re
import json
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(__file__))
import store
import claude_api

WORKERS = 4
MODEL = os.environ.get("MAILMIND_MODEL")  # optional, e.g. claude-haiku-5-5
TRIAGE_EFFORT = "low"      # API only. Triage runs on every new message and is a short classify-and-summarize
NEUTRAL_CWD = tempfile.gettempdir()        # run claude away from project context

PROMPT = """You are triaging ONE email for a personal dashboard. Reply with ONLY a JSON object (no prose, no code fence) with these keys:
- "summary": 1-2 sentence plain-English summary of what this email is and what (if anything) it wants.
- "importance": integer 1-5. 5 = urgent / personal / time-sensitive / financial / needs a reply; 3 = useful but not urgent; 1 = trivial or purely informational.
- "category": one of "action-needed","personal","work-job","finance","calendar","info","other".
- "action": a short next step if one is needed (e.g. "Reply with availability"), else "".

Email:
From: {frm}
Subject: {subject}
Date: {date}
Body:
{body}
"""


# --- triage schema: when the CLI supports --json-schema the model returns a guaranteed-valid
#     object in a top-level `structured_output` field, killing the old regex-scrape parse-error class.
TRIAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "importance": {"type": "integer", "minimum": 1, "maximum": 5},
        "category": {"type": "string",
                     "enum": ["action-needed", "personal", "work-job", "finance", "calendar", "info", "other"]},
        "action": {"type": "string"},
    },
    "required": ["summary", "importance", "category"],
}

_SCHEMA_OK = None


def _supports_schema():
    """Cache whether the installed claude CLI accepts --json-schema (cheap help-text probe,
    no API call). Older CLIs fall back to the text + regex path automatically."""
    global _SCHEMA_OK
    if _SCHEMA_OK is None:
        if os.environ.get("MAILMIND_NO_SCHEMA"):
            _SCHEMA_OK = False
        else:
            try:
                r = subprocess.run(["claude", "--help"], capture_output=True, text=True,
                                   timeout=15, cwd=NEUTRAL_CWD)
                _SCHEMA_OK = "--json-schema" in (r.stdout + r.stderr)
            except Exception:
                _SCHEMA_OK = False
    return _SCHEMA_OK


def engine():
    """Which engine AI calls use, "api" or "cli": MAILMIND_AI when it names one, else the API
    whenever a key is set."""
    forced = os.environ.get("MAILMIND_AI", "").strip().lower()
    if forced in ("api", "cli"):
        return forced
    return "api" if claude_api.key() else "cli"


def describe_engine():
    """One line for the startup log."""
    if engine() == "api":
        return "Claude API · " + model_label() + " · key from " + claude_api.key_source()
    return "claude CLI" + (" · " + MODEL if MODEL else "")


def model_label():
    """The model name stored beside each AI result and shown under it in the UI."""
    if engine() == "api":
        return MODEL or claude_api.MODEL
    return MODEL or "claude-cli"


_last = {"ok": None, "ts": 0, "error": ""}   # how the most recent AI call went, for status()


def _plain(e):
    """A failed call's reason in words the setup card can show (never the prompt)."""
    s = str(e)
    if isinstance(e, FileNotFoundError):
        return "the claude command isn't installed"
    if " 401" in s or "authentication" in s.lower() or "invalid x-api-key" in s.lower():
        return "the API key was rejected"
    if "login" in s.lower() or "logged in" in s.lower():
        return "the claude command isn't signed in"
    if " 429" in s or "rate" in s.lower():
        return "rate-limited for now"
    if isinstance(e, subprocess.TimeoutExpired) or "timed out" in s.lower():
        return "it timed out"
    return s[:140] or type(e).__name__


def _noted(fn):
    """Record each call's outcome in _last, passing its result or exception through unchanged."""
    def run(*a, **kw):
        try:
            out = fn(*a, **kw)
        except Exception as e:
            _last.update(ok=False, ts=int(time.time()), error=_plain(e))
            raise
        _last.update(ok=True, ts=int(time.time()), error="")
        return out
    run.__name__, run.__doc__ = fn.__name__, fn.__doc__
    return run


def status():
    """What the UI says about AI, with no call made: the engine ("api", "cli" or "none" when
    neither a key nor the claude command is there), its model, where the key came from, and how
    the most recent call went (last_ok None until one has run)."""
    eng = engine()
    ready = bool(claude_api.key()) if eng == "api" else bool(shutil.which("claude"))
    return {"engine": eng if ready else "none", "model": model_label() if ready else "",
            "key_from": claude_api.key_source() if ready and eng == "api" else "",
            "last_ok": _last["ok"], "last_ts": _last["ts"], "last_error": _last["error"]}


@_noted
def call_claude(prompt):
    """Plain text result (used by summarize_thread). Unchanged signature."""
    if engine() == "api":
        return claude_api.message(prompt, model=MODEL)
    cmd = ["claude", "-p", prompt, "--output-format", "text"]
    if MODEL:
        cmd += ["--model", MODEL]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=NEUTRAL_CWD)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:200] or "claude failed")
    return r.stdout.strip()


def _loose_json(out):
    m = re.search(r"\{.*\}", out, re.S)
    return json.loads(m.group(0)) if m else {}


@_noted
def call_claude_json(prompt, schema, effort=None):
    """Ask Claude for a JSON object matching `schema`. Returns the validated dict from
    structured output (the API's output_config.format, or the CLI's --json-schema), else
    best-effort regex-parsed JSON from the text. Raises RuntimeError ONLY on a transport
    failure (API error, or a non-zero CLI exit) so the caller can distinguish retry from
    fallback; a successful-but-unparseable reply yields {}. `effort` applies to the API only."""
    if engine() == "api":
        out = claude_api.message(prompt, None if os.environ.get("MAILMIND_NO_SCHEMA") else schema,
                                 model=MODEL, effort=effort)
        try:
            obj = json.loads(out)
        except ValueError:
            obj = None
        if isinstance(obj, dict):
            return obj
        try:
            return _loose_json(out)
        except Exception:
            return {}
    use = _supports_schema()
    cmd = ["claude", "-p", prompt]
    cmd += (["--output-format", "json", "--json-schema", json.dumps(schema)] if use
            else ["--output-format", "text"])
    if MODEL:
        cmd += ["--model", MODEL]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=NEUTRAL_CWD)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:200] or "claude failed")
    out = r.stdout
    if use:
        try:
            obj = json.loads(out)
            if isinstance(obj.get("structured_output"), dict):
                return obj["structured_output"]
            out = obj.get("result", out)   # flag accepted but no structured field -> parse the prose
        except Exception:
            pass
    try:
        return _loose_json(out)
    except Exception:
        return {}


def parse(out):
    m = re.search(r"\{.*\}", out, re.S)
    if not m:
        raise ValueError("no JSON")
    obj = json.loads(m.group(0))
    imp = int(obj.get("importance", 2))
    return (str(obj.get("summary", ""))[:600], max(1, min(5, imp)),
            str(obj.get("category", "other"))[:32], str(obj.get("action", ""))[:200])


def summarize_one(row):
    frm = f"{row['from_name']} <{row['from_addr']}>" if row["from_name"] else row["from_addr"]
    prompt = PROMPT.format(frm=frm, subject=row["subject"] or "(no subject)",
                           date=row["date_iso"] or "", body=(row["snippet"] or "")[:1800])
    try:
        obj = call_claude_json(prompt, TRIAGE_SCHEMA, effort=TRIAGE_EFFORT)
    except Exception:
        return row["pk"], None  # transient (rate-limit/timeout) -> leave for retry
    if obj.get("summary"):
        try:
            imp = max(1, min(5, int(obj.get("importance", 2))))
        except Exception:
            imp = 2
        return row["pk"], (str(obj["summary"])[:600], imp,
                           str(obj.get("category", "other"))[:32],
                           str(obj.get("action", ""))[:200], model_label())
    # successful call but no usable summary -> store a fallback, don't loop forever
    return row["pk"], ((row["subject"] or "(no subject)"), 2, "other", "", "parse-error")


def summarize_pending(limit=200, workers=WORKERS, log=lambda *_: None):
    c = store.conn()
    rows = store.needs_summary(c, limit)
    if not rows:
        c.close()
        return 0
    log(f"summarizing {len(rows)} messages with {workers} workers...")
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(summarize_one, r): r for r in rows}
        for f in as_completed(futs):
            pk, payload = f.result()
            if payload is None:
                continue  # transient failure -> stays in needs_summary for next run
            s, imp, cat, act, model = payload
            store.save_summary(c, pk, s, imp, cat, act, model,
                               datetime.now(timezone.utc).isoformat())
            c.commit()
            done += 1
            if done % 25 == 0 or done == len(rows):
                log(f"  {done}/{len(rows)}")
    c.close()
    return done


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    workers = int(sys.argv[2]) if len(sys.argv) > 2 else WORKERS
    summarize_pending(limit, workers, log=lambda m: print(m, flush=True))
    print("stats:", store.stats(store.conn()))


if __name__ == "__main__":
    main()

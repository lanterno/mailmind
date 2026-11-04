"""Summarize + importance-score non-junk mail with Claude. Runs a few calls in parallel.

Every AI call in the app goes through call_claude_json / call_claude here, which call the Claude
API through the Assistant's gateway (mailmind.bootstrap.assistant). With no API key AI is off: those
calls raise AIOff before anything is sent, and status() says "none" rather than counting them as
failed calls.
Usage: python3 summarize.py [limit] [workers]
"""
import sys
import os
import re
import json
import time
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(__file__))
import store
from mailmind.assistant.application.ports import AIOff   # the calls below raise it when AI is off
from mailmind.assistant.domain import models
from mailmind.bootstrap import assistant

WORKERS = 4
MODEL = os.environ.get("MAILMIND_MODEL")  # optional, e.g. claude-haiku-5-5
TRIAGE_EFFORT = "low"      # triage runs on every new message and is a short classify-and-summarize

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


# --- triage schema: structured output returns an object that matches it, so a triage reply needs
#     no regex scrape (the old parse-error class).
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


def engine():
    """"api" when an API key resolves, else "none": AI is off."""
    return "api" if assistant.gateway().on else "none"


def describe_engine():
    """One line for the startup log."""
    return f"Claude API through {assistant.transport()} · {model_label()} · {_key_note()}"


def _key_note():
    """Where the API key came from, or why there is none."""
    key, source = assistant.key()
    if key:
        return "key from " + source
    if source == "mailmind.config.json":
        return "no key, so AI is off: the anthropic_api_key in mailmind.config.json can't be read"
    return "no key, so AI is off: set anthropic_api_key in mailmind.config.json, or ANTHROPIC_API_KEY"


def model_label():
    """The model name stored beside each AI result and shown under it in the UI."""
    return MODEL or models.DEFAULT


_last = {"ok": None, "ts": 0, "error": ""}   # how the most recent AI call went, for status()


def _plain(e):
    """A failed call's reason in words the setup card can show (never the prompt)."""
    s = str(e)
    if " 401" in s or "authentication" in s.lower() or "invalid x-api-key" in s.lower():
        return "the API key was rejected"
    if " 429" in s or "rate" in s.lower():
        return "rate-limited for now"
    if "timed out" in s.lower():
        return "it timed out"
    return s[:140] or type(e).__name__


def _noted(fn):
    """Record each call's outcome in _last, passing its result or exception through unchanged.
    A call refused because AI is off was never made, so it isn't recorded."""
    def run(*a, **kw):
        try:
            out = fn(*a, **kw)
        except AIOff:
            raise
        except Exception as e:
            _last.update(ok=False, ts=int(time.time()), error=_plain(e))
            raise
        _last.update(ok=True, ts=int(time.time()), error="")
        return out
    run.__name__, run.__doc__ = fn.__name__, fn.__doc__
    return run


def status():
    """What the UI says about AI, with no call made: the engine ("api", or "none" with no key),
    its model, where the key came from, and how the most recent call went (last_ok None until
    one has run)."""
    on = engine() == "api"
    return {"engine": "api" if on else "none", "model": model_label() if on else "",
            "key_from": assistant.key()[1] if on else "",
            "last_ok": _last["ok"], "last_ts": _last["ts"], "last_error": _last["error"]}


@_noted
def call_claude(prompt, feature="other"):
    """Plain text result (used by summarize_thread). `feature` names the call in the usage ledger."""
    return assistant.gateway().ask(prompt, feature=feature, model=MODEL).text


def _loose_json(out):
    m = re.search(r"\{.*\}", out, re.S)
    return json.loads(m.group(0)) if m else {}


@_noted
def call_claude_json(prompt, schema, effort=None, feature="other"):
    """Ask Claude for a JSON object matching `schema`. Returns the dict structured output
    (output_config.format) gives, else best-effort regex-parsed JSON from the text. Raises
    RuntimeError ONLY when no reply came (AIOff, one, when AI is off), so the caller can tell
    retry from fallback; a successful-but-unparseable reply yields {}. `feature` names the call in
    the usage ledger."""
    out = assistant.gateway().ask(prompt, None if os.environ.get("MAILMIND_NO_SCHEMA") else schema,
                                  feature=feature, model=MODEL, effort=effort).text
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
        obj = call_claude_json(prompt, TRIAGE_SCHEMA, effort=TRIAGE_EFFORT, feature="triage")
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

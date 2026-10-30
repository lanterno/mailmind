"""Summarize + importance-score non-junk mail via the local `claude` CLI
(uses the user's subscription, no API key). Runs a few calls in parallel.
Usage: python3 summarize.py [limit] [workers]
"""
import sys
import os
import re
import json
import subprocess
import tempfile
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(__file__))
import store

WORKERS = 4
MODEL = os.environ.get("MAILMIND_MODEL")  # optional, e.g. claude-haiku-4-5
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


def model_label():
    """The model name stored beside each AI result and shown under it in the UI."""
    return MODEL or "claude-cli"


def call_claude(prompt):
    """Plain text result (used by summarize_thread). Unchanged signature."""
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


def call_claude_json(prompt, schema):
    """Run claude expecting a JSON object matching `schema`. Returns the validated dict
    from the top-level `structured_output` field when the CLI supports --json-schema,
    else best-effort regex-parsed JSON from the text path. Raises RuntimeError ONLY on a
    transport failure (non-zero exit) so the caller can distinguish retry from fallback;
    a successful-but-unparseable reply yields {}."""
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
        obj = call_claude_json(prompt, TRIAGE_SCHEMA)
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

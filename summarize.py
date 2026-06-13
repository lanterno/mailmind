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


def call_claude(prompt):
    cmd = ["claude", "-p", prompt, "--output-format", "text"]
    if MODEL:
        cmd += ["--model", MODEL]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=NEUTRAL_CWD)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:200] or "claude failed")
    return r.stdout.strip()


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
        out = call_claude(prompt)
    except Exception:
        return row["pk"], None  # transient (rate-limit/timeout) -> leave for retry
    try:
        s, imp, cat, act = parse(out)
        return row["pk"], (s, imp, cat, act, (MODEL or "claude-cli"))
    except Exception:  # claude replied but not JSON -> store a fallback, don't loop forever
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

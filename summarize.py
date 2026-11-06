"""Summarize + importance-score non-junk mail with Claude. Runs a few calls in parallel.

Every AI call in the app goes through call_claude_json / call_claude here, which call the Claude
API through the Assistant's gateway (mailmind.bootstrap.assistant). With no API key AI is off: those
calls raise AIOff before anything is sent, and status() says "none" rather than counting them as
failed calls. The cost governor may hold a call back instead (HeldBack: OverLimit for one someone
asked for at the daily limit, Deferred for background work), which isn't a failed call either.
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
from mailmind.assistant.application.ports import AIOff, HeldBack, ModelError, NotSent, OverLimit   # noqa: F401 (re-exported)
from mailmind.assistant.domain import models, pricing
from mailmind.bootstrap import assistant

WORKERS = 4
MODEL = os.environ.get("MAILMIND_MODEL")  # optional, e.g. claude-haiku-5-5
TRIAGE_EFFORT = "low"      # triage runs on every new message and is a short classify-and-summarize

# A backlog goes to the Message Batches API, at half price, one batch at a time. New mail stays synchronous.
BATCH_FEATURE = "triage-batch"
BATCH_MIN = 100            # this many waiting is a backlog (a first import, or after an outage)
BATCH_MAX = 2000           # requests in one batch
BATCH_KEEP_NEWEST = 20     # of a backlog, triaged now, so the brief has something to say meanwhile
BATCH_POLL = 300           # seconds between asking whether a batch has ended
BATCH_SHARE = 2            # a batch books at most 1/this of what today's limit leaves, the rest for whoever asks
BATCH_SEND_GIVE_UP = 900   # seconds: a batch still being sent after this was cut off by a crash

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
    A call that was never made (AI off, or held back by the cost governor) isn't recorded."""
    def run(*a, **kw):
        try:
            out = fn(*a, **kw)
        except NotSent:
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
    its model, where the key came from, how the most recent call went (last_ok None until one
    has run), and what today's calls cost so far in micro-dollars (None when the usage ledger
    can't be read)."""
    on = engine() == "api"
    return {"engine": "api" if on else "none", "model": model_label() if on else "",
            "key_from": assistant.key()[1] if on else "",
            "last_ok": _last["ok"], "last_ts": _last["ts"], "last_error": _last["error"],
            "spent_today_micros": spent_today(), "limit_micros": assistant.governor().limit_micros(),
            **_batch_note(on)}


def _batch_note(on):
    """`batch_waiting`, when there's a batch out to say so of."""
    if not on:
        return {}
    try:
        n = batch_status()["waiting"]
    except Exception:
        return {}
    return {"batch_waiting": n} if n else {}


def spent_today():
    """What today's AI calls cost so far, in micro-dollars, or None when the ledger can't be read."""
    try:
        return assistant.gateway().spent_today()
    except Exception as e:
        print(f"[ai] the usage ledger can't be read: {type(e).__name__}", flush=True)
        return None


def usage(days=30):
    """Each of the last `days` local days, newest first: its AI calls and their cost in
    micro-dollars, in all and by feature (the usage ledger's report)."""
    return assistant.gateway().usage(days)


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
    return _object_in(out)


def _object_in(out):
    """The JSON object a reply holds, or {} when it holds none."""
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


def _triage_prompt(row):
    frm = f"{row['from_name']} <{row['from_addr']}>" if row["from_name"] else row["from_addr"]
    return PROMPT.format(frm=frm, subject=row["subject"] or "(no subject)",
                         date=row["date_iso"] or "", body=(row["snippet"] or "")[:1800])


def _assessment(row, obj):
    """What to store for a message from what triage answered. A reply with no usable summary stores a
    fallback, so a message doesn't go round for ever."""
    if obj.get("summary"):
        try:
            imp = max(1, min(5, int(obj.get("importance", 2))))
        except Exception:
            imp = 2
        return (str(obj["summary"])[:600], imp, str(obj.get("category", "other"))[:32],
                str(obj.get("action", ""))[:200], model_label())
    return ((row["subject"] or "(no subject)"), 2, "other", "", "parse-error")


def summarize_one(row):
    try:
        obj = call_claude_json(_triage_prompt(row), TRIAGE_SCHEMA, effort=TRIAGE_EFFORT, feature="triage")
    except Exception:
        return row["pk"], None  # transient (rate-limit/timeout) -> leave for retry
    return row["pk"], _assessment(row, obj)


def summarize_pending(limit=200, workers=WORKERS, log=lambda *_: None):
    """Triage up to `limit` messages that have no summary yet, `workers` at a time, and return how
    many were summarized. With AI off, or while the cost governor holds triage back, it returns 0
    at once: not one message is tried."""
    if engine() == "none" or assistant.gateway().held_back("triage", MODEL):
        return 0
    c = store.conn()
    out = store.batch_open(c)
    rows = store.needs_summary(c, limit, newer_than=out["cutoff_ts"] if out else None)
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


@_noted
def _send_batch(requests, name):
    return assistant.gateway().submit_batch(requests, None if os.environ.get("MAILMIND_NO_SCHEMA") else TRIAGE_SCHEMA,
                                            feature=BATCH_FEATURE, model=name, effort=TRIAGE_EFFORT)


def batch_submit(at_least=None, keep=None, log=lambda *_: None):
    """When a backlog is waiting and no batch is out, send it as one, and return how many it took. It
    takes what fits in its share of today's limit, by estimate, and leaves the newest to go now."""
    at_least, keep = BATCH_MIN if at_least is None else at_least, BATCH_KEEP_NEWEST if keep is None else keep
    gw = assistant.gateway()
    if engine() == "none" or gw.held_back(BATCH_FEATURE, MODEL):
        return 0
    c = store.conn()
    try:
        if store.batch_open(c):
            return 0
        rows = [r for r in store.needs_summary(c, keep + BATCH_MAX) if r["message_key"] is not None]
    finally:
        c.close()
    if len(rows) < at_least:
        return 0
    name = MODEL or models.DEFAULT
    room = gw.room() // BATCH_SHARE
    requests, keys, estimate, cutoff = [], [], 0, 0
    for r in rows[keep:]:
        text = _triage_prompt(r)
        cost = pricing.batch_estimate(name, len(text))
        if estimate + cost > room:
            break
        requests.append((str(r["message_key"]), text))
        keys.append(r["message_key"])
        estimate += cost
        cutoff = max(cutoff, r["ts"] or 0)
    if not requests:
        return 0
    c = store.conn()
    try:
        batch_id = store.batch_begin(c, BATCH_FEATURE, name, keys, estimate, cutoff, int(time.time()))
        if batch_id is None:
            return 0
        try:
            remote_id = _send_batch(requests, name)
        except Exception as e:                  # it didn't go, so they wait; the next round tries again
            store.batch_drop(c, batch_id)
            c.commit()
            log(f"a batch of {len(requests)} wasn't sent: {type(e).__name__}")
            return 0
        store.batch_sent(c, batch_id, remote_id, int(time.time()))
        c.commit()
    finally:
        c.close()
    log(f"sent a batch of {len(requests)}, at most about {pricing.usd(estimate)}")
    return len(requests)


def _store_answers(c, batch, results):
    """Each answer a batch brought, for each copy of its message and under its key. How many."""
    keys, stored, when = store.batch_keys(c, batch["id"]), 0, datetime.now(timezone.utc).isoformat()
    for r in results:
        key = int(r.custom_id) if r.custom_id.isdigit() else None
        if key not in keys or r.reply is None:
            continue                            # no answer: it is waiting again once the batch is closed
        row = c.execute("SELECT * FROM messages WHERE message_key=? LIMIT 1", (key,)).fetchone()
        obj = _object_in(r.reply.text)
        if row is not None:
            store.save_summary(c, row["pk"], *_assessment(row, obj), when)
        elif obj.get("summary"):                # every copy has gone: it may come back
            store.save_assessment(c, key, *_assessment({"subject": ""}, obj), when)
        stored += 1
    return stored


def batch_collect(log=lambda *_: None):
    """Bring in the answers of a batch that has ended, and return how many it stored. Nothing here sends
    a message, so neither the limit nor someone using AI holds it back: these answers are paid for."""
    if engine() == "none":
        return 0
    gw, now, stored = assistant.gateway(), int(time.time()), 0
    c = store.conn()
    try:
        for b in store.batches_in_state(c, "submitting"):
            if b["created_at"] < now - BATCH_SEND_GIVE_UP:
                store.batch_drop(c, b["id"])
                log("a batch was cut off while it was being sent; its messages are waiting again")
        c.commit()
        out = store.batch_open(c)
        due = out is not None and out["state"] == "running" and out["polled_at"] + BATCH_POLL <= now
        if due:
            try:
                ended = gw.batch_ended(out["remote_id"])
                results = gw.batch_results(out["remote_id"], feature=out["feature"], model=out["model"]) if ended else None
            except NotSent:
                raise
            except ModelError as e:
                ended, results = False, None
                if e.status == 404:             # another key's, or removed: nothing more will come of it
                    store.batch_drop(c, out["id"])
                    log("the API no longer has a batch; its messages are waiting again")
                else:
                    store.batch_polled(c, out["id"], now)
                    log(f"couldn't read a batch: {type(e).__name__} {e.status}")
                c.commit()
            if results is not None:
                stored = _store_answers(c, out, results)
                store.batch_collected(c, out["id"], now)
                log(f"a batch brought {stored} answers")
            elif not ended:
                store.batch_polled(c, out["id"], now)
            c.commit()
        for b in store.batches_in_state(c, "collected"):
            try:                                # what it holds is mail: it stays at the API no longer than needed
                gw.discard_batch(b["remote_id"])
            except NotSent:
                raise
            except ModelError as e:
                if e.status != 404:
                    log(f"couldn't remove a batch: {type(e).__name__} {e.status}")
                    continue
            store.batch_discarded(c, b["id"])
            c.commit()
    except NotSent:
        pass
    finally:
        c.close()
    return stored


def batch_status():
    """What's out in a batch, with no call made: how many messages, and what it may cost."""
    c = store.conn()
    try:
        out = store.batch_open(c)
        return {"waiting": store.batch_waiting(c), "state": out["state"] if out else None,
                "estimate_micros": out["estimate_micros"] if out else 0}
    finally:
        c.close()

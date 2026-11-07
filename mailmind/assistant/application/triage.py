"""Summarizing and scoring mail that isn't junk: a few calls in parallel for what's new, and a backlog as a batch at half
price."""
import os
import time
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

from mailmind.assistant.application.calls import Calls, object_in
from mailmind.assistant.application.gateway import Gateway
from mailmind.assistant.application.ports import Answers, BatchResult, BatchStatus, Connect, Mail, ModelError, NotSent
from mailmind.assistant.domain import models, pricing
from mailmind.kernel.row import Connection, Row
from mailmind.platform.logging import get_logger

assess_log = get_logger("assess")

type Log = Callable[[str], None]
type Assessment = tuple[str, int, str, str, str]    # what's stored of a message: summary, importance, category, action, model

WORKERS = 4
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
_last = {"ok": None, "ts": 0, "error": ""}   # how the most recent AI call went, for status()


class Triage:
    WORKERS = WORKERS
    TRIAGE_EFFORT = "low"      # triage runs on every new message and is a short classify-and-summarize
    TRIAGE_SCHEMA = TRIAGE_SCHEMA
    PROMPT = PROMPT
    # A backlog goes to the Message Batches API, at half price, one batch at a time. New mail stays synchronous.
    BATCH_FEATURE = "triage-batch"
    BATCH_MIN = 100            # this many waiting is a backlog (a first import, or after an outage)
    BATCH_MAX = 2000           # requests in one batch
    BATCH_KEEP_NEWEST = 20     # of a backlog, triaged now, so the brief has something to say meanwhile
    BATCH_POLL = 300           # seconds between asking whether a batch has ended
    BATCH_SHARE = 2            # a batch books at most 1/this of what today's limit leaves, the rest for whoever asks
    BATCH_SEND_GIVE_UP = 900   # seconds: a batch still being sent after this was cut off by a crash

    ASSESS_BATCH = 300                        # messages triaged in one round
    ASSESS_YIELD = 45                         # seconds a round waits while the user is using AI

    def __init__(self, calls: Calls, gateway: Callable[[], Gateway], answers: Answers, connect: Connect, mail: Mail):
        self._calls = calls
        self._gateway = gateway
        self._answers = answers
        self._connect = connect
        self._mail = mail

    def _triage_prompt(self, row: Row) -> str:
        frm = f"{row['from_name']} <{row['from_addr']}>" if row["from_name"] else row["from_addr"]
        return self.PROMPT.format(frm=frm, subject=row["subject"] or "(no subject)",
                             date=row["date_iso"] or "", body=(row["snippet"] or "")[:1800])

    def _assessment(self, row: Row, obj: Mapping[str, Any]) -> Assessment:
        """What to store for a message from what triage answered. A reply with no usable summary stores a
        fallback, so a message doesn't go round for ever."""
        if obj.get("summary"):
            try:
                imp = max(1, min(5, int(obj.get("importance", 2))))
            except Exception:
                imp = 2
            return (str(obj["summary"])[:600], imp, str(obj.get("category", "other"))[:32],
                    str(obj.get("action", ""))[:200], self._calls.model_label())
        return ((row["subject"] or "(no subject)"), 2, "other", "", "parse-error")

    def summarize_one(self, row: Row) -> tuple[int, Assessment | None]:
        try:
            obj = self._calls.call_claude_json(self._triage_prompt(row), self.TRIAGE_SCHEMA, effort=self.TRIAGE_EFFORT, feature="triage")
        except Exception:
            return row["pk"], None  # transient (rate-limit/timeout) -> leave for retry
        return row["pk"], self._assessment(row, obj)

    def summarize_pending(self, limit: int = 200, workers: int = WORKERS, log: Log = lambda _: None) -> int:
        """Triage up to `limit` messages that have no summary yet, `workers` at a time, and return how
        many were summarized. With AI off, or while the cost governor holds triage back, it returns 0
        at once: not one message is tried."""
        if self._calls.engine() == "none" or self._gateway().held_back("triage", self._calls.MODEL):
            return 0
        c = self._connect()
        out = self._answers.batch_open(c)
        rows = self._answers.needs_summary(c, limit, newer_than=out["cutoff_ts"] if out else None)
        if not rows:
            c.close()
            return 0
        log(f"summarizing {len(rows)} messages with {workers} workers...")
        done = 0
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(self.summarize_one, r): r for r in rows}
            for f in as_completed(futs):
                pk, payload = f.result()
                if payload is None:
                    continue  # transient failure -> stays in needs_summary for next run
                s, imp, cat, act, model = payload
                self._answers.save_summary(c, pk, s, imp, cat, act, model,
                                   datetime.now(timezone.utc).isoformat())
                c.commit()
                done += 1
                if done % 25 == 0 or done == len(rows):
                    log(f"  {done}/{len(rows)}")
        c.close()
        return done

    def _send_batch(self, requests: list[tuple[str, str]], name: str) -> str:
        return self._calls.noting(lambda: self._gateway().submit_batch(
            requests, None if os.environ.get("MAILMIND_NO_SCHEMA") else self.TRIAGE_SCHEMA,
            feature=self.BATCH_FEATURE, model=name, effort=self.TRIAGE_EFFORT))

    def batch_submit(self, at_least: int | None = None, keep: int | None = None, log: Log = lambda _: None) -> int:
        """When a backlog is waiting and no batch is out, send it as one, and return how many it took. It
        takes what fits in its share of today's limit, by estimate, and leaves the newest to go now."""
        at_least, keep = self.BATCH_MIN if at_least is None else at_least, self.BATCH_KEEP_NEWEST if keep is None else keep
        gw = self._gateway()
        if self._calls.engine() == "none" or gw.held_back(self.BATCH_FEATURE, self._calls.MODEL):
            return 0
        c = self._connect()
        try:
            if self._answers.batch_open(c):
                return 0
            rows = [r for r in self._answers.needs_summary(c, keep + self.BATCH_MAX) if r["message_key"] is not None]
        finally:
            c.close()
        if len(rows) < at_least:
            return 0
        name = self._calls.MODEL or models.DEFAULT
        room = gw.room()                    # None: no governor, so no limit to keep within
        requests: list[tuple[str, str]] = []
        keys: list[int] = []
        estimate, cutoff = 0, 0
        for r in rows[keep:]:
            text = self._triage_prompt(r)
            cost = pricing.batch_estimate(name, len(text))
            if room is not None and estimate + cost > room // self.BATCH_SHARE:
                break
            requests.append((str(r["message_key"]), text))
            keys.append(r["message_key"])
            estimate += cost
            cutoff = max(cutoff, r["ts"] or 0)
        if not requests:
            return 0
        c = self._connect()
        try:
            batch_id = self._answers.batch_begin(c, self.BATCH_FEATURE, name, keys, estimate, cutoff, int(time.time()))
            if batch_id is None:
                return 0
            try:
                remote_id = self._send_batch(requests, name)
            except Exception as e:                  # it didn't go, so they wait; the next round tries again
                self._answers.batch_drop(c, batch_id)
                c.commit()
                log(f"a batch of {len(requests)} wasn't sent: {type(e).__name__}")
                return 0
            self._answers.batch_sent(c, batch_id, remote_id, int(time.time()))
            c.commit()
        finally:
            c.close()
        log(f"sent a batch of {len(requests)}, at most about {pricing.usd(estimate)}")
        return len(requests)

    def _store_answers(self, c: Connection, batch: Row, results: Iterable[BatchResult]) -> int:
        """Each answer a batch brought, for each copy of its message and under its key. How many."""
        keys, stored, when = self._answers.batch_keys(c, batch["id"]), 0, datetime.now(timezone.utc).isoformat()
        for r in results:
            key = int(r.custom_id) if r.custom_id.isdigit() else None
            if key not in keys or r.reply is None:
                continue                            # no answer: it is waiting again once the batch is closed
            row = self._mail.any_copy(c, key)
            obj = object_in(r.reply.text)
            if row is not None:
                self._answers.save_summary(c, row["pk"], *self._assessment(row, obj), when)
            elif obj.get("summary"):                # every copy has gone: it may come back
                self._answers.save_assessment(c, key, *self._assessment({"subject": ""}, obj), when)
            stored += 1
        return stored

    def batch_collect(self, log: Log = lambda _: None) -> int:
        """Bring in the answers of a batch that has ended, and return how many it stored. Nothing here sends
        a message, so neither the limit nor someone using AI holds it back: these answers are paid for."""
        if self._calls.engine() == "none":
            return 0
        gw, now, stored = self._gateway(), int(time.time()), 0
        c = self._connect()
        try:
            for b in self._answers.batches_in_state(c, "submitting"):
                if b["created_at"] < now - self.BATCH_SEND_GIVE_UP:
                    self._answers.batch_drop(c, b["id"])
                    log("a batch was cut off while it was being sent; its messages are waiting again")
            c.commit()
            out = self._answers.batch_open(c)
            if out is not None and out["state"] == "running" and out["polled_at"] + self.BATCH_POLL <= now:
                try:
                    ended = gw.batch_ended(out["remote_id"])
                    results = gw.batch_results(out["remote_id"], feature=out["feature"], model=out["model"]) if ended else None
                except NotSent:
                    raise
                except ModelError as e:
                    ended, results = False, None
                    if e.status == 404:             # another key's, or removed: nothing more will come of it
                        self._answers.batch_drop(c, out["id"])
                        log("the API no longer has a batch; its messages are waiting again")
                    else:
                        self._answers.batch_polled(c, out["id"], now)
                        log(f"couldn't read a batch: {type(e).__name__} {e.status}")
                    c.commit()
                if results is not None:
                    stored = self._store_answers(c, out, results)
                    self._answers.batch_collected(c, out["id"], now)
                    log(f"a batch brought {stored} answers")
                elif not ended:
                    self._answers.batch_polled(c, out["id"], now)
                c.commit()
            for b in self._answers.batches_in_state(c, "collected"):
                try:                                # what it holds is mail: it stays at the API no longer than needed
                    gw.discard_batch(b["remote_id"])
                except NotSent:
                    raise
                except ModelError as e:
                    if e.status != 404:
                        log(f"couldn't remove a batch: {type(e).__name__} {e.status}")
                        continue
                self._answers.batch_discarded(c, b["id"])
                c.commit()
        except NotSent:
            pass
        finally:
            c.close()
        return stored

    def batch_status(self) -> BatchStatus:
        """What's out in a batch, with no call made: how many messages, and what it may cost."""
        c = self._connect()
        try:
            out = self._answers.batch_open(c)
            return {"waiting": self._answers.batch_waiting(c), "state": out["state"] if out else None,
                    "estimate_micros": out["estimate_micros"] if out else 0}
        finally:
            c.close()

    def round(self) -> int | None:
        """The assess job's round: triage up to ASSESS_BATCH messages that have no summary yet. It runs
        on its own lane, so a slow model never holds up a sync, and with AI off it tries nothing. A full
        batch means more are waiting, so the next round follows at once. While the user is using AI the
        round waits until it has yielded long enough; held back otherwise (the limit, the breaker), it
        waits its interval. A batch's answers are brought in first, whatever holds the rest back, and a
        backlog goes out in the next batch rather than through this round's calls."""
        def say(m: str) -> None:                       # the app's own words, and counts and an error's class
            assess_log.info("%(what)s", {"what": m})

        self.batch_collect(say)
        why = self._gateway().held_back("triage", self._calls.MODEL)
        if why:
            return self.ASSESS_YIELD if why == "yielding" else None
        if not os.environ.get("MAILMIND_NO_BATCHES"):
            self.batch_submit(log=say)
        done = self.summarize_pending(limit=self.ASSESS_BATCH, workers=self.WORKERS)
        if done:
            assess_log.info("%d summarized", done)
        return 0 if done >= self.ASSESS_BATCH else None

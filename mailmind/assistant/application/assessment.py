"""Summarizing and scoring mail that isn't junk: a few calls in parallel for what's new, and a backlog as a batch at half
price, which only Claude takes. A batch out when a person chooses another provider is let go here, with nothing sent: its
messages wait for a summary again, and what it booked is free again.

Only mail from the last RECENT_DAYS is summarized in the background: older mail is summarized only when a person asks for a
summary, so turning AI on over a mailbox of years spends nothing on its past. Background summaries ask the provider's small
model (`Calls.background_label`), and each is stored under it."""
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from mailmind.assistant.application.calls import Calls
from mailmind.assistant.application.gateway import Gateway
from mailmind.assistant.application.ports import Answers, BatchResult, BatchStatus, Mail, ModelError, NotSent, Reply
from mailmind.assistant.domain import pricing, prompts
from mailmind.assistant.domain.schema import object_in
from mailmind.kernel.ai_provider import DEFAULT_PROVIDER, AiProvider
from mailmind.kernel.assessment import ASSESSED, REFUSED, UNUSABLE, Assessment
from mailmind.kernel.ids import MessageKey, Pk
from mailmind.kernel.row import Connection, Row
from mailmind.platform import environment, sse
from mailmind.platform.db.uow import UnitOfWork
from mailmind.platform.logging import get_logger

assess_log = get_logger("assess")

type Log = Callable[[str], None]

WORKERS = 4
RECENT_DAYS = 30           # how far back the background summarizes; older mail only when a person asks for a summary

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


class Assessments:
    WORKERS = WORKERS
    TRIAGE_EFFORT = "low"      # triage runs on every new message and is a short classify-and-summarize
    TRIAGE_SCHEMA = TRIAGE_SCHEMA
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
    RECENT_DAYS = RECENT_DAYS

    def __init__(self, calls: Calls, gateway: Callable[[], Gateway], answers: Answers, uow: UnitOfWork, mail: Mail,
                 provider: Callable[[], AiProvider] = lambda: DEFAULT_PROVIDER, clock: Callable[[], float] = time.time):
        self._calls = calls
        self._gateway = gateway
        self._answers = answers
        self._uow = uow
        self._mail = mail
        self._provider = provider           # the provider a person chose, AI mode on or off
        self._clock = clock

    def _recent(self) -> int:
        """The moment the background's reach starts: mail from before it waits to be opened."""
        return int(self._clock()) - self.RECENT_DAYS * 86400

    def _triage_prompt(self, row: Row) -> str:
        frm = f"{row['from_name']} <{row['from_addr']}>" if row["from_name"] else row["from_addr"]
        return prompts.TRIAGE.fill(frm=frm, subject=row["subject"] or "(no subject)",
                                   date=row["date_iso"] or "", body=(row["snippet"] or "")[:1800])

    def _assessment(self, row: Row, reply: Reply, prompt_version: int | None, asked: str | None = None) -> Assessment:
        """What to store for a message from what triage answered, under the model it `asked` (the one in force unless
        named: a batch's answers name the model the batch asked). A reply with no usable summary stores a fallback, so a
        message doesn't go round for ever."""
        obj = object_in(reply.text)
        if obj.get("summary"):
            return Assessment(str(obj["summary"])[:600], obj.get("importance", 2), str(obj.get("category", "other"))[:32],
                              str(obj.get("action", ""))[:200], asked or self._calls.model_label(),
                              reply.model, prompt_version, ASSESSED)
        return Assessment(row["subject"] or "(no subject)", 2, "other", "", "parse-error",
                          reply.model, prompt_version, REFUSED if reply.refused else UNUSABLE)

    def _save(self, c: Connection, pk: Pk, said: Assessment, when: str) -> None:
        self._answers.save_summary(c, pk, said.summary, said.importance, said.category, said.action, said.model, when,
                                   said.served_by, said.prompt_version, said.outcome)

    def summarize_one(self, row: Row, model: str | None = None) -> tuple[Pk, Assessment | None]:
        """One message triaged by `model`, the background's unless named."""
        model = model or self._calls.background_label()
        try:
            reply = self._calls.reply_to(self._triage_prompt(row), self.TRIAGE_SCHEMA, effort=self.TRIAGE_EFFORT,
                                         feature="triage", model=model)
        except Exception:
            return row["pk"], None  # transient (rate-limit/timeout) -> leave for retry
        return row["pk"], self._assessment(row, reply, prompts.TRIAGE.version, model)

    def summarize_pending(self, limit: int = 200, workers: int = WORKERS, log: Log = lambda _: None) -> int:
        """Triage up to `limit` messages from the last RECENT_DAYS that have no summary yet, `workers` at a time, and
        return how many were summarized. With AI off, or while the cost governor holds triage back, it returns 0 at once:
        not one message is tried."""
        model = self._calls.background_label()
        if self._calls.engine() == "none" or self._gateway().held_back("triage", model):
            return 0
        recent = self._recent()
        with self._uow.read() as c:
            out = self._answers.batch_open(c)
            rows = self._answers.needs_summary(c, limit, newer_than=max(out["cutoff_ts"] or 0, recent) if out else recent)
        if not rows:
            return 0
        log(f"summarizing {len(rows)} messages with {workers} workers...")
        done = 0
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(self.summarize_one, r, model): r for r in rows}
            for f in as_completed(futs):
                pk, said = f.result()
                if said is None:
                    continue  # transient failure -> stays in needs_summary for next run
                with self._uow.write() as c:
                    self._save(c, pk, said, datetime.now(timezone.utc).isoformat())
                done += 1
                if done % 25 == 0 or done == len(rows):
                    log(f"  {done}/{len(rows)}")
        return done

    def _send_batch(self, requests: list[tuple[str, str]], name: str) -> str:
        return self._calls.noting(lambda: self._gateway().submit_batch(
            requests, None if environment.NO_SCHEMA.on() else self.TRIAGE_SCHEMA,
            feature=self.BATCH_FEATURE, model=name, effort=self.TRIAGE_EFFORT))

    def batch_submit(self, at_least: int | None = None, keep: int | None = None, log: Log = lambda _: None) -> int:
        """When a backlog from the last RECENT_DAYS is waiting and no batch is out, send it as one, and return how many it
        took. It takes what fits in its share of today's limit, by estimate, and leaves the newest to go now."""
        at_least, keep = self.BATCH_MIN if at_least is None else at_least, self.BATCH_KEEP_NEWEST if keep is None else keep
        gw, name = self._gateway(), self._calls.background_label()
        if self._calls.engine() == "none" or not gw.takes_batches or gw.held_back(self.BATCH_FEATURE, name):
            return 0
        with self._uow.read() as c:
            if self._answers.batch_open(c):
                return 0
            rows = [r for r in self._answers.needs_summary(c, keep + self.BATCH_MAX, newer_than=self._recent())
                    if r["message_key"] is not None]
        if len(rows) < at_least:
            return 0
        room = gw.room()                    # None: no governor, so no limit to keep within
        requests: list[tuple[str, str]] = []
        keys: list[MessageKey] = []
        estimate, cutoff = 0, 0
        for r in rows[keep:]:
            text = self._triage_prompt(r)
            cost = pricing.batch_estimate(name, len(prompts.SYSTEM) + len(text))
            if room is not None and estimate + cost > room // self.BATCH_SHARE:
                break
            requests.append((str(r["message_key"]), text))
            keys.append(r["message_key"])
            estimate = min(estimate + cost, pricing.MOST)
            cutoff = max(cutoff, r["ts"] or 0)
        if not requests:
            return 0
        with self._uow.write() as c:                # committed before the network: sending it is a call
            batch_id = self._answers.batch_begin(c, self.BATCH_FEATURE, name, keys, estimate, cutoff, int(time.time()),
                                                 prompts.TRIAGE.version)
        if batch_id is None:
            return 0
        try:
            remote_id = self._send_batch(requests, name)
        except Exception as e:                      # it didn't go, so they wait; the next round tries again
            with self._uow.write() as c:
                self._answers.batch_drop(c, batch_id)
            log(f"a batch of {len(requests)} wasn't sent: {type(e).__name__}")
            return 0
        with self._uow.write() as c:
            self._answers.batch_sent(c, batch_id, remote_id, int(time.time()))
        log(f"sent a batch of {len(requests)}, at most about {pricing.usd(estimate)}")
        return len(requests)

    def _store_answers(self, c: Connection, batch: Row, results: Iterable[BatchResult]) -> int:
        """Each answer a batch brought, for each copy of its message and under its key, labelled with the model the batch
        asked, whatever is in force now. How many."""
        keys, stored, when = self._answers.batch_keys(c, batch["id"]), 0, datetime.now(timezone.utc).isoformat()
        for r in results:
            key = MessageKey(int(r.custom_id)) if r.custom_id.isdigit() else None
            if key is None or key not in keys or r.reply is None:
                continue                            # no answer: it is waiting again once the batch is closed
            row = self._mail.any_copy(c, key)
            said = self._assessment(row or {"subject": ""}, r.reply, batch["prompt_version"], batch["model"])
            if row is not None:
                self._save(c, row["pk"], said, when)
            elif said.outcome == ASSESSED:          # every copy has gone: it may come back
                self._answers.save_assessment(c, key, said.summary, said.importance, said.category, said.action, said.model,
                                              when, said.served_by, said.prompt_version, said.outcome)
            stored += 1
        return stored

    def _let_go(self, log: Log) -> None:
        """Another provider than Claude is chosen, so a batch out (being sent, or sent) is dropped here, and nothing is sent to
        the API: its messages wait for a summary again, from the provider chosen, and what it booked no longer counts."""
        with self._uow.read() as c:
            out = [b["id"] for state in ("submitting", "running") for b in self._answers.batches_in_state(c, state)]
        if not out:
            return
        with self._uow.write() as c:
            for batch_id in out:
                self._answers.batch_drop(c, batch_id)
        log("another provider than Claude is chosen, so the batch out was let go; its messages are waiting again")

    def batch_collect(self, log: Log = lambda _: None) -> int:
        """Bring in the answers of a batch that has ended, and return how many it stored. Nothing here sends
        a message, so neither the limit nor someone using AI holds it back: these answers are paid for. With another
        provider than Claude chosen, a batch out is let go instead; with Claude chosen and AI mode off, it waits, and is
        brought in once AI mode is on again."""
        gw, now, stored = self._gateway(), int(time.time()), 0
        if self._provider() != "anthropic":
            self._let_go(log)
            return 0
        if self._calls.engine() == "none" or not gw.takes_batches:
            return 0
        try:
            with self._uow.read() as c:             # most rounds find nothing to do, and take no lock for it
                cut_off = [b for b in self._answers.batches_in_state(c, "submitting")
                           if b["created_at"] < now - self.BATCH_SEND_GIVE_UP]
            if cut_off:
                with self._uow.write() as c:
                    for b in cut_off:
                        self._answers.batch_drop(c, b["id"])
                        log("a batch was cut off while it was being sent; its messages are waiting again")
            with self._uow.read() as c:
                out = self._answers.batch_open(c)
            if out is not None and out["state"] == "running" and out["polled_at"] + self.BATCH_POLL <= now:
                try:
                    ended = gw.batch_ended(out["remote_id"])
                    results = gw.batch_results(out["remote_id"], feature=out["feature"], model=out["model"]) if ended else None
                except NotSent:
                    raise
                except ModelError as e:
                    ended, results = False, None
                    with self._uow.write() as c:
                        if e.status == 404:         # another key's, or removed: nothing more will come of it
                            self._answers.batch_drop(c, out["id"])
                            log("the API no longer has a batch; its messages are waiting again")
                        else:
                            self._answers.batch_polled(c, out["id"], now)
                            log(f"couldn't read a batch: {type(e).__name__} {e.status}")
                if results is not None:
                    with self._uow.write() as c:
                        stored = self._store_answers(c, out, results)
                        self._answers.batch_collected(c, out["id"], now)
                        log(f"a batch brought {stored} answers")
                elif not ended:
                    with self._uow.write() as c:
                        self._answers.batch_polled(c, out["id"], now)
            with self._uow.read() as c:
                collected = self._answers.batches_in_state(c, "collected")
            for b in collected:
                try:                                # what it holds is mail: it stays at the API no longer than needed
                    gw.discard_batch(b["remote_id"])
                except NotSent:
                    raise
                except ModelError as e:
                    if e.status != 404:
                        log(f"couldn't remove a batch: {type(e).__name__} {e.status}")
                        continue
                with self._uow.write() as c:
                    self._answers.batch_discarded(c, b["id"])
        except NotSent:
            pass
        return stored

    def summaries_waiting(self) -> int:
        """How many messages from the last RECENT_DAYS wait for a summary, those out in a batch among them."""
        with self._uow.read() as c:
            return self._answers.summaries_waiting(c, self._recent())

    def batch_status(self) -> BatchStatus:
        """What's out in a batch, with no call made: how many messages, and what it may cost."""
        with self._uow.read() as c:
            out = self._answers.batch_open(c)
            return {"waiting": self._answers.batch_waiting(c), "state": out["state"] if out else None,
                    "estimate_micros": out["estimate_micros"] if out else 0}

    def round(self) -> int | None:
        """The assess job's round: triage up to ASSESS_BATCH messages that have no summary yet. It runs
        on its own lane, so a slow model never holds up a sync, and with AI off it tries nothing. A full
        batch means more are waiting, so the next round follows at once. While the user is using AI the
        round waits until it has yielded long enough; held back otherwise (the limit, the breaker), it
        waits its interval. A batch's answers are brought in first, whatever holds the rest back, and a
        backlog goes out in the next batch rather than through this round's calls.

        Summaries change what needs you and what can wait with no mail changing, so a round that kept any tells the
        open tabs how many (`summarized`), a batch's answers among them, even when what follows them holds the round
        back or fails: the lists, the Inbox's tabs, the rail and the brief read them again."""
        landed = [0]
        try:
            return self._round(landed)
        finally:
            if landed[0]:
                sse.publish("summarized", {"count": landed[0]})

    def _round(self, landed: list[int]) -> int | None:
        """The round, adding to `landed` each summary it keeps as it keeps it."""
        def say(m: str) -> None:                       # the app's own words, and counts and an error's class
            assess_log.info("%(what)s", {"what": m})

        landed[0] += self.batch_collect(say)
        why = self._gateway().held_back("triage", self._calls.background_label())
        if why:
            return self.ASSESS_YIELD if why == "yielding" else None
        if not environment.NO_BATCHES.on():
            self.batch_submit(log=say)
        done = self.summarize_pending(limit=self.ASSESS_BATCH, workers=self.WORKERS)
        landed[0] += done
        if done:
            assess_log.info("%d summarized", done)
        return 0 if done >= self.ASSESS_BATCH else None

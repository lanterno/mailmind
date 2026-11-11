"""Fakes behind the Assistant's ports, for a use case a unit test builds by hand: a LanguageModel that answers from a queue
and keeps each Prompt it was sent, a UsageLedger in memory, a unit of work with no database, and `Triage`, the stored mail
and the Answers port's triage half as the SQLite adapter keeps them. `calls_on` makes the Calls every use case asks
through, over the real Gateway (and a CostGovernor, when a test hands one), so what reaches the model and the feature each
call is counted under are as the app's."""
import contextlib
import itertools
import json
import time

from mailmind.assistant.application.assessment import Assessments
from mailmind.assistant.application.calls import Calls
from mailmind.assistant.application.gateway import Gateway
from mailmind.assistant.application.ports import AIOff, BatchResult, ModelError, Reply, Usage
from mailmind.assistant.application.status import Status
from mailmind.kernel.assessment import Assessment


def reply_of(said, model):
    """What a model answers for `said`: a Reply as it is, a str as its text, anything else as its JSON. An exception is
    raised."""
    if isinstance(said, BaseException):
        raise said
    if isinstance(said, Reply):
        return said
    return Reply(said if isinstance(said, str) else json.dumps(said), model, Usage(), "end_turn")


class Model:
    """A LanguageModel. Each reply a test queued answers one call in turn, as `reply_of` reads it; with none queued, an
    empty object. A batch is answered once a test ends it, each request as `end_batch` says or else with an empty object.
    An exception in `failing`, by the method's name, is raised by that method until it is taken out. With `enabled` False
    it is the stand-in for AI off, which raises AIOff for anything asked of it, and notes what that was."""

    def __init__(self, *replies, enabled=True):
        self.enabled = enabled
        self.replies = list(replies)
        self.sent = []
        self.batches = {}          # its id -> {"requests": [(custom id, Prompt)], "ended": bool, "results": {custom id: said}}
        self.asked = []            # each batch method called, by name
        self.discarded = []
        self.failing = {}

    def answer(self, *replies):
        self.replies.extend(replies)

    @property
    def prompt(self):
        """The text of the one prompt sent."""
        [sent] = self.sent
        return sent.text

    def complete(self, prompt):
        self.sent.append(prompt)
        if not self.enabled:
            raise AIOff("AI is off")
        return reply_of(self.replies.pop(0) if self.replies else {}, prompt.model)

    def _ask(self, name, batch_id=None):
        self.asked.append(name)
        if not self.enabled:
            raise AIOff("AI is off")
        if name in self.failing:
            raise self.failing[name]
        if batch_id is not None and batch_id not in self.batches:
            raise ModelError("no such batch", 404)

    def submit_batch(self, requests):
        self._ask("submit_batch")
        batch_id = f"msgbatch_{len(self.batches) + 1}"
        self.batches[batch_id] = {"requests": list(requests), "ended": False, "results": {}}
        return batch_id

    @property
    def batched(self):
        """The custom ids of every request sent in a batch."""
        return [cid for b in self.batches.values() for cid, _ in b["requests"]]

    def end_batch(self, results=None, batch_id=None):
        """The batch (the last one sent unless named) has ended: each custom id in `results` answered as `reply_of` reads
        it, None for no answer, "permanent" for no answer that sending again would change."""
        self.batches[batch_id or list(self.batches)[-1]].update(ended=True, results=results or {})

    def batch_ended(self, batch_id):
        self._ask("batch_ended", batch_id)
        return self.batches[batch_id]["ended"]

    def batch_results(self, batch_id, asked):
        self._ask("batch_results", batch_id)
        batch, out = self.batches[batch_id], []
        for cid, prompt in batch["requests"]:
            said = batch["results"].get(cid, {})
            out.append(BatchResult(cid, None, said == "permanent") if said in (None, "permanent")
                       else BatchResult(cid, reply_of(said, prompt.model)))
        return out

    def discard_batch(self, batch_id):
        self._ask("discard_batch", batch_id)
        del self.batches[batch_id]
        self.discarded.append(batch_id)


class Ledger:
    """A UsageLedger in memory: each Spend it was told of, and what batches out have booked."""

    def __init__(self):
        self.spends = []
        self.booked = 0

    @property
    def features(self):
        return [s.feature for s in self.spends]

    def record(self, spend):
        self.spends.append(spend)

    def spent(self, day):
        return sum(s.cost_micros for s in self.spends if s.day == day)

    def reserved(self):
        return self.booked

    def days(self, since):
        return []


class Uow:
    """A unit of work with no database: a block's connection is a stand-in no fake reads. It counts the write blocks."""

    def __init__(self):
        self.writes = 0

    @contextlib.contextmanager
    def read(self):
        yield None

    @contextlib.contextmanager
    def write(self):
        self.writes += 1
        yield None


_OUT = ("submitting", "running")


class Triage:
    """The stored mail as triage reads it (the Mail port's `any_copy`) and the Answers port's triage half, in memory and as
    the SQLite adapter keeps them: what was said of a message is kept under its key, a message waits for triage until then
    unless a batch out holds it, one batch is out at a time, and dropping a batch or bringing its answers in lets its
    messages go."""

    def __init__(self, rows=()):
        self.rows = {r["pk"]: dict(r) for r in rows}
        self.said = {}             # message key -> Assessment
        self.batches = {}          # id -> the batch's row
        self.items = {}            # message key -> the id of the batch that holds it
        self._ids = itertools.count(1)

    def add(self, row):
        self.rows[row["pk"]] = dict(row)

    # Mail
    def any_copy(self, c, key):
        return next((r for r in self.rows.values() if r["message_key"] == key), None)

    # Answers
    def needs_summary(self, c, limit=200, newer_than=None):
        floor, seen, out = -1 if newer_than is None else newer_than, set(), []
        for r in sorted(self.rows.values(), key=lambda r: -(r["ts"] or 0)):
            key = r["message_key"]
            if key is None or key in self.said or key in self.items or key in seen or (r["ts"] or 0) <= floor:
                continue
            seen.add(key)
            out.append(r)
        return out[:limit]

    def save_summary(self, c, pk, summary, importance, category, action, model, created_at, served_by=None,
                     prompt_version=None, outcome=None):
        key = self.rows[pk]["message_key"] if pk in self.rows else None
        if key is not None:
            self.save_assessment(c, key, summary, importance, category, action, model, created_at, served_by,
                                 prompt_version, outcome)

    def save_assessment(self, c, key, summary, importance, category, action, model, created_at, served_by=None,
                        prompt_version=None, outcome=None):
        self.said[key] = Assessment(summary, importance, category, action, model, served_by, prompt_version, outcome)

    def assessment(self, c, key):
        return self.said.get(key)

    def batch_open(self, c):
        out = [b for b in self.batches.values() if b["state"] in _OUT]
        return out[-1] if out else None

    def batch_begin(self, c, feature, model, keys, estimate_micros, cutoff_ts, now, prompt_version=None):
        if self.batch_open(c):
            return None
        batch_id = next(self._ids)
        self.batches[batch_id] = {"id": batch_id, "remote_id": None, "feature": feature, "model": model, "state": "submitting",
                                  "requests": len(keys), "estimate_micros": estimate_micros, "cutoff_ts": cutoff_ts,
                                  "created_at": now, "polled_at": 0, "ended_at": None, "prompt_version": prompt_version}
        self.items.update((k, batch_id) for k in keys)
        return batch_id

    def _update(self, batch_id, **columns):
        """An UPDATE: a batch dropped meanwhile is no row to change."""
        if batch_id in self.batches:
            self.batches[batch_id].update(columns)

    def _release(self, batch_id):
        self.items = {k: b for k, b in self.items.items() if b != batch_id}

    def batch_sent(self, c, batch_id, remote_id, now):
        self._update(batch_id, state="running", remote_id=remote_id, polled_at=now)

    def batch_drop(self, c, batch_id):
        self.batches.pop(batch_id, None)
        self._release(batch_id)

    def batch_polled(self, c, batch_id, now):
        self._update(batch_id, polled_at=now)

    def batch_keys(self, c, batch_id):
        return {k for k, b in self.items.items() if b == batch_id}

    def batch_collected(self, c, batch_id, now):
        self._release(batch_id)
        self._update(batch_id, state="collected", ended_at=now)

    def batch_discarded(self, c, batch_id):
        self._update(batch_id, state="done")

    def batches_in_state(self, c, state):
        return [b for _, b in sorted(self.batches.items()) if b["state"] == state]

    def batch_waiting(self, c):
        return len(self.items)


class Said:
    """The Answers port's briefs, deltas and reviews in memory, each kept under what the SQLite adapter keys it by, and the
    mail still waiting for triage (`waiting`, rows with their `ts`), which is all a brief written ahead asks of it."""

    def __init__(self):
        self.briefs, self.deltas, self.reviews, self.waiting = {}, {}, {}, []

    def needs_summary(self, c, limit=200, newer_than=None):
        return [r for r in self.waiting if r["ts"] > (-1 if newer_than is None else newer_than)][:limit]

    def get_brief(self, c, accounts, day):
        return self.briefs.get((accounts, day))

    def save_brief(self, c, accounts, day, fingerprint, narrative, groups_json, model, created_at):
        self.briefs[accounts, day] = {"accounts": accounts, "day": day, "fingerprint": fingerprint, "narrative": narrative,
                                      "groups_json": groups_json, "model": model, "created_at": created_at}

    def get_delta(self, c, accounts, since_ts, fingerprint):
        return self.deltas.get((accounts, since_ts, fingerprint))

    def save_delta(self, c, accounts, since_ts, fingerprint, headline, items_json, model, created_at):
        self.deltas[accounts, since_ts, fingerprint] = {"headline": headline, "items_json": items_json, "model": model,
                                                        "created_at": created_at}

    def get_review(self, c, accounts, day):
        return self.reviews.get((accounts, day))

    def save_review(self, c, accounts, day, fingerprint, narrative, next_json, stats_json, needs_json, waiting_json, model,
                    created_at):
        self.reviews[accounts, day] = {"fingerprint": fingerprint, "narrative": narrative, "next_json": next_json,
                                       "stats_json": stats_json, "needs_json": needs_json, "waiting_json": waiting_json,
                                       "model": model, "created_at": created_at}


def gateway_on(model, ledger=None, governor=None, clock=time.time):
    """The real Gateway, over `model` and `ledger`."""
    return Gateway(lambda: model, Ledger() if ledger is None else ledger, clock=clock, governor=governor)


def calls_over(gateway):
    """The Calls a use case asks through, over `gateway`."""
    status = Status(lambda: gateway, lambda: ("", ""), lambda: "", lambda: None, lambda: {"waiting": 0, "state": None,
                                                                                            "estimate_micros": 0},
                    lambda: "")
    return Calls(lambda: gateway, status)


def calls_on(model, ledger=None, governor=None, clock=time.time):
    """The Calls a use case asks through, over `model` and `ledger` by the real Gateway."""
    return calls_over(gateway_on(model, ledger, governor, clock))


def message(n, **over):
    """Message n as triage reads it: a later n is newer mail."""
    return {"pk": n, "message_key": 1000 + n, "ts": 1_800_000_000 + 60 * n, "from_name": f"Sender {n}",
            "from_addr": f"s{n}@example.test", "subject": f"Message {n}", "date_iso": "2026-11-02T09:00:00+00:00",
            "snippet": f"What message {n} says.", **over}


def triage_on(model, triage, ledger=None, governor=None):
    """Assessments over `model` and `triage`, the stored mail and what was said of it."""
    gateway = gateway_on(model, ledger, governor)
    return Assessments(calls_over(gateway), lambda: gateway, triage, Uow(), triage)

"""The Weekly Review — a retrospective companion to the daily briefing. Aggregates the last 7 days
from SQLite (cheap columns only, NO body/IMAP fetch): what you sent, what came in, who's waiting on
YOU (open action mail) vs who you're waiting on (sent-no-reply follow-ups), your reply-later stack,
and what you cleaned up. One schema-constrained Claude call turns the numbers + the top open
threads into a short prose retrospective plus up to 3 "start next week with" suggestions. Stored in
`reviews` by (account set + local date), so the cost is paid once a day and the lists are
deterministic SQL (only the prose + the picks are AI). Row text is fed as UNTRUSTED DATA.
"""
from collections.abc import Collection, Iterable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
import json
from typing import Any

from mailmind.assistant.application.calls import Calls
from mailmind.assistant.application.ports import Answers, Mail, NotSent, Result
from mailmind.assistant.domain import prompts, review
from mailmind.assistant.domain.review import Awaited, Meeting, Needed, Retrospective, Stats
from mailmind.kernel.errors import Unreachable
from mailmind.kernel.row import Connection, Row
from mailmind.platform.db.uow import UnitOfWork
from mailmind.platform.logging import get_logger
from mailmind.platform.workers.singleflight import SingleFlight

log = get_logger("ai")
WEEK_SECONDS = 7 * 86400
TOP_OPEN = 8                            # cap the deterministic open/waiting lists
CAP_TOTAL = 9000
REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "narrative": {"type": "string"},
        "next": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["narrative", "next"],
    "additionalProperties": False,
}


class Reviews:
    def __init__(self, calls: Calls, answers: Answers, uow: UnitOfWork, mail: Mail):
        self._calls = calls
        self._answers = answers
        self._uow = uow
        self._mail = mail
        self._FLIGHTS = SingleFlight()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _aggregate(self, c: Connection, accounts_set: Collection[str],
                   since_ts: float) -> tuple[Stats, list[Needed], list[Awaited]]:
        """All deterministic — pure local SQL. Returns (stats dict, needs list, waiting list)."""
        sent = self._mail.sent_since(c, accounts_set, since_ts)
        received = self._mail.received_since(c, accounts_set, since_ts)
        # received breakdown by bucket (what kind of mail arrived)
        by_bucket: dict[str, int] = {}
        for r in self._mail.received_by_bucket(c, accounts_set, since_ts):
            by_bucket[r["bucket"] or "other"] = r["n"]

        needs_rows = self._mail.needs_you_rows(c, accounts_set, TOP_OPEN)
        needs: list[Needed] = [{"key": r["message_key"], "folder": r["folder"], "uid": r["uid"],
                                "account": r["account"], "thread_id": r["thread_id"],
                                "who": r["from_name"] or r["from_addr"] or "someone",
                                "subject": r["subject"] or "(no subject)",
                                "importance": r["importance"], "action": r["action"] or ""} for r in needs_rows]

        waiting_rows = self._mail.awaited_threads(c, accounts_set, TOP_OPEN)
        waiting: list[Awaited] = [{"account": r["account"], "thread_id": r["thread_id"],
                                   "key": r["message_key"], "folder": r["folder"], "uid": r["uid"],
                                   "subject": r["subject"] or "(no subject)",
                                   "to": (r["recipients"] or "").split(",")[0].strip() if r["recipients"] else "",
                                   "sent_ts": r["first_sent_ts"]} for r in waiting_rows]

        replylater = self._mail.replylater_due(c, accounts_set)

        since_iso = datetime.fromtimestamp(since_ts, timezone.utc).isoformat()
        try:
            cleaned = self._mail.senders_cleaned(c, since_iso, accounts_set)
        except Exception:
            cleaned = 0

        handled = self._mail.handled_since(c, accounts_set, since_iso)
        stats: Stats = {"sent": sent, "received": received, "by_bucket": by_bucket,
                        "needs_you": len(needs), "waiting_on": len(waiting), "reply_later": replylater,
                        "cleaned": cleaned, "handled": handled}
        return stats, needs, waiting

    def _meetings(self, since_ts: float) -> list[Meeting]:
        """The week's meetings and deadlines, from the invites in the mail: what it read is what the prose and the list say. A
        calendar that can't be read costs the review only its mention of them."""
        now = datetime.now()
        since = datetime.fromtimestamp(since_ts)
        try:
            gone = self._mail.events_from(since, review.EVENTS_READ)
            coming = self._mail.events_from(now, review.EVENTS_READ)
        except Exception as e:
            log.warning("the week's meetings can't be read", exc_info=e)
            return []
        return review.meetings_of(gone, coming, since, now, now + timedelta(days=review.AHEAD_DAYS))

    def _items_now(self, c: Connection, items: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """A stored list, as the page reads it: each with the folder and uid of its message's copy now. One whose message has
        gone is left out, and one that named no message (a follow-up whose thread has no row) stays as it was. A list stored
        before named each by its pk too, which says nothing after a renumber, and that goes."""
        now: list[dict[str, Any]] = []
        for item in items:
            kept = {name: value for name, value in item.items() if name != "pk"}
            if not item["key"]:
                now.append({"uid": None, **kept})
            elif (there := self._mail.copy_now(c, item["key"], item["folder"])) is not None:
                now.append({**kept, "folder": there["folder"], "uid": there["uid"]})
        return now

    def _result(self, stats: Stats, needs: Sequence[Mapping[str, Any]], waiting: Sequence[Mapping[str, Any]],
                meetings: Sequence[Mapping[str, Any]], ai: Retrospective, model: str, created_at: str, cached: bool, stale: bool,
                since_ts: float) -> dict[str, Any]:
        return {"ok": True, "narrative": ai["narrative"], "next": ai["next"],
                "stats": stats, "needs": needs, "waiting": waiting, "meetings": meetings,
                "week_start": datetime.fromtimestamp(since_ts, timezone.utc).isoformat(),
                "model": model, "generated": created_at, "cached": cached, "stale": stale}

    def build(self, accounts_set: Iterable[str] | None, force: bool = False, generate: bool = True) -> Result:
        """With generate=False it only reads: nothing stored yet ->
        {missing:true}, and no AI call."""
        accounts_set = set(accounts_set or [])
        import time
        since_ts = time.time() - WEEK_SECONDS
        accounts, day = ",".join(sorted(accounts_set)), datetime.now().astimezone().date().isoformat()
        with self._uow.read() as c:
            stats, needs, waiting = self._aggregate(c, accounts_set, since_ts)
            cached_row = self._answers.get_review(c, accounts, day)
        fp = review.fingerprint_of(stats, needs, waiting)

        def stored(row: Row) -> Result:
            said = json.loads(row["stats_json"])
            said["handled"] = stats["handled"]      # an Undo changes it: as it stands, not as it was written
            with self._uow.read() as c:
                stored_needs = self._items_now(c, json.loads(row["needs_json"]))
                stored_waiting = self._items_now(c, json.loads(row["waiting_json"]))
                meetings = self._items_now(c, said.pop("meetings", []))     # kept beside its numbers, for what they were made from
            return self._result(said, stored_needs, stored_waiting, meetings,
                           {"narrative": row["narrative"], "next": json.loads(row["next_json"])},
                           row["model"], row["created_at"], True, row["fingerprint"] != fp, since_ts)

        if cached_row and not force:
            return stored(cached_row)
        if not generate:
            return {"ok": True, "missing": True}

        def read() -> Result | None:
            with self._uow.read() as c:
                row = self._answers.get_review(c, accounts, day)
            return stored(row) if row else None

        def make() -> Result:
            bl = ", ".join(f"{k}: {v}" for k, v in sorted(stats["by_bucket"].items(), key=lambda kv: -kv[1])[:5])
            stats_txt = (f"Sent: {stats['sent']}\nReceived in inbox: {stats['received']} ({bl})\n"
                         f"Still need your reply: {stats['needs_you']}\n"
                         f"Waiting to hear back: {stats['waiting_on']}\n"
                         f"Pinned to reply-later: {stats['reply_later']}\n"
                         f"Senders cleaned up: {stats['cleaned']}\n"
                         f"Filed away: {stats['handled']}")
            meetings = self._meetings(since_ts)
            needs_txt = "\n".join(
                f"- [{n['importance']}] {n['who']} — {n['subject']}" + (f" — {n['action'][:120]}" if n["action"] else "")
                for n in needs) or "(nothing)"
            waiting_txt = "\n".join(f"- {w['subject']} → {w['to']}" for w in waiting) or "(nothing)"
            prompt = prompts.REVIEW.fill(stats=stats_txt, meetings=review.meeting_lines(meetings), needs=needs_txt[:CAP_TOTAL], waiting=waiting_txt[:2000])
            try:
                obj = self._calls.call_claude_json(prompt, REVIEW_SCHEMA, feature="review")
                ai: Retrospective = review.clean(obj)
                model = self._calls.model_label()
                if review.degenerate(ai["narrative"]):     # the deterministic narrative is still useful
                    fb = review.fallback(stats, needs, waiting)
                    ai = {"narrative": fb["narrative"], "next": ai["next"] or fb["next"]}
                    model = "fallback"
            except NotSent:                     # AI off, or over the daily limit: the route answers it
                raise
            except Exception as e:
                log.warning("review failed", exc_info=e)
                raise Unreachable("review unavailable", retry=True)

            created = self._now()
            with self._uow.write() as c:
                self._answers.save_review(c, accounts, day, fp, ai["narrative"], json.dumps(ai["next"]), json.dumps({**stats, "meetings": meetings}),
                                  json.dumps(needs), json.dumps(waiting), model, created)
            return self._result(stats, needs, waiting, meetings, ai, model, created, False, False, since_ts)

        answer = self._FLIGHTS.run((accounts, day), make, read, wait=60, recheck=not force)   # a first load may be making it
        if answer is None:
            raise Unreachable("review unavailable", retry=True)
        return answer

"""The Weekly Review — a retrospective companion to the daily briefing. Aggregates the last 7 days
from SQLite (cheap columns only, NO body/IMAP fetch): what you sent, what came in, who's waiting on
YOU (open action mail) vs who you're waiting on (sent-no-reply follow-ups), your reply-later stack,
and what you cleaned up. One schema-constrained Claude call turns the numbers + the top open
threads into a short prose retrospective plus up to 3 "start next week with" suggestions. Stored in
`reviews` by (account set + local date), so the cost is paid once a day and the lists are
deterministic SQL (only the prose + the picks are AI). Row text is fed as UNTRUSTED DATA.
"""
from collections.abc import Collection, Iterable, Mapping, Sequence
from datetime import datetime, timezone
import json
from typing import Any

from mailmind.assistant.application.calls import Calls
from mailmind.assistant.application.ports import Answers, Mail, NotSent, Result
from mailmind.assistant.domain import prompts, review
from mailmind.assistant.domain.review import Awaited, Needed, Retrospective, Stats
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
        needs: list[Needed] = [{"pk": r["pk"], "key": r["message_key"], "folder": r["folder"],
                                "account": r["account"], "thread_id": r["thread_id"],
                                "who": r["from_name"] or r["from_addr"] or "someone",
                                "subject": r["subject"] or "(no subject)",
                                "importance": r["importance"], "action": r["action"] or ""} for r in needs_rows]

        waiting_rows = self._mail.awaited_threads(c, accounts_set, TOP_OPEN)
        waiting: list[Awaited] = [{"account": r["account"], "thread_id": r["thread_id"], "pk": r["pk"],
                                   "key": r["message_key"], "folder": r["folder"],
                                   "subject": r["subject"] or "(no subject)",
                                   "to": (r["recipients"] or "").split(",")[0].strip() if r["recipients"] else "",
                                   "sent_ts": r["first_sent_ts"]} for r in waiting_rows]

        replylater = self._mail.replylater_due(c, accounts_set)

        since_iso = datetime.fromtimestamp(since_ts, timezone.utc).isoformat()
        try:
            cleaned = self._mail.senders_cleaned(c, since_iso, accounts_set)
        except Exception:
            cleaned = 0

        stats: Stats = {"sent": sent, "received": received, "by_bucket": by_bucket,
                        "needs_you": len(needs), "waiting_on": len(waiting), "reply_later": replylater,
                        "cleaned": cleaned}
        return stats, needs, waiting

    def _items_now(self, c: Connection, items: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """A stored list, as the page reads it: each with the pk its message has now. The pk it had says
        nothing after a renumber. One whose message has gone is left out, and one that named no message
        (a follow-up whose thread has no row) stays as it was."""
        now: list[dict[str, Any]] = []
        for item in items:
            pk = self._mail.pk_now(c, item["key"], item["folder"]) if item["key"] else None
            if pk is not None or not item["key"]:
                now.append({**item, "pk": pk})
        return now

    def _result(self, stats: Stats, needs: Sequence[Mapping[str, Any]], waiting: Sequence[Mapping[str, Any]],
                ai: Retrospective, model: str, created_at: str, cached: bool, stale: bool,
                since_ts: float) -> dict[str, Any]:
        return {"ok": True, "narrative": ai["narrative"], "next": ai["next"],
                "stats": stats, "needs": needs, "waiting": waiting,
                "week_start": datetime.fromtimestamp(since_ts, timezone.utc).isoformat(),
                "model": model, "generated": created_at, "cached": cached, "stale": stale}

    def build(self, accounts_set: Iterable[str] | None, force: bool = False, generate: bool = True) -> Result:
        """-> (result_dict, http_code). With generate=False it only reads: nothing stored yet ->
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
            with self._uow.read() as c:
                stored_needs = self._items_now(c, json.loads(row["needs_json"]))
                stored_waiting = self._items_now(c, json.loads(row["waiting_json"]))
            return self._result(json.loads(row["stats_json"]), stored_needs, stored_waiting,
                           {"narrative": row["narrative"], "next": json.loads(row["next_json"])},
                           row["model"], row["created_at"], True, row["fingerprint"] != fp, since_ts), 200

        if cached_row and not force:
            return stored(cached_row)
        if not generate:
            return {"ok": True, "missing": True}, 200

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
                         f"Senders cleaned up: {stats['cleaned']}")
            needs_txt = "\n".join(
                f"- [{n['importance']}] {n['who']} — {n['subject']}" + (f" — {n['action'][:120]}" if n["action"] else "")
                for n in needs) or "(nothing)"
            waiting_txt = "\n".join(f"- {w['subject']} → {w['to']}" for w in waiting) or "(nothing)"
            prompt = prompts.REVIEW.fill(stats=stats_txt, needs=needs_txt[:CAP_TOTAL], waiting=waiting_txt[:2000])
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
                return {"ok": False, "error": "review unavailable", "retry": True}, 502

            created = self._now()
            with self._uow.write() as c:
                self._answers.save_review(c, accounts, day, fp, ai["narrative"], json.dumps(ai["next"]), json.dumps(stats),
                                  json.dumps(needs), json.dumps(waiting), model, created)
            return self._result(stats, needs, waiting, ai, model, created, False, False, since_ts), 200

        return (self._FLIGHTS.run((accounts, day), make, read, wait=60, recheck=not force)   # a first load may be making it
                or ({"ok": False, "error": "review unavailable", "retry": True}, 502))

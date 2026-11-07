"""Threads waiting on someone: follow-ups, nudges and the Reply-Later stack."""
import time
from typing import Any

from mailmind.attention.application.ports import Accounts, AttentionBook
from mailmind.attention.application.snoozing import Snoozing
from mailmind.attention.application.threads import Threads
from mailmind.attention.domain import followups
from mailmind.attention.domain.events import FollowUpDismissed, FollowUpsChanged, ReplyLaterChanged
from mailmind.kernel.when import now_iso
from mailmind.platform import bus
from mailmind.platform.db.uow import UnitOfWork
from mailmind.platform.logging import get_logger

snooze_log = get_logger("snooze")
followup_log = get_logger("followup")


class FollowUps:
    def __init__(self, uow: UnitOfWork, book: AttentionBook, threads: Threads, accounts: Accounts):
        self._uow = uow
        self._book = book
        self._threads = threads
        self._accounts = accounts

    def tick(self) -> None:
        """Resolve met commitments, expire stale ones, then promote/detect Waiting threads. Pure-local SQL (no IMAP),
        so it rides the always-on 60s loop and works read-only, as the snooze does."""
        now = int(time.time())
        with self._uow.write() as c:
            before = self._book.due_followup_keys(c)
            for acct in set(self._accounts.emails()):
                self._book.resolve_followups(c, acct)     # resolve FIRST (answered flips clear before re-detect)
            self._book.expire_followups(c, now, followups.EXPIRE_DAYS)
            self._book.followup_promote_due(c, now, followups.GRACE_DAYS)
            self._book.followup_detect_received(c, now, followups.GRACE_DAYS, followups.EXPIRE_DAYS)
            after = self._book.due_followup_keys(c)
        if after != before:                               # set comparison catches swaps, not just count deltas
            bus.publish(FollowUpsChanged(len(after)))

    def dismiss(self, body: dict[str, Any]) -> tuple[dict[str, Any], int]:
        """(result, code)."""
        with self._uow.write() as c:
            acct, tid = self._threads.target(c, body)
            if not acct or not tid:
                return {"ok": False, "error": "bad target"}, 400
            self._book.dismiss_followup(c, acct, tid, now_iso())
        bus.publish(FollowUpDismissed(acct, tid))
        return {"ok": True, "thread_id": tid}, 200

    def reply_later(self, body: dict[str, Any]) -> tuple[dict[str, Any], int]:
        """Pin/un-pin a thread to the Reply-Later stack. on=true pins (supersedes auto-nudges), on=false clears.
        (result, code)."""
        with self._uow.write() as c:
            acct, tid = self._threads.target(c, body)
            if not acct or not tid:
                return {"ok": False, "error": "bad target"}, 400
            on = body.get("on", True)
            if on:
                anchor = int(body["pk"]) if body.get("pk") is not None else None
                self._book.set_replylater(c, acct, tid, anchor, now_iso())
            else:
                self._book.clear_replylater(c, acct, tid)
        bus.publish(ReplyLaterChanged(acct, tid))
        return {"ok": True, "thread_id": tid, "on": bool(on)}, 200


class Clocked:
    """The job that runs every minute. Database only, so it runs read-only too."""
    def __init__(self, snoozing: Snoozing, follow_ups: FollowUps):
        self._snoozing = snoozing
        self._follow_ups = follow_ups

    def round(self) -> None:
        """Wake due snoozes, then bring the follow-ups up to date; one step failing doesn't stop the other."""
        try:
            self._snoozing.wake_due()
        except Exception as e:
            snooze_log.warning("tick error", exc_info=e)
        try:
            self._follow_ups.tick()
        except Exception as e:
            followup_log.warning("tick error", exc_info=e)

"""Durable outbound queue + a single drain worker for MailMind.

A send is enqueued (state=queued) by the server after build_message has already
validated it; the worker claims it (queued->sending atomically), delivers over SMTP, holding
no IMAP gate, then does the best-effort post-send side effects (APPEND to Sent for non-Gmail,
set \\Answered on the original for replies — each guarded by a done-once flag, and each a job
on its account's IMAP gate),
and marks it sent. The queue is a DurableQueue over the outbox table: failures back off
exponentially and retry; after MAX_ATTEMPTS the row is 'error' (terminal until an explicit
retry). Exactly-once delivery is enforced by a persisted `delivered` flag, the queue's gate,
set the instant the wire send returns: a crash/DB-failure after delivery requeues the row, but
send_one() then skips the send and only finishes bookkeeping.
A step of those that fails puts the delivered row back in the queue, with the same back-off and
attempts counted afresh, so it's done once the server is back; the gate keeps the retry from
sending again. Listed, such a row is `sent`, or `error` with a Retry once a Sent copy gives up.
The Message-ID + raw bytes are built once and reused, so the Sent copy matches the wire.
"""
import json
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from mailmind.compose.application.ports import Accounts, Account, Book, Copies, Effects, Messages, Screen, Transport
from mailmind.compose.domain import draft, outbox_item
from mailmind.compose.domain.builder import Built
from mailmind.compose.domain.errors import Unanswered
from mailmind.compose.domain.events import DraftCopyRemoved, Failed, Sent
from mailmind.compose.domain.outbox_item import SendMeta
from mailmind.kernel.row import Connection, Row
from mailmind.platform import bus, profile
from mailmind.platform.db.uow import UnitOfWork
from mailmind.platform.logging import get_logger
from mailmind.platform.workers import gates
from mailmind.platform.workers.durable_queue import DurableQueue

log = get_logger("outbox")


def _now() -> int:
    return int(time.time())


class Outbox:
    MAX_ATTEMPTS = outbox_item.MAX_ATTEMPTS
    _BACKOFF_BASE = outbox_item.BACKOFF_BASE
    _BACKOFF_CAP = outbox_item.BACKOFF_CAP
    _POLL = 30             # worker wakes at least this often to catch due retries
    SEND_DELAY = outbox_item.SEND_DELAY
    LOOK_IN_SENT_WAIT = 5  # how long a Retry waits for the account's gate, to look in Sent first

    def __init__(self, uow: UnitOfWork, book: Book, transport: Transport, copies: Copies, accounts: Accounts, mail: Messages,
                 effects: Effects, screen: Screen):
        self._uow, self._book, self._transport, self._copies = uow, book, transport, copies
        self._accounts, self._mail, self._effects, self._screen = accounts, mail, effects, screen
        # each account's sends in a lane of their own: one waiting on its IMAP server for a Sent copy
        # holds up no other account's
        self.QUEUE = DurableQueue("outbox", "outbox", running="sending", finished="sent", finished_at="sent_at",
                                  gate="delivered", connect=uow.connect, max_attempts=self.MAX_ATTEMPTS,
                                  backoff_base=self._BACKOFF_BASE, backoff_cap=self._BACKOFF_CAP, poll=self._POLL,
                                  lane="account", unsure=("wire_begun", outbox_item.UNSURE))

    def enqueue(self, built: Built, meta: SendMeta) -> tuple[int | None, bool]:
        """Persist a queued send. `built` = compose.build_message(...) output; `meta` =
        {account, idempotency_key, in_reply_to_pk, orig_message_id, subject, to, cc, bcc}. Returns
        (outbox_id, created). Kicks the worker."""
        orig = self._mail.original(meta.get("in_reply_to_pk"))         # its row may be gone by the time this goes out
        row = outbox_item.new_row(built, meta, orig, _now() + self.SEND_DELAY, datetime.now(timezone.utc).isoformat())
        with self._uow.write() as c:
            oid, created = self._book.outbox_insert(c, row)
        self.kick()
        return oid, created

    def cancel(self, oid: int) -> bool:
        """Undo Send — drop the row if it's still in its grace window. False if too late."""
        return self._commit(lambda c: self.QUEUE.cancel_queued(c, oid))

    def kick(self) -> None:
        self.QUEUE.kick()

    def retry(self, oid: int) -> dict[str, bool]:
        """A person's Retry. On an unsure row it may send what went out already: they were told. Where the server
        files sent mail itself, a copy in Sent settles that first, and only the row's bookkeeping is left to do."""
        went_out = self._in_sent_already(oid)

        def again(c: Connection) -> bool:
            ok = self.QUEUE.requeue(c, oid)
            if ok:
                self._book.outbox_set(c, oid, **outbox_item.RETRIED)
                if went_out:
                    self.QUEUE.mark_gate(c, oid)
            return ok
        ok = self._commit(again)
        if ok:
            self.kick()
        return {"ok": True, "gone": True} if ok and went_out else {"ok": ok}

    def _in_sent_already(self, oid: int) -> bool:
        """Is an unsure send in Sent? False whenever that can't be told, and they were told it might go twice."""
        with self._uow.read() as c:
            row = self._book.outbox_get(c, oid)
        if row is None or not outbox_item.is_unsure(row):
            return False
        try:
            acct = self._accounts.get(row["account"])
            if not self._accounts.saves_sent(acct):
                return False
            return gates.run(acct["email"], lambda: self._copies.in_sent(acct, row["message_id"]),
                             priority=gates.Priority.INTERACTIVE, deadline=self.LOOK_IN_SENT_WAIT)
        except Exception as e:
            log.warning("couldn't look in Sent for #%(id)s", {"id": oid}, exc_info=e)
            return False

    def list_recent(self, limit: int = 50) -> dict[str, Any]:
        with self._uow.read() as c:
            rows = self._book.outbox_list(c, limit)
        out = [outbox_item.listed(r) for r in rows]
        return {"ok": True, "items": out}

    def send_now(self, oid: int) -> bool:
        """Bring a scheduled send forward to now (Send-now from the outbox flyout)."""
        with self._uow.write() as c:
            ok = self._book.outbox_send_now(c, oid, _now())
        if ok:
            self.kick()
        return ok

    def _commit[T](self, fn: Callable[[Connection], T]) -> T:
        """Run fn(c) in a write block of its own, and return what fn returned."""
        with self._uow.write() as c:
            return fn(c)

    def _side_effects(self, oid: int, row: Row, acct: Account) -> str | None:
        """Done-once: Sent copy (non-Gmail) + \\Answered on the original, and the rest below.
        Each IMAP op is a job of its own on its account's IMAP gate (NOT one across the whole
        thing). A failure here never fails an already-delivered send. Each step is owed until its
        flag is set: what failed is returned (scrubbed), for send_one to try again, and the next
        pass does only what's still owed."""
        owed = None
        owes = outbox_item.owed(row, self._accounts.saves_sent(acct))
        if owes.sent_filed_for_us:
            self._commit(lambda c: self._book.outbox_set(c, oid, sent_appended=1))
        elif owes.sent_copy:
            mid = outbox_item.sent_copy_to_look_for(row)
            try:
                gates.run(acct["email"], lambda: self._copies.append_to_sent(acct, row["raw"], message_id=mid),
                          priority=gates.Priority.AFTER_USER)
                self._commit(lambda c: self._book.outbox_set(c, oid, sent_appended=1))
            except Exception as e:
                owed = f"sent copy failed: {type(e).__name__}"
                log.warning("Sent-copy failed for #%(id)s: %(why)s", {"id": oid, "why": self._transport.sanitize_error(e)})
        if owes.answered:
            try:
                pk = self._original_pk(row)
                gates.run(row["orig_account"] or self._effects.account_of(pk) or gates.EVERY_ACCOUNT,
                          lambda: self._effects.mark_answered(pk, row["orig_account"], row["orig_message_id"]),
                          priority=gates.Priority.AFTER_USER)
                self._commit(lambda c: self._book.outbox_set(c, oid, answered_set=1))
            except Exception as e:
                owed = owed or f"marking the original answered failed: {type(e).__name__}"
                log.warning("\\Answered failed for #%(id)s", {"id": oid}, exc_info=e)
        if owes.followup:                        # best-effort: it never fails the send, and nothing is asked again for it
            try:
                orig = self._mail.original(self._original_pk(row), row["orig_account"], row["orig_message_id"])
                tid = outbox_item.thread_of_reply(row, orig)
                if tid:
                    with self._uow.write() as c:
                        self._effects.followup_sent(c, row["account"], tid, orig["pk"] if orig else None,
                                                    _now(), datetime.now(timezone.utc).isoformat())
                self._commit(lambda c: self._book.outbox_set(c, oid, followup_recorded=1))
            except Exception as e:
                log.warning("followup record failed for #%(id)s", {"id": oid}, exc_info=e)
        if owes.draft:
            try:
                with self._uow.read() as c:
                    dr = self._book.draft_get(c, row["draft_id"])
                if dr:
                    def cleanup() -> None:   # the row goes with its copy: Edit re-checks it under the gate
                        removed = self.draft_copy_is_ours(dr) and self._copies.delete_draft_uid(
                            self._accounts.get(dr["account"]), dr["folder"], dr["uid"], dr["uidvalidity"])
                        with self._uow.read() as c:
                            waiting = self._book.journal_get(c, row["draft_id"])
                        if waiting is not None and waiting["tried"] and waiting["account"] == dr["account"]:
                            self._copies.delete_left_copy(self._accounts.get(dr["account"]), waiting["tried"])

                        def forget(c: Connection) -> None:
                            self._book.draft_delete(c, row["draft_id"])
                            if removed:
                                self._mail.remove_copies(c, dr["account"], dr["folder"], [dr["uid"]])
                        self._commit(forget)
                        if removed:
                            bus.publish(DraftCopyRemoved(dr["account"], dr["folder"]))
                    gates.run(dr["account"], cleanup, priority=gates.Priority.AFTER_USER)
                self._commit(lambda c: self._book.outbox_set(c, oid, draft_deleted=1))
            except Exception as e:
                owed = owed or f"removing the draft's copy failed: {type(e).__name__}"
                log.warning("draft cleanup failed for #%(id)s", {"id": oid}, exc_info=e)
        return owed

    def _original_pk(self, row: Row) -> int | None:
        """What pk_of_original gives, if it can still be the original. One queued before migration 27 names nothing to check
        its pk against, and goes by it as it always has."""
        pk = outbox_item.pk_of_original(row)
        if pk is None or not row["orig_account"]:
            return pk
        return pk if outbox_item.still_the_original(row, self._mail.message(pk)) else None

    def draft_copy_is_ours(self, d: Row) -> bool:
        def flagged() -> bool:
            with self._uow.read() as c:
                return self._book.flagged_draft(c, d["account"], d["folder"], d["uid"])
        return draft.copy_is_ours(d, self._screen.names_drafts, flagged)

    def send_one(self, oid: int) -> None:
        row = self._commit(lambda c: self.QUEUE.claim(c, oid))
        if row is None:                          # lost the claim (cancelled / another worker)
            return
        try:
            acct = self._accounts.get(row["account"])  # host/port/creds ONLY from config (no SSRF)
        except KeyError:
            self._commit(lambda c: self.QUEUE.give_up(c, row, "account removed"))
            bus.publish(Failed(oid, row["account"], True))
            return
        # Deliver exactly once. A recovered row (delivered=1, requeued by reset_stuck) skips
        # the wire send and only finishes bookkeeping — so a crash after send never re-sends.
        if not row["delivered"]:
            try:
                self._transport.send(acct, row["raw"], json.loads(row["envelope"]),  # lock-free: SMTP != IMAP ops
                              before_dot=lambda: self._commit(lambda c: self._book.outbox_set(c, oid, wire_begun=1)))
            except Exception as e:
                tried = outbox_item.failed_try(unanswered=isinstance(e, Unanswered), permanent=self._transport.permanent(e),
                                               error=self._transport.sanitize_error(e))

                def failed(c: Connection) -> tuple[bool, int]:
                    self._book.outbox_set(c, oid, **tried.marks)
                    if tried.give_up:
                        self.QUEUE.give_up(c, row, tried.error)
                        return True, 0
                    return self.QUEUE.retry_later(c, row, tried.error, _now())
                terminal, delay = self._commit(failed)
                if tried.unsure:
                    log.warning("send #%(id)s went unanswered; not sending it again", {"id": oid})
                else:
                    log.warning("send #%(id)s failed (%(why)s); %(what)s",
                                {"id": oid, "why": tried.error, "what": "giving up" if terminal else f"retry in {delay}s"})
                bus.publish(Failed(oid, row["account"], terminal))
                return
            self._commit(lambda c: self.QUEUE.mark_gate(c, oid))  # delivered=1, committed BEFORE side effects
        # delivered — side effects, then mark sent (all guarded so a DB hiccup can't strand a
        # delivered row; next pass sees delivered=1 and only retries bookkeeping). A step that
        # failed sends the row back to the queue instead, delivered, until it's done or gives up.
        try:
            owed = self._side_effects(oid, row, acct)
            if owed:
                tried = {"id": oid, "attempts": outbox_item.attempts_since_delivery(row)}
                terminal, delay = self._commit(lambda c: self.QUEUE.retry_later(c, tried, owed, _now()))
                log.warning("sent #%(id)s; what it still owes %(what)s",
                            {"id": oid, "what": "gives up" if terminal else f"is tried again in {delay}s"})
            else:
                self._commit(lambda c: self.QUEUE.finish(c, oid, datetime.now(timezone.utc).isoformat()))
                log.info("sent #%(id)s", {"id": oid})
            bus.publish(Sent(oid, row["account"]))
        except Exception as e:
            log.warning("post-send bookkeeping for #%(id)s deferred", {"id": oid}, exc_info=e)

    def drain(self) -> None:
        if not profile.writes_allowed():     # read-only: sends wait here for a full run (D4)
            return
        self.QUEUE.drain(self.send_one, _now())        # one bad row is logged and never aborts the rest

    def worker_loop(self) -> None:
        self.QUEUE.run_forever(self.drain)             # recovers rows a crash left mid-send, then drains

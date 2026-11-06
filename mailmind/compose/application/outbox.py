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
from mailmind.compose.domain import outbox_item
from mailmind.compose.domain.builder import Built
from mailmind.compose.domain.errors import Unanswered
from mailmind.compose.domain.events import DraftCopyRemoved, Failed, Sent
from mailmind.compose.domain.outbox_item import SendMeta
from mailmind.kernel.row import Connection, Row
from mailmind.platform import bus, profile
from mailmind.platform.workers import gates
from mailmind.platform.workers.durable_queue import DurableQueue


def _now() -> int:
    return int(time.time())


class Outbox:
    MAX_ATTEMPTS = 5
    _BACKOFF_BASE = 20      # secs; doubles per attempt
    _BACKOFF_CAP = 900
    _POLL = 30             # worker wakes at least this often to catch due retries
    SEND_DELAY = 10        # grace window before the wire send, so Undo Send can cancel it
    LOOK_IN_SENT_WAIT = 5  # how long a Retry waits for the account's gate, to look in Sent first

    def __init__(self, book: Book, transport: Transport, copies: Copies, accounts: Accounts, mail: Messages,
                 effects: Effects, screen: Screen):
        self._book, self._transport, self._copies = book, transport, copies
        self._accounts, self._mail, self._effects, self._screen = accounts, mail, effects, screen
        # each account's sends in a lane of their own: one waiting on its IMAP server for a Sent copy
        # holds up no other account's
        self.QUEUE = DurableQueue("outbox", "outbox", running="sending", finished="sent", finished_at="sent_at",
                                  gate="delivered", connect=lambda: book.connect(), max_attempts=self.MAX_ATTEMPTS,
                                  backoff_base=self._BACKOFF_BASE, backoff_cap=self._BACKOFF_CAP, poll=self._POLL,
                                  lane="account", unsure=("wire_begun", outbox_item.UNSURE))

    def enqueue(self, built: Built, meta: SendMeta) -> tuple[int | None, bool]:
        """Persist a queued send. `built` = compose.build_message(...) output; `meta` =
        {account, idempotency_key, in_reply_to_pk, orig_message_id, subject, to, cc, bcc}. Returns
        (outbox_id, created). Kicks the worker."""
        orig = self._mail.original(meta.get("in_reply_to_pk"))         # its row may be gone by the time this goes out
        row = outbox_item.new_row(built, meta, orig, _now() + self.SEND_DELAY, datetime.now(timezone.utc).isoformat())
        c = self._book.connect()
        try:
            oid, created = self._book.outbox_insert(c, row)
            c.commit()
        finally:
            c.close()
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
                self._book.outbox_set(c, oid, wire_begun=0, wait_kind="grace")
                if went_out:
                    self.QUEUE.mark_gate(c, oid)
            return ok
        ok = self._commit(again)
        if ok:
            self.kick()
        return {"ok": True, "gone": True} if ok and went_out else {"ok": ok}

    def _in_sent_already(self, oid: int) -> bool:
        """Is an unsure send in Sent? False whenever that can't be told, and they were told it might go twice."""
        row = self._commit(lambda c: self._book.outbox_get(c, oid))
        if row is None or row["state"] != "error" or not row["wire_begun"] or row["delivered"]:
            return False
        try:
            acct = self._accounts.get(row["account"])
            if not self._accounts.saves_sent(acct):
                return False
            return gates.run(acct["email"], lambda: self._copies.in_sent(acct, row["message_id"]),
                             priority=gates.Priority.INTERACTIVE, deadline=self.LOOK_IN_SENT_WAIT)
        except Exception as e:
            print(f"[outbox] couldn't look in Sent for #{oid}: {type(e).__name__}", flush=True)
            return False

    def list_recent(self, limit: int = 50) -> dict[str, Any]:
        c = self._book.connect()
        try:
            rows = self._book.outbox_list(c, limit)
        finally:
            c.close()
        out = [outbox_item.listed(r) for r in rows]
        return {"ok": True, "items": out}

    def send_now(self, oid: int) -> bool:
        """Bring a scheduled send forward to now (Send-now from the outbox flyout)."""
        c = self._book.connect()
        try:
            ok = self._book.outbox_send_now(c, oid, _now())
            c.commit()
        finally:
            c.close()
        if ok:
            self.kick()
        return ok

    def _commit[T](self, fn: Callable[[Connection], T]) -> T:
        """Run fn(c) on a short-lived connection, commit, and return what fn returned."""
        c = self._book.connect()
        try:
            out = fn(c)
            c.commit()
            return out
        finally:
            c.close()

    def _side_effects(self, oid: int, row: Row, acct: Account) -> str | None:
        """Done-once: Sent copy (non-Gmail) + \\Answered on the original, and the rest below.
        Each IMAP op is a job of its own on its account's IMAP gate (NOT one across the whole
        thing). A failure here never fails an already-delivered send. Each step is owed until its
        flag is set: what failed is returned (scrubbed), for send_one to try again, and the next
        pass does only what's still owed."""
        owed = None
        if self._accounts.saves_sent(acct) and not row["sent_appended"]:
            self._commit(lambda c: self._book.outbox_set(c, oid, sent_appended=1))   # Gmail auto-saves
        elif not row["sent_appended"]:
            # delivered before this run (a retry, or a crash's recovery): the last APPEND may have landed
            mid = row["message_id"] if row["delivered"] else None
            try:
                gates.run(acct["email"], lambda: self._copies.append_to_sent(acct, row["raw"], message_id=mid),
                          priority=gates.Priority.AFTER_USER)
                self._commit(lambda c: self._book.outbox_set(c, oid, sent_appended=1))
            except Exception as e:
                owed = f"sent copy failed: {type(e).__name__}"
                print(f"[outbox] Sent-copy failed for #{oid}: {self._transport.sanitize_error(e)}", flush=True)
        replied_to = row["in_reply_to_pk"] or row["orig_message_id"]
        if replied_to and not row["answered_set"]:
            try:
                gates.run(row["orig_account"] or self._effects.account_of(row["in_reply_to_pk"]) or gates.EVERY_ACCOUNT,
                          lambda: self._effects.mark_answered(row["in_reply_to_pk"], row["orig_account"], row["orig_message_id"]),
                          priority=gates.Priority.AFTER_USER)
                self._commit(lambda c: self._book.outbox_set(c, oid, answered_set=1))
            except Exception as e:
                owed = owed or f"marking the original answered failed: {type(e).__name__}"
                print(f"[outbox] \\Answered failed for #{oid}: {type(e).__name__}", flush=True)
        # sent-no-reply: seed a follow-up on the replied-to thread (replies only — a brand-new compose
        # has no local thread_id until its Sent copy syncs). Best-effort, done-once, never fails the send.
        if replied_to and not row["followup_recorded"]:
            try:
                orig = self._mail.original(row["in_reply_to_pk"], row["orig_account"], row["orig_message_id"])
                tid = (orig["thread_id"] if orig else None) or row["orig_thread_id"]
                c = self._book.connect()
                if tid:
                    self._effects.followup_sent(c, row["account"], tid, orig["pk"] if orig else row["in_reply_to_pk"],
                                               _now(), datetime.now(timezone.utc).isoformat())
                c.commit(); c.close()
                self._commit(lambda c: self._book.outbox_set(c, oid, followup_recorded=1))
            except Exception as e:
                print(f"[outbox] followup record failed for #{oid}: {type(e).__name__}", flush=True)
        # the draft this message was composed from is superseded once it's truly sent
        if row["draft_id"] and not row["draft_deleted"]:
            try:
                c = self._book.connect(); dr = self._book.draft_get(c, row["draft_id"]); c.close()
                if dr:
                    def cleanup() -> None:   # the row goes with its copy: Edit re-checks it under the gate
                        removed = self.draft_copy_is_ours(dr) and self._copies.delete_draft_uid(
                            self._accounts.get(dr["account"]), dr["folder"], dr["uid"], dr["uidvalidity"])
                        c = self._book.connect()
                        waiting = self._book.journal_get(c, row["draft_id"])
                        c.close()
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
                print(f"[outbox] draft cleanup failed for #{oid}: {type(e).__name__}", flush=True)
        return owed

    def draft_copy_is_ours(self, d: Row) -> bool:
        """May this `drafts` row's copy be removed from the server? A removal has no Undo, so only one
        MailMind saved (the row holds its hash, and a save that never synced has no `messages` row to
        be flagged), one the server flags \\Draft, or one in a folder named Drafts. Received mail
        filed under "Contract drafts" stays, and a `drafts` row may well point at it: until now Edit
        was offered in any folder whose name held "draft"."""
        if not d["uid"]:                         # a save still only in the journal: nothing to remove
            return False
        if d["raw_sha"] or self._screen.names_drafts(d["folder"]):
            return True
        c = self._book.connect()
        try:
            return self._book.flagged_draft(c, d["account"], d["folder"], d["uid"])
        finally:
            c.close()

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
            except Unanswered:            # it may have gone out: a person decides, never a retry
                self._commit(lambda c: self.QUEUE.give_up(c, row, outbox_item.UNSURE))
                print(f"[outbox] send #{oid} went unanswered; not sending it again", flush=True)
                bus.publish(Failed(oid, row["account"], True))
                return
            except Exception as e:
                err, permanent = self._transport.sanitize_error(e), self._transport.permanent(e)

                def failed(c: Connection) -> tuple[bool, int]:   # the server answered, or it never had the whole message
                    self._book.outbox_set(c, oid, wire_begun=0, wait_kind="backoff")
                    if permanent:                  # a 5xx: the same answer again, so no retry
                        self.QUEUE.give_up(c, row, err)
                        return True, 0
                    return self.QUEUE.retry_later(c, row, err, _now())
                terminal, delay = self._commit(failed)
                print(f"[outbox] send #{oid} failed ({err}); "
                      f"{'giving up' if terminal else f'retry in {delay}s'}", flush=True)
                bus.publish(Failed(oid, row["account"], terminal))
                return
            self._commit(lambda c: self.QUEUE.mark_gate(c, oid))  # delivered=1, committed BEFORE side effects
        # delivered — side effects, then mark sent (all guarded so a DB hiccup can't strand a
        # delivered row; next pass sees delivered=1 and only retries bookkeeping). A step that
        # failed sends the row back to the queue instead, delivered, until it's done or gives up.
        try:
            owed = self._side_effects(oid, row, acct)
            if owed:
                # its tries count from the delivery, not on from the send's
                tried = {"id": oid, "attempts": row["attempts"] if row["delivered"] else 0}
                terminal, delay = self._commit(lambda c: self.QUEUE.retry_later(c, tried, owed, _now()))
                print(f"[outbox] sent #{oid}; what it still owes "
                      f"{'gives up' if terminal else f'is tried again in {delay}s'}", flush=True)
            else:
                self._commit(lambda c: self.QUEUE.finish(c, oid, datetime.now(timezone.utc).isoformat()))
                print(f"[outbox] sent #{oid}", flush=True)
            bus.publish(Sent(oid, row["account"]))
        except Exception as e:
            print(f"[outbox] post-send bookkeeping for #{oid} deferred: {type(e).__name__}", flush=True)

    def drain(self) -> None:
        if not profile.writes_allowed():     # read-only: sends wait here for a full run (D4)
            return
        self.QUEUE.drain(self.send_one, _now())        # one bad row is logged and never aborts the rest

    def worker_loop(self) -> None:
        self.QUEUE.run_forever(self.drain)             # recovers rows a crash left mid-send, then drains

"""Silencing a thread, here and on the server (decision D6)."""
from collections.abc import Iterable
from typing import Any

from mailmind.attention.application.ports import AttentionBook, Messages, Undo
from mailmind.attention.application.threads import Threads
from mailmind.attention.domain import mute
from mailmind.attention.domain.events import ThreadMuted, ThreadUnmuted
from mailmind.kernel.errors import Refused
from mailmind.kernel.ids import AccountId, ThreadRef
from mailmind.kernel.row import Row
from mailmind.kernel.when import now_iso
from mailmind.platform import bus
from mailmind.platform.db.uow import UnitOfWork


class Muting:
    def __init__(self, uow: UnitOfWork, book: AttentionBook, messages: Messages, threads: Threads, undo: Undo):
        self._uow = uow
        self._book = book
        self._messages = messages
        self._threads = threads
        self._undo = undo

    def _mark_read(self, c: object, rows: Iterable[Row], when: str) -> None:
        for r in rows:
            self._messages.queue_flags(c, r, {"seen": True}, f"mute:{r['pk']}:{when}", when)

    def mute(self, body: dict[str, Any]) -> dict[str, Any]:
        with self._uow.write() as c:
            acct, tid = self._threads.target(c, body)
            if not acct or not tid:
                raise Refused("bad mute target")
            todo = mute.plan(self._book.is_muted(c, acct, tid), acct, tid, self._book.unread_in_thread(c, acct, tid),
                             self._book.a_member(c, acct, tid))
            if todo is None:
                return {"ok": True, "thread_id": tid}
            self._mark_read(c, todo.mark_read, now_iso())
            self._book.mute_thread(c, acct, tid, now_iso())
        self._undo.push(todo.undo, body.get("undo_group"))
        bus.publish(ThreadMuted(acct, tid))
        if todo.mark_read:
            self._messages.flags_queued()
        return {"ok": True, "thread_id": tid}

    def unmute(self, body: dict[str, Any]) -> dict[str, Any]:
        with self._uow.write() as c:
            acct, tid = self._threads.target(c, body)
            if not acct or not tid:
                raise Refused("bad mute target")
            self._book.unmute_thread(c, acct, tid)
        bus.publish(ThreadUnmuted(acct, tid))
        return {"ok": True, "thread_id": tid}

    def _thread_now(self, c: object, d: dict[str, Any]) -> list[ThreadRef]:
        """A merge moves a mute to the thread that took this one in, and an undo entry doesn't hear of it, so ask a
        message the thread held. One that has since been filed elsewhere can't say."""
        member = d.get("member")
        pk = self._messages.copy_in_folder(c, member[0], member[1]) if member else None
        r = self._messages.get(c, pk) if pk else None
        return [r["thread_id"]] if r and r["thread_id"] else []

    def reverse(self, d: dict[str, Any]) -> tuple[bool, str | None, None]:
        """Undo of a mute: unmuted, and unread again what it marked read."""
        marked, when = False, now_iso()
        with self._uow.write() as c:
            for tid in mute.threads_to_unmute(d, self._thread_now(c, d)):
                self._book.unmute_thread(c, d["account"], tid)
            for pk, seen in mute.seen_to_restore(d):
                self._messages.set_local_flags(c, pk, seen=seen)
            for key, folder in mute.marked_read(d):
                pk = self._messages.copy_in_folder(c, key, folder)
                r = self._messages.get(c, pk) if pk else None
                if r:
                    marked = self._messages.queue_flags(c, r, {"seen": False}, f"unmute:{pk}:{when}", when) or marked
        if marked:
            self._messages.flags_queued()
        return True, d.get("account"), None

    def on_ingest(self, c: object, account: str) -> None:      # the Mailbox's port says `str`
        """After threading assigns thread_ids, mark unseen members of any muted thread read, here and on the
        server, so freshly-arrived replies in a muted thread stay silent. In the caller's transaction."""
        self._mark_read(c, self._book.unread_in_muted_threads(c, AccountId(account)), now_iso())

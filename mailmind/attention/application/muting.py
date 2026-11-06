"""Silencing a thread, here and on the server (decision D6)."""
from mailmind.kernel.when import now_iso
from mailmind.platform import sse


class Muting:
    def __init__(self, book, messages, threads, undo, screen):
        self._book = book
        self._messages = messages
        self._threads = threads
        self._undo = undo
        self._screen = screen

    def _mark_read(self, c, rows, when):
        for r in rows:
            self._messages.queue_flags(c, r, {"seen": True}, f"mute:{r['pk']}:{when}", when)

    def mute(self, body):
        """(result, code)."""
        with self._book.transaction() as c:
            acct, tid = self._threads.target(c, body)
            if not tid:
                return {"ok": False, "error": "bad mute target"}, 400
            if self._book.is_muted(c, acct, tid):     # already muted: don't recapture prev_seen (it would be
                return {"ok": True, "thread_id": tid}, 200   # the post-mute all-seen state, breaking undo) — no-op
            rows = self._book.unread_in_thread(c, acct, tid)
            self._mark_read(c, rows, now_iso())
            unread = [[r["message_key"], r["folder"]] for r in rows]   # the ones Undo marks unread again: pk isn't stable
            self._book.mute_thread(c, acct, tid, now_iso())
        self._undo.push({"kind": "mute", "account": acct, "thread_id": tid,
                         "unread": unread, "label": "Muted"}, body.get("undo_group"))
        self._screen.stats_changed()
        sse.publish("mute", {"account": acct})
        if unread:
            self._screen.flags_queued()
        return {"ok": True, "thread_id": tid}, 200

    def unmute(self, body):
        """(result, code)."""
        with self._book.transaction() as c:
            acct, tid = self._threads.target(c, body)
            if not tid:
                return {"ok": False, "error": "bad mute target"}, 400
            self._book.unmute_thread(c, acct, tid)
        self._screen.stats_changed()
        sse.publish("mute", {"account": acct})
        return {"ok": True, "thread_id": tid}, 200

    def on_ingest(self, c, account):
        """After recompute_threads assigns thread_ids, mark unseen members of any muted thread read, here and on the
        server, so freshly-arrived replies in a muted thread stay silent. In the caller's transaction."""
        self._mark_read(c, self._book.unread_in_muted_threads(c, account), now_iso())

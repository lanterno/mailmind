"""Hiding a message until later, and bringing it back."""
import time
from datetime import datetime

from mailmind.attention.domain import snooze
from mailmind.kernel.when import now_iso
from mailmind.platform import sse


class Snoozing:
    def __init__(self, book, messages, undo, screen):
        self._book = book
        self._messages = messages
        self._undo = undo
        self._screen = screen

    def _pks(self, c, body):
        if body.get("thread_id") and body.get("account"):
            return self._book.thread_pks(c, body["account"], body["thread_id"])
        if isinstance(body.get("pks"), list):
            return [int(x) for x in body["pks"]][:snooze.BULK_CAP]
        if body.get("pk") is not None:
            return [int(body["pk"])]
        return []

    def snooze(self, body):
        """(result, code)."""
        until = snooze.wake_time(body, datetime.now().astimezone())
        if until is None:
            return {"ok": False, "error": "bad snooze time"}, 400
        with self._book.transaction() as c:
            pks = self._pks(c, body)
            prev, where = [], []
            for pk in pks:
                r = self._messages.get(c, pk)
                prev.append(r["snoozed_until"] if r else None)
                where.append([r["message_key"], r["folder"]] if r else None)   # pk isn't stable: the stack outlives it
                self._book.set_snooze(c, pk, until)
        if pks:
            self._undo.push({"kind": "snooze", "pks": pks, "where": where, "prev_until": prev, "label": "Snoozed"},
                            body.get("undo_group"))
        self._screen.stats_changed()
        return {"ok": True, "count": len(pks), "until": until}, 200

    def unsnooze(self, body):
        """(result, code)."""
        with self._book.transaction() as c:
            pks = self._pks(c, body)
            for pk in pks:
                self._book.clear_snooze(c, pk)
        self._screen.stats_changed()
        return {"ok": True, "count": len(pks)}, 200

    def wake_due(self):
        """Resurface messages whose snooze time has passed: clear the snooze, mark unread, and push a realtime event
        so they pop back into the inbox view. Database only: the unread mark reaches the server through the queue, or
        the next sync that read the flags would take it back."""
        now = int(time.time())
        marked = False
        with self._book.transaction() as c:
            due = self._book.due_snoozes(c, now)
            for r in due:
                self._book.clear_snooze(c, r["pk"])
                if r["seen"]:
                    marked = self._messages.queue_flags(c, r, {"seen": False}, f"wake:{r['pk']}:{now}", now_iso()) or marked
        if marked:
            self._screen.flags_queued()
        if due:
            self._screen.stats_changed()
            sse.publish("snooze-wake", {"count": len(due), "accounts": sorted(set(r["account"] for r in due))})
            print(f"[snooze] woke {len(due)} message(s)", flush=True)

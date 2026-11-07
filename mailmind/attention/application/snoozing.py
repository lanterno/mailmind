"""Hiding a message until later, and bringing it back."""
import time
from datetime import datetime
from typing import Any

from mailmind.attention.application.ports import AttentionBook, Messages, Screen, Undo
from mailmind.attention.domain import snooze
from mailmind.kernel.row import Row
from mailmind.kernel.when import now_iso
from mailmind.platform import sse
from mailmind.platform.db.uow import UnitOfWork
from mailmind.platform.logging import get_logger

log = get_logger("snooze")


class Snoozing:
    def __init__(self, uow: UnitOfWork, book: AttentionBook, messages: Messages, undo: Undo, screen: Screen):
        self._uow = uow
        self._book = book
        self._messages = messages
        self._undo = undo
        self._screen = screen

    def reverse(self, d: dict[str, Any]) -> tuple[bool, str | None, None]:
        """Undo of a snooze: each message as it was."""
        with self._uow.write() as c:
            for r in snooze.restorations(d):
                pk = self._messages.copy_in_folder(c, *r.where) if r.where else r.pk
                if pk is None:
                    continue
                if r.until is not None:
                    self._book.set_snooze(c, pk, r.until)
                else:
                    self._book.clear_snooze(c, pk)
                self._messages.index(c, pk)
        return True, d.get("account"), None

    def _pks(self, c: object, body: dict[str, Any]) -> list[int]:
        thread = snooze.named_thread(body)
        return self._book.thread_pks(c, *thread) if thread else snooze.named_pks(body)

    def snooze(self, body: dict[str, Any]) -> tuple[dict[str, Any], int]:
        """(result, code)."""
        until = snooze.wake_time(body, datetime.now().astimezone())
        if until is None:
            return {"ok": False, "error": "bad snooze time"}, 400
        with self._uow.write() as c:
            pks = self._pks(c, body)
            before: list[Row | None] = []
            for pk in pks:
                before.append(self._messages.get(c, pk))
                self._book.set_snooze(c, pk, until)
        if pks:
            self._undo.push(snooze.undo_entry(pks, before), body.get("undo_group"))
        self._screen.stats_changed()
        return {"ok": True, "count": len(pks), "until": until}, 200

    def unsnooze(self, body: dict[str, Any]) -> tuple[dict[str, Any], int]:
        """(result, code)."""
        with self._uow.write() as c:
            pks = self._pks(c, body)
            for pk in pks:
                self._book.clear_snooze(c, pk)
        self._screen.stats_changed()
        return {"ok": True, "count": len(pks)}, 200

    def wake_due(self) -> None:
        """Resurface messages whose snooze time has passed: clear the snooze, mark unread, and push a realtime event
        so they pop back into the inbox view. Database only: the unread mark reaches the server through the queue, or
        the next sync that read the flags would take it back."""
        now = int(time.time())
        marked = False
        with self._uow.write() as c:
            due = self._book.due_snoozes(c, now)
            for r in due:
                self._book.clear_snooze(c, r["pk"])
                if snooze.marks_unread_on_waking(r["seen"]):
                    marked = self._messages.queue_flags(c, r, {"seen": False}, f"wake:{r['pk']}:{now}", now_iso()) or marked
        if marked:
            self._screen.flags_queued()
        if due:
            self._screen.stats_changed()
            sse.publish("snooze-wake", {"count": len(due), "accounts": sorted(set(r["account"] for r in due))})
            log.info("woke %d message(s)", len(due))

"""The tasks a person has confirmed from their mail: kept, listed where Commitments are, marked done and removed. What a person
confirmed is read as their input, so a date that won't do is refused and said so."""
from collections.abc import Mapping
from datetime import date
from typing import Any

from mailmind.attention.application.ports import Accounts, AttentionBook, Messages
from mailmind.attention.domain import tasks
from mailmind.attention.domain.events import TasksChanged
from mailmind.kernel import proposed
from mailmind.kernel.errors import NotFound, Refused
from mailmind.kernel.ids import AccountId, FolderName, MessageKey, Pk
from mailmind.kernel.parsed import as_int, required_int
from mailmind.kernel.when import now_iso
from mailmind.platform import bus
from mailmind.platform.db.uow import UnitOfWork


class Tasks:
    def __init__(self, uow: UnitOfWork, book: AttentionBook, messages: Messages, accounts: Accounts):
        self._uow = uow
        self._book = book
        self._messages = messages
        self._accounts = accounts

    def add(self, body: Mapping[str, Any]) -> dict[str, Any]:
        pk = as_int(body.get("pk"))
        if pk is None:
            raise Refused("missing message")
        title = proposed.one_line(body.get("title"), proposed.TITLE_MAX)
        if not title:
            raise Refused("a task needs a title")
        due = None
        if body.get("due"):
            due = proposed.read_date(body.get("due"), date.today())
            if due is None:
                raise Refused("that date isn’t one this can use")
        with self._uow.write() as c:
            row = self._messages.get(c, Pk(pk))
            if not row or row["message_key"] is None:
                raise NotFound("not found")
            task_id = self._book.add_task(c, AccountId(row["account"]), MessageKey(row["message_key"]),
                                          FolderName(row["folder"]), title, due, now_iso())
            item = self._book.task_rows(c, task_id)[0]
        bus.publish(TasksChanged())
        return {"ok": True, "task": tasks.as_listed(item)}

    def listing(self) -> dict[str, Any]:
        """Every enabled account's tasks. One whose account is off stays, for when it is back on."""
        enabled = set(self._accounts.emails())
        with self._uow.read() as c:
            rows = self._book.task_rows(c)
        return {"ok": True, "items": [tasks.as_listed(r) for r in rows if r["account"] in enabled]}

    def mark(self, body: Mapping[str, Any]) -> dict[str, Any]:
        done_at = None if body.get("done") is False else now_iso()
        with self._uow.write() as c:
            found = self._book.set_task_done(c, required_int(body.get("id")), done_at)
        return self._said(found)

    def remove(self, body: Mapping[str, Any]) -> dict[str, Any]:
        with self._uow.write() as c:
            found = self._book.remove_task(c, required_int(body.get("id")))
        return self._said(found)

    def _said(self, found: bool) -> dict[str, Any]:
        if not found:
            raise NotFound("that task is gone")
        bus.publish(TasksChanged())
        return {"ok": True}

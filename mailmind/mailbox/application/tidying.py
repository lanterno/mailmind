"""Tidy on its own: once a day, for someone who turned it on, what the brief's Tidy offers goes to Archive under one undo group,
and the brief says so and can take all of it back. Whether it may run at all is the job's to ask (bootstrap.background)."""
from datetime import datetime
from typing import Any

from mailmind.kernel.errors import Refused
from mailmind.mailbox.application.filing import Filing
from mailmind.mailbox.application.ports import StoredMail
from mailmind.mailbox.domain import tidy
from mailmind.platform.db.uow import UnitOfWork
from mailmind.platform.undo import Undo


class Tidying:
    def __init__(self, uow: UnitOfWork, store: StoredMail, filing: Filing, undo: Undo):
        self._uow = uow
        self._store = store
        self._filing = filing
        self._undo = undo

    def round(self) -> None:
        """Today's run, if none has filed anything yet. Nothing to file leaves nothing recorded, so a later round looks again."""
        now = datetime.now().astimezone()
        if now.hour < tidy.FIRST_HOUR:
            return
        group = tidy.group_of(now.date().isoformat())
        with self._uow.read() as c:
            if self._store.command_ran_under(c, group):
                return
            rows = self._store.digest(c, limit=tidy.READS)
            taken_back = self._store.archives_undone(c)
        picked = [{"key": r["message_key"], "folder": r["folder"], "uid": r["uid"]} for r in rows if tidy.takes(r, taken_back)]
        if picked:
            self._filing.file_many({"verb": "archive", "rows": picked, "undo_group": group, "mutation_id": group})

    def latest(self) -> dict[str, Any]:
        """The newest run a person can still take back, and what it filed, for the strip in the brief. With an Undo of it, or
        once it has aged off the stack, there is none."""
        for entry in reversed(self._undo.entries()):
            if tidy.is_run(entry.get("group")):
                children = entry["children"]
                return {"ok": True, "group": entry["group"], "filed": len(children),
                        "items": [{"subject": d.get("subject"), "who": d.get("who")} for d in reversed(children)]}
        return {"ok": True, "group": None, "filed": 0, "items": []}

    def undo(self, group: object) -> dict[str, Any]:
        """Take a whole run back, wherever it stands on the undo stack, and say what went back as an Undo does. Raises Refused
        for a group that is no run of Tidy's, so this reverses nothing else."""
        if not tidy.is_run(group):
            raise Refused()
        return self._undo.undo_group(str(group))

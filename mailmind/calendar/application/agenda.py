"""Today & next. Pure-local: it reads only stored bodies, never IMAP."""
import time
from datetime import datetime
from typing import Any

from mailmind.calendar.application.ports import IcsReader, StoredInvites
from mailmind.calendar.domain import agenda
from mailmind.calendar.domain.invite import Invite, Party
from mailmind.kernel.row import Row

_EXPAND_SECONDS = 1.0       # of this thread's time, on one list: a reader bounds each rule, but one message can hold many


class Agenda:
    def __init__(self, stored: StoredInvites, reader: IcsReader):
        self._stored = stored
        self._reader = reader

    def upcoming(self, now: datetime | None = None, limit: int = 5, days: int = 60) -> dict[str, Any]:
        """The events in calendar invites from the last `days` of mail that haven't ended yet, soonest first; a
        repeating one at its next occurrence. One row per event UID, from the message with its latest SEQUENCE, the newest
        of those that have it. Times are local wall-clock ISO strings."""
        now = now or datetime.now()
        seen: dict[tuple[str | tuple[str, str], str], int] = {}
        found: list[tuple[Row, Invite]] = []
        for r, text in self._stored.invite_parts(int(now.timestamp()) - days * 86400, agenda.SKIPPED_ROLES):
            try:
                events = self._reader.events(text)
            except Exception:
                continue
            for invite in events:
                if not invite.get("start_iso"):
                    continue
                key = agenda.event_key(invite)
                held = seen.get(key)
                if held is None:
                    seen[key] = len(found)
                    found.append((r, invite))
                elif agenda.supersedes(invite, found[held][1]):
                    found[held] = (r, invite)
        moved, gone = agenda.changed_occurrences([inv for _r, inv in found])
        items: list[dict[str, Any]] = []
        expand_until = time.thread_time() + _EXPAND_SECONDS
        for r, inv in agenda.cut_up(found):
            if agenda.dead(inv) or (inv.get("recurrence_id") and inv.get("uid") in gone):
                continue
            if agenda.repeats(inv) and not inv.get("recurrence_id"):
                inv: Invite = {**inv, "exdates": inv["exdates"] + moved.get(inv.get("uid"), [])}
            over = agenda.repeats(inv) and time.thread_time() > expand_until
            occ = None if over else self._reader.next_occurrence(inv, now)
            if occ is None and agenda.repeats(inv):                  # a rule the reader can't expand, or isn't asked to: its first date
                occ = self._reader.next_occurrence(agenda.first_date(inv), now)
            if occ is None:
                continue
            try:
                start, end = (agenda.local_naive(d) for d in occ)
            except (OverflowError, ValueError):                      # a date this machine's zone pushes past year 1 or 9999
                continue
            org = inv.get("organizer") or Party()
            items.append({
                "key": r["message_key"], "folder": r["folder"], "uid": r["uid"], "account": r["account"], "thread_id": r["thread_id"],
                "summary": inv["summary"], "location": inv.get("location", ""),
                "start": start.isoformat(timespec="minutes"), "end": end.isoformat(timespec="minutes"),
                "all_day": inv["all_day"], "organizer": org.get("name") or org.get("email") or "",
                "invite": inv.get("method") == "REQUEST", "recurrence": inv.get("recurrence", ""),
            })
        items.sort(key=lambda x: x["start"])
        return {"ok": True, "items": items[:limit]}

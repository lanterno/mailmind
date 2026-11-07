"""Today & next. Pure-local: it reads only stored bodies, never IMAP."""
from datetime import datetime
from typing import Any

from mailmind.calendar.application.ports import IcsReader, StoredInvites
from mailmind.calendar.domain import agenda
from mailmind.calendar.domain.invite import Invite, Party
from mailmind.kernel.row import Row


class Agenda:
    def __init__(self, stored: StoredInvites, reader: IcsReader):
        self._stored = stored
        self._reader = reader

    def upcoming(self, account: str | None = None, now: datetime | None = None, limit: int = 5, days: int = 60) -> dict[str, Any]:
        """The events in calendar invites from the last `days` of mail that haven't ended yet, soonest first; a
        repeating one at its next occurrence. One row per event UID, from the newest message that carries it. Times
        are local wall-clock ISO strings."""
        now = now or datetime.now()
        seen: set[tuple[str | tuple[str, str], str]] = set()
        found: list[tuple[Row, Invite]] = []
        for r, text in self._stored.invite_parts(int(now.timestamp()) - days * 86400, account, agenda.SKIPPED_ROLES):
            try:
                events = self._reader.events(text)
            except Exception:
                continue
            for invite in events:
                if not invite.get("start_iso"):
                    continue
                key = agenda.event_key(invite)
                if key not in seen:
                    seen.add(key)
                    found.append((r, invite))
        moved, gone = agenda.changed_occurrences([inv for _r, inv in found])
        items: list[dict[str, Any]] = []
        for r, inv in found:
            if agenda.dead(inv) or (inv.get("recurrence_id") and inv.get("uid") in gone):
                continue
            if inv.get("rrule") and not inv.get("recurrence_id"):
                inv: Invite = {**inv, "exdates": inv["exdates"] + moved.get(inv.get("uid"), [])}
            occ = self._reader.next_occurrence(inv, now)
            if occ is None and inv.get("rrule"):                     # a rule the reader can't expand: its first date
                occ = self._reader.next_occurrence({**inv, "rrule": ""}, now)
            if occ is None:
                continue
            start, end = (agenda.local_naive(d) for d in occ)
            org = inv.get("organizer") or Party()
            items.append({
                "pk": r["pk"], "key": r["message_key"], "account": r["account"], "thread_id": r["thread_id"],
                "summary": inv["summary"], "location": inv.get("location", ""),
                "start": start.isoformat(timespec="minutes"), "end": end.isoformat(timespec="minutes"),
                "all_day": inv["all_day"], "organizer": org.get("name") or org.get("email") or "",
                "invite": inv.get("method") == "REQUEST", "recurrence": inv.get("recurrence", ""),
            })
        items.sort(key=lambda x: x["start"])
        return {"ok": True, "items": items[:limit]}

"""The invite a message carries, for the reader's card and an RSVP."""
from collections.abc import Mapping
from typing import Any

from mailmind.calendar.application.ports import IcsReader, StoredInvites
from mailmind.calendar.domain.invite import Invite
from mailmind.content import mime


class Invites:
    def __init__(self, stored: StoredInvites, reader: IcsReader):
        self._stored = stored
        self._reader = reader

    def of(self, pk: int, parsed: Mapping[str, Any]) -> tuple[Invite | None, str | None]:
        """If the message carries a text/calendar part, parse it into a display-ready invite: (invite, part id)."""
        cal = next((a for a in parsed.get("attachments", [])
                    if "calendar" in (a.get("content_type") or "").lower()
                    or (a.get("filename") or "").lower().endswith(".ics")), None)
        if not cal:
            return None, None
        raw = self._stored.body(pk)
        if not raw:
            return None, None
        try:
            payload, _ct = mime.extract_part(raw, cal["part_id"])
            if payload is None:
                return None, None
            return self._reader.parse(payload.decode("utf-8", "replace")), cal["part_id"]
        except Exception:
            return None, None

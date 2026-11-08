"""An event a person has confirmed, as a file for their calendar. It sends nothing and creates nothing here: the page saves what
this answers, and they open it. What they confirmed is read as a person's input, so a date that won't do is refused and said so,
where a model's is dropped."""
from collections.abc import Mapping
from datetime import date, datetime, timezone
from typing import Any

from mailmind.calendar.application.ports import Messages
from mailmind.calendar.domain import publish
from mailmind.kernel import proposed
from mailmind.kernel.errors import NotFound, Refused
from mailmind.kernel.parsed import as_int

FILENAME = "event.ics"


class Publishing:
    def __init__(self, messages: Messages):
        self._messages = messages

    def event(self, body: Mapping[str, Any]) -> dict[str, Any]:
        pk = as_int(body.get("pk"))
        if pk is None:
            raise Refused("missing message")
        title = proposed.one_line(body.get("title"), proposed.TITLE_MAX)
        if not title:
            raise Refused("an event needs a title")
        today = date.today()
        start = proposed.read_moment(body.get("start"), today)
        if start is None:
            raise Refused("that start isn’t a date this can use")
        end = None
        if body.get("end"):
            end = proposed.read_moment(body.get("end"), today)
            if end is None or not proposed.ends_after(start, end):
                raise Refused("that end doesn’t come after the start")
        row = self._messages.message(pk)
        if not row:
            raise NotFound("not found")
        event = publish.NewEvent(title, start, end, proposed.one_line(body.get("place"), proposed.PLACE_MAX))
        uid = publish.uid_for(row["account"], row["message_key"], event)
        return {"ok": True, "filename": FILENAME, "ics": publish.build(event, row["account"], uid, datetime.now(timezone.utc))}

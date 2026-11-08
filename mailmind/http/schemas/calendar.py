"""What the page asks of Calendar, and what it answers."""
from typing import Literal

from mailmind.http.schemas.common import Answer, Number, RequestBody


class RsvpBody(RequestBody):
    """The invite, by its message's key, and the folder and uid of the copy the page has (`mailmind.http.named`)."""
    key: Number
    folder: str | None = None
    uid: Number | None = None
    decision: str | None = None


class RsvpQueued(Answer):
    ok: Literal[True]
    outbox_id: int
    queued: bool
    decision: str
    organizer: str
    summary: str


class UpcomingEvent(Answer):
    key: int
    folder: str
    uid: int                                # the copy's on the server, not the event's
    account: str
    thread_id: str | None
    summary: str
    location: str
    start: str
    end: str
    all_day: bool
    organizer: str
    invite: bool
    recurrence: str


class Upcoming(Answer):
    ok: Literal[True]
    items: list[UpcomingEvent]

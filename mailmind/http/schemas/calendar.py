"""What the page asks of Calendar, and what it answers."""
from typing import Literal

from mailmind.http.schemas.common import Answer, NamesAMessage, Number


class RsvpBody(NamesAMessage):
    pk: Number | None = None
    decision: str | None = None


class RsvpQueued(Answer):
    ok: Literal[True]
    outbox_id: int
    queued: bool
    decision: str
    organizer: str
    summary: str


class UpcomingEvent(Answer):
    pk: int
    key: int
    folder: str
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

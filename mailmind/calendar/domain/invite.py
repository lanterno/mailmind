"""An invite as the reader of its text gives it, which agenda and rsvp read and the reader's card shows."""
from typing import TypedDict


class Party(TypedDict, total=False):
    """An organizer or an attendee. An invite with no organizer holds an empty one."""
    name: str
    email: str


class Invite(TypedDict):
    summary: str
    location: str
    description: str
    when: str
    all_day: bool
    start_iso: str
    end_iso: str
    organizer: Party
    attendees: list[Party]
    recurrence: str
    method: str
    status: str
    uid: str
    rrule: str
    exdates: list[str]
    recurrence_id: str
    tzid: str

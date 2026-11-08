"""An event of a person's own, as a METHOD:PUBLISH calendar: a file for their calendar, beside `rsvp`'s answer to someone
else's. It is sent to no one and invites no one, so it holds no ATTENDEE, and its ORGANIZER, which a PUBLISH must name (RFC 5546),
is the person whose calendar it goes in. What it says came from a stranger's mail by way of a model, so nothing in it may break
a line: `rsvp`'s escaping leaves a carriage return alone. Total: nothing here raises.
"""
import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from mailmind.calendar.domain.rsvp import _fold, _ics_dt, _ics_esc  # pyright: ignore[reportPrivateUsage]
from mailmind.kernel import proposed

DEFAULT_LENGTH = timedelta(hours=1)     # a time with no end is a calendar's zero-minute event otherwise


@dataclass(frozen=True)
class NewEvent:
    title: str
    start: str                          # a day or a time, as `kernel.proposed.read_moment` gave it
    end: str | None
    place: str


def uid_for(account: str, key: int, event: NewEvent) -> str:
    """The same event confirmed again is the same event to a calendar, which replaces it rather than listing it twice."""
    seed = "\0".join((account, str(key), event.title, event.start))
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32] + "@mailmind.local"


def _esc(text: str, cap: int) -> str:
    return _ics_esc(proposed.one_line(text, cap))


def _when(event: NewEvent) -> list[str]:
    if proposed.is_day(event.start):                # DTEND of a day is the day after the last, exclusive
        last = date.fromisoformat(event.end or event.start)
        return [f"DTSTART;VALUE=DATE:{event.start.replace('-', '')}",
                f"DTEND;VALUE=DATE:{(last + timedelta(days=1)).strftime('%Y%m%d')}"]
    end = event.end or (datetime.fromisoformat(event.start) + DEFAULT_LENGTH).isoformat(timespec="minutes")
    return ["DTSTART:" + _ics_dt(event.start), "DTEND:" + _ics_dt(end)]


def build(event: NewEvent, organizer: str, uid: str, stamp: datetime) -> str:
    """A METHOD:PUBLISH VCALENDAR holding `event`, for `organizer`'s own calendar."""
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//MailMind//Suggestion//EN", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
             "BEGIN:VEVENT", _fold("UID:" + uid), "DTSTAMP:" + stamp.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
             "SEQUENCE:0", _fold("SUMMARY:" + _esc(event.title, proposed.TITLE_MAX)), *_when(event)]
    if event.place:
        lines.append(_fold("LOCATION:" + _esc(event.place, proposed.PLACE_MAX)))
    lines.append(_fold("ORGANIZER:mailto:" + _esc(organizer, 320)))
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(lines) + "\r\n"

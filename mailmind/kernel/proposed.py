"""What someone proposes for a calendar or a to-do list: what the model read out of a message, or what a person typed over it.
Both are text to be read with care, and the Assistant, Calendar and Attention all read it, so the reading is here. Total:
nothing here raises.

A date is ISO 8601: a day (`YYYY-MM-DD`), or a time (`YYYY-MM-DDTHH:MM`) with its offset where it has one. One that won't read,
or falls outside the window around today (`BEHIND`, `AHEAD`), reads as None, which is how a date a model made up or mistook goes:
clamping it to the window's edge would put a wrong date in front of someone, so it is dropped instead.
"""
import re
from datetime import date, datetime, timedelta

BEHIND = timedelta(days=366)            # a message read late can still name last month's meeting, or a task overdue
AHEAD = timedelta(days=366 * 5)
LONGEST = timedelta(days=31)            # what an event may run to: past this it's a misread end
TITLE_MAX = 200
PLACE_MAX = 300

_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
_TIME = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}(?::?\d{2})?)?")
_BREAKS = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")      # what ends a line somewhere, and what no line should hold


def one_line(value: object, cap: int) -> str:
    """Text as one line, within `cap`. A calendar's lines are its structure, so nothing a sender wrote may break one."""
    if not isinstance(value, str):
        return ""
    return " ".join(_BREAKS.sub(" ", value).split())[:cap]


def _reading(text: object) -> date | datetime | None:
    if not isinstance(text, str):
        return None
    s = text.strip()
    try:
        if _DAY.fullmatch(s):
            return date.fromisoformat(s)
        if _TIME.fullmatch(s):
            return datetime.fromisoformat(s)
    except ValueError:
        pass
    return None


def _day_of(value: date | datetime) -> date:
    return value.date() if isinstance(value, datetime) else value


def _within(value: date | datetime, today: date) -> bool:
    return today - BEHIND <= _day_of(value) <= today + AHEAD


def read_moment(text: object, today: date) -> str | None:
    """A day, or a time with its offset where it has one, as ISO text, or None."""
    value = _reading(text)
    if value is None or not _within(value, today):
        return None
    return value.isoformat(timespec="minutes") if isinstance(value, datetime) else value.isoformat()


def read_date(text: object, today: date) -> str | None:
    """A day as ISO text, or None. A time reads as its day."""
    value = _reading(text)
    if value is None or not _within(value, today):
        return None
    return _day_of(value).isoformat()


def is_day(moment: str) -> bool:
    """Whether what `read_moment` gave is a whole day, rather than a time."""
    return _DAY.fullmatch(moment) is not None


def ends_after(start: str, end: str) -> bool:
    """Whether `end` can end an event that begins at `start`, both as `read_moment` gave them: of one kind, not before it (a
    day may end on itself, a time may not), and no more than LONGEST past it."""
    a, b = _reading(start), _reading(end)
    if a is None or b is None:
        return False
    if isinstance(a, datetime) and isinstance(b, datetime):
        return (a.tzinfo is None) == (b.tzinfo is None) and timedelta(0) < b - a <= LONGEST
    if isinstance(a, datetime) or isinstance(b, datetime):
        return False
    return timedelta(0) <= b - a <= LONGEST

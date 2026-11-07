"""Today & next: which invites make the list, and when. What a message says about an event, as a reader parsed it:
an update replaces the original, a cancellation removes it, and a message about one occurrence of a series
(RECURRENCE-ID) moves or cancels just that one."""
from collections.abc import Iterable
from datetime import datetime

from mailmind.calendar.domain.invite import Invite

SKIPPED_ROLES = ("trash", "junk")


def event_key(inv: Invite) -> tuple[str | tuple[str, str], str]:
    return (inv.get("uid") or (inv["summary"], inv["start_iso"]), inv.get("recurrence_id", ""))


def dead(inv: Invite) -> bool:
    return inv.get("method") == "CANCEL" or inv.get("status") == "CANCELLED"


def repeats(inv: Invite) -> bool:
    return bool(inv.get("rrule") or inv.get("rdates"))


def changed_occurrences(invites: Iterable[Invite]) -> tuple[dict[str, list[str]], set[str]]:
    """Per series: its rescheduled or cancelled occurrences, and the series that are gone."""
    moved: dict[str, list[str]] = {}
    gone: set[str] = set()
    for inv in invites:
        if inv.get("uid") and inv.get("recurrence_id"):
            moved.setdefault(inv["uid"], []).append(inv["recurrence_id"])
        elif inv.get("uid") and dead(inv):
            gone.add(inv["uid"])
    return moved, gone


def local_naive(d: datetime) -> datetime:
    """A parsed calendar time as local wall-clock time; a floating (naive) one is already that."""
    return d.astimezone().replace(tzinfo=None) if d.tzinfo else d

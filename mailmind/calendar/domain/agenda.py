"""Today & next: which invites make the list, and when. What a message says about an event, as a reader parsed it:
an update replaces the original, a cancellation removes it, and a message about one occurrence of a series
(RECURRENCE-ID) moves or cancels just that one, or with RANGE=THISANDFUTURE, that one and every one after it."""
from collections.abc import Iterable
from datetime import datetime

from mailmind.calendar.domain.invite import Invite, Span

SKIPPED_ROLES = ("trash", "junk")


def event_key(inv: Invite) -> tuple[str | tuple[str, str], str]:
    return (inv.get("uid") or (inv["summary"], inv["start_iso"]), inv.get("recurrence_id", ""))


def supersedes(inv: Invite, held: Invite) -> bool:
    """Whether what an older message says (`inv`) still beats what a newer one does: only with the later of the organizer's
    revisions. A message can come late, and a stale copy be sent again."""
    return inv.get("sequence", 0) > held.get("sequence", 0)


def dead(inv: Invite) -> bool:
    return inv.get("method") == "CANCEL" or inv.get("status") == "CANCELLED"


def repeats(inv: Invite) -> bool:
    return bool(inv.get("rrule") or inv.get("rdates"))


def first_date(inv: Invite) -> Invite:
    """The event as though it happened only once, on the date it starts."""
    return {**inv, "rrule": "", "rdates": []}


def changed_occurrences(invites: Iterable[Invite]) -> tuple[dict[str, list[str]], set[str]]:
    """Per series: its rescheduled or cancelled occurrences, and the series that are gone."""
    moved: dict[str, list[str]] = {}
    gone: set[str] = set()
    for inv in invites:
        if inv.get("uid") and inv.get("recurrence_id"):
            if not inv.get("this_and_future"):                   # that one is `cut_up`'s
                moved.setdefault(inv["uid"], []).append(inv["recurrence_id"])
        elif inv.get("uid") and dead(inv):
            gone.add(inv["uid"])
    return moved, gone


def _occurrence(iso: str) -> datetime | None:
    try:
        return local_naive(datetime.fromisoformat(iso))
    except (OverflowError, ValueError):                          # a date this machine's zone pushes past year 1 or 9999
        return None


def cut_up[T](found: list[tuple[T, Invite]]) -> list[tuple[T, Invite]]:
    """`found`, with each series a RANGE=THISANDFUTURE override changed cut where it did: the series stops before the first such
    occurrence, and each override goes on as the series from there to the next, in its own words, moved as far as it moved. One
    with no live series beside it stays what it is, a word about one date."""
    series = {inv["uid"]: inv for _t, inv in found if inv.get("uid") and not inv.get("recurrence_id") and repeats(inv) and not dead(inv)}
    said_at: dict[str, list[tuple[datetime, str]]] = {}
    for _t, inv in found:
        at = _occurrence(inv["recurrence_id"]) if inv.get("this_and_future") and inv.get("uid") in series else None
        if at:
            said_at.setdefault(inv["uid"], []).append((at, inv["recurrence_id"]))
    cuts = {uid: [said for _at, said in sorted(each)] for uid, each in said_at.items()}
    out: list[tuple[T, Invite]] = []
    for t, inv in found:
        uid, said = inv.get("uid"), inv.get("recurrence_id")
        if uid not in cuts or (said and said not in cuts[uid]):
            out.append((t, inv))
        elif not said:
            out.append((t, {**inv, "span": Span(before=cuts[uid][0])}))
        else:
            whole, later = series[uid], cuts[uid][cuts[uid].index(said) + 1:]
            out.append((t, {**inv, "rrule": whole["rrule"], "rdates": whole.get("rdates", []), "exdates": whole["exdates"],
                            "recurrence": whole["recurrence"], "tzid": whole["tzid"], "recurrence_id": "",
                            "span": Span(origin=whole["start_iso"], since=said, before=later[0] if later else "")}))
    return out


def local_naive(d: datetime) -> datetime:
    """A parsed calendar time as local wall-clock time; a floating (naive) one is already that."""
    return d.astimezone().replace(tzinfo=None) if d.tzinfo else d

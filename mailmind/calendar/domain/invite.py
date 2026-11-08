"""An invite as the reader of its text gives it, which agenda and rsvp read and the card shows. The reader finds an event's
`Fields`; `invite_of` says what they come to, the card's words included."""
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone
from typing import NotRequired, TypedDict
from zoneinfo import ZoneInfo

from mailmind.calendar.domain.windows_zones import WINDOWS_ZONES

_FREQ = {"DAILY": "day", "WEEKLY": "week", "MONTHLY": "month", "YEARLY": "year"}
_DAYS = {"MO": "Mon", "TU": "Tue", "WE": "Wed", "TH": "Thu", "FR": "Fri", "SA": "Sat", "SU": "Sun"}


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
    rdates: NotRequired[list[str]]          # only where the text has some


class Fields(TypedDict, total=False):
    """What the reader found in one event, each as it was said. A time is aware, or naive where the invite floats."""
    summary: str
    location: str
    description: str
    uid: str
    status: str
    rrule: str
    start: datetime | None
    end: datetime | None
    all_day: bool
    tzid: str
    organizer: Party
    attendees: list[Party]
    exdates: list[datetime | date]          # a date strikes that whole day from a timed series
    rdates: list[datetime]
    recurrence_id: datetime | None


def _zone_names(tzid: str) -> Iterator[str]:
    """What a TZID might call its zone: itself, or Windows' name for it. One opening with a slash, RFC 5545's mark of a name from a
    registry (`/vendor.example/Olson_2011/Europe/Berlin`), also by what ends it, which at most three parts can be."""
    yield WINDOWS_ZONES.get(tzid, tzid)
    if tzid.startswith("/"):
        path = tzid.split("/")[-3:]
        yield from ("/".join(path[at:]) for at in range(len(path) - 1))


def zone(tzid: str | None) -> ZoneInfo | None:
    """The zone a TZID names, in the zone database or as Windows calls it. Any other is no zone at all, whatever the invite's own
    VTIMEZONE says of it: the time it qualifies floats."""
    for name in _zone_names(tzid or ""):
        try:
            return ZoneInfo(name) if name else None
        except Exception:
            continue
    return None


def party(value: str | None, name: str) -> Party:
    email = (value or "").strip()
    if email.lower().startswith("mailto:"):
        email = email[7:]
    return {"name": name.strip(), "email": email.strip()}


def read_time(val: str | None, params: dict[str, str]) -> tuple[datetime | None, bool]:
    """-> (aware-or-naive datetime | None, all_day bool)."""
    v = (val or "").strip()
    if len(v) == 8 and v.isdigit():
        try:
            return datetime.strptime(v, "%Y%m%d"), True
        except Exception:
            return None, True
    z = v.endswith("Z")
    vv = v[:-1] if z else v
    d = None
    for fmt in ("%Y%m%dT%H%M", "%Y%m%dT%H%M%S"):     # this order: strptime would read 1030 as 10:03:00 with seconds to fill
        try:
            d = datetime.strptime(vv, fmt)
            break
        except Exception:
            pass
    if d is None:
        return None, False
    if z:
        d = d.replace(tzinfo=timezone.utc)
    else:
        tz = zone(params.get("TZID"))
        if tz:
            d = d.replace(tzinfo=tz)
    return d, False


def local(d: datetime) -> datetime:
    """Render an aware dt in the machine's local zone; a naive (floating) dt as-is."""
    try:
        return d.astimezone() if d.tzinfo else d
    except Exception:
        return d


def when(start: datetime | None, end: datetime | None, all_day: bool) -> str:
    if not start:
        return ""
    s = local(start)
    if all_day:
        return s.strftime("%a, %b ") + str(s.day)
    head = s.strftime("%a, %b ") + str(s.day) + " · " + s.strftime("%-I:%M %p")
    if end:
        e = local(end)
        if e.date() == s.date():
            return head + "–" + e.strftime("%-I:%M %p")
        return head + " → " + e.strftime("%a, %b ") + str(e.day) + " " + e.strftime("%-I:%M %p")
    return head


def rule_in_words(r: str | None) -> str:
    if not r:
        return ""
    kv: dict[str, str] = {}
    for p in r.split(";"):
        if "=" in p:
            k, v = p.split("=", 1)
            kv[k.upper()] = v
    freq = _FREQ.get(kv.get("FREQ", "").upper())
    if not freq:
        return ""
    try:
        n = int(kv.get("INTERVAL", "1"))
    except Exception:
        n = 1
    base = ("Every " + str(n) + " " + freq + "s") if n > 1 else ("Every " + freq)
    if kv.get("BYDAY"):
        days = ", ".join(_DAYS.get(x.strip()[-2:].upper(), x) for x in kv["BYDAY"].split(","))
        base += " on " + days
    if kv.get("COUNT"):
        base += " ×" + kv["COUNT"]
    elif kv.get("UNTIL"):
        d, _ = read_time(kv["UNTIL"], {})
        if d:
            base += " until " + local(d).strftime("%b %-d, %Y")
    return base


def length(start: datetime, end: datetime | None, all_day: bool) -> timedelta:
    """How long an event runs, an all-day one with no end a day. An end that floats where its start doesn't, or the other
    way about (a TZID only one of them could use), is read by its wall clock, as though in the start's terms."""
    if end is None:
        return timedelta(days=1 if all_day else 0)
    if bool(end.tzinfo) != bool(start.tzinfo):
        end = end.replace(tzinfo=start.tzinfo)
    return max(end - start, timedelta(0))


def rule_parts(r: str | None) -> dict[str, str]:
    kv: dict[str, str] = {}
    for p in (r or "").split(";"):
        if "=" in p:
            k, v = p.split("=", 1)
            kv[k.strip().upper()] = v.strip().upper()
    return kv


def until_bound(kv: dict[str, str], start: datetime) -> datetime | None:
    """UNTIL as a bound comparable with the event's own starts (inclusive), or None."""
    u, date_only = read_time(kv.get("UNTIL", ""), {})
    if u is None:
        return None
    if date_only:
        u = u.replace(hour=23, minute=59, second=59)
    if u.tzinfo and not start.tzinfo:
        return u.astimezone().replace(tzinfo=None)
    if start.tzinfo and not u.tzinfo:
        return u.replace(tzinfo=start.tzinfo)
    return u


def invite_of(e: Fields, method: str) -> Invite:
    start, end, all_day = e.get("start"), e.get("end"), bool(e.get("all_day"))
    recurrence_id = e.get("recurrence_id")
    inv: Invite = {
        "summary": e.get("summary", "(no title)"),
        "location": e.get("location", ""),
        "description": e.get("description", "")[:1500],
        "when": when(start, end, all_day),
        "all_day": all_day,
        "start_iso": (start.isoformat() if start else ""),
        "end_iso": (end.isoformat() if end else ""),
        "organizer": e.get("organizer") or {},
        "attendees": [a for a in e.get("attendees", []) if a.get("email")][:25],
        "recurrence": rule_in_words(e.get("rrule")),
        "method": method,
        "status": e.get("status", ""),
        "uid": e.get("uid", ""),
        "rrule": e.get("rrule", ""),
        "exdates": [d.isoformat() for d in e.get("exdates", [])],
        "recurrence_id": (recurrence_id.isoformat() if recurrence_id else ""),
        "tzid": e.get("tzid", ""),
    }
    if e.get("rdates"):
        inv["rdates"] = [d.isoformat() for d in e.get("rdates", [])]
    return inv

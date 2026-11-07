"""An invite's text read through `icalendar` and `recurring-ical-events`, for the new front door. The old one runs with
nothing installed and keeps `ics_reader`; `mailmind.bootstrap.calendar` picks. Nothing of either library leaves this
module: it answers the `IcsReader` port in the domain's own shapes.

An invite is a stranger's text and this runs on request threads, so nothing here raises, however broken the text, and
each call has a budget of this thread's time to answer in. A call that uses it up answers as for a text or a rule it
can't read. Four things the libraries would do with that text are not done:

- `_Parser` doesn't let `icalendar` act on a VTIMEZONE, which would remember a zone for the whole process, under a name the
  sender chose, made from rules it expands through to year 9999 however often they fire.
- It doesn't let it look up a TZID either. One rule says what a TZID means, `invite.zone`, as it does for `ics_reader`:
  a name the zone database doesn't know makes the time floating.
- It doesn't let it decode the escapes of what isn't text (`_AS_SAID`). An RSVP echoes a UID and writes to an organizer, and
  a stranger's `\\n` there would be a line of their own in the reply.
- `Component.from_ical` opens a text with no line break in it as a path, if a file is there. The parser doesn't.
"""
import sys
import time
from collections.abc import Callable, Iterator
from datetime import date, datetime, time as day_time, timedelta, timezone
from typing import Any

import recurring_ical_events
from icalendar import Calendar, Component, Event, vDDDLists, vDDDTypes, vRecur, vText
from icalendar.cal import ComponentFactory
from icalendar.parser.ical.component import ComponentIcalParser

from mailmind.calendar.domain.agenda import local_naive
from mailmind.calendar.domain.invite import Fields, Invite, invite_of, length, party, read_time, rule_parts, until_bound, zone

_READ_SECONDS = 0.2
_EXPAND_SECONDS = 0.1
_MAX_TEXT = 1_000_000                       # characters: the library unfolds and splits lines where nothing can stop it
_MAX_EVENTS = 200                           # of one text: the Agenda reads all of them on every request
_EXPANDS = {"DAILY", "WEEKLY", "MONTHLY", "YEARLY"}     # anything finer: no date a person would want, and a CPU bomb
_DAY = timedelta(days=1)
_READING = ("dateutil.", "icalendar", "recurring_ical_events", "x_wr_timezone", __name__)
_AS_SAID = {"RRULE", "UID", "ORGANIZER", "ATTENDEE"}


class _Spent(BaseException):
    """Out of time. Not an Exception, so nothing in a library can take it for a bad value and go on."""


def _within(seconds: float, otherwise: Any, work: Callable[..., Any], *args: Any) -> Any:
    """`work`'s answer, or `otherwise` if it raises or spends `seconds` of this thread's CPU time. A rule can walk to year
    9999 with no occurrence to show for it, inside one `next()` of dateutil's generator, so counting occurrences can't
    stop it. The check runs at each Python call, which dateutil makes at least once for each period of a rule. CPU time,
    since a busy machine is no fault of this call's. It raises only in a frame of the reading itself: a garbage collector's
    callback or a `__del__` may run at any call, and what's raised there is dropped, and takes the tracer with it."""
    limit = time.thread_time() + seconds

    def check(frame: Any, _event: str, _arg: Any) -> None:
        if time.thread_time() > limit and frame.f_globals.get("__name__", "").startswith(_READING):
            raise _Spent

    before = sys.gettrace()
    sys.settrace(check)
    try:
        return work(*args)
    except (_Spent, Exception):
        return otherwise
    finally:
        sys.settrace(before)


class _Said(vText):
    """A value as its line said it, its escapes and all."""

    @classmethod
    def get_value_from_content_line(cls, line: Any) -> str:
        return line.raw_parts()[2]


class _Parser(ComponentIcalParser):
    """The library's parser, with what the module's docstring says it doesn't do left out. As lenient as `ics_reader`
    otherwise: what it can't read in a calendar it leaves out, and goes on."""

    def handle_begin_component(self, vals: str) -> None:
        super().handle_begin_component(vals)
        begun: Any = self._stack[-1]
        begun.ignore_exceptions = True              # in any component: a VALARM's bad TRIGGER is no reason to lose the event

    def handle_end_component(self, vals: str) -> None:
        if vals.upper() != "VTIMEZONE":
            super().handle_end_component(vals)
        elif self._stack:
            self._stack.pop()
        else:
            raise ValueError("END with no BEGIN")

    def parse_and_add_property(self, name: str, params: Any, val: str, tzid: str | None, line: Any) -> None:
        super().parse_and_add_property(name, params, val, None, line)   # the wall clock, and `params` keep the TZID

    def get_factory_for_property(self, name: str, params: Any) -> Any:
        return _Said if name in _AS_SAID else super().get_factory_for_property(name, params)

    def handle_line_parse_error(self, exception: Exception) -> None:
        pass

    def handle_property(self, name: str, params: Any, vals: str, line: Any) -> None:
        if self.component:
            super().handle_property(name, params, vals, line)

    def prepare_components(self) -> None:
        if self._stack:                             # a text cut short: what it did finish
            self._components.append(self._stack[0])


_FACTORY = ComponentFactory()


def _each(component: Any, name: str) -> list[Any]:
    value = component.get(name)
    return value if isinstance(value, list) else [] if value is None else [value]


def _last(component: Any, name: str) -> Any:
    """A property said more than once: the last, as ics_reader takes it."""
    each = _each(component, name)
    return each[-1] if each else None


def _param(prop: Any, name: str) -> str:
    """On one line: RFC 6868's `^n` can say a line break, and a name goes on to an RSVP's headers."""
    value = prop.params.get(name, "")
    return " ".join(str(value[0] if isinstance(value, list) and value else value).splitlines())


def _params(prop: Any) -> dict[str, str]:
    return {str(name): _param(prop, name) for name in prop.params}


def _zoned(value: datetime, tzid: str) -> datetime:
    tz = zone(tzid) if value.tzinfo is None else None
    return value.replace(tzinfo=tz) if tz else value


def _time(prop: Any) -> tuple[datetime | None, bool]:
    """-> (aware-or-naive datetime | None, all_day bool). What the library wouldn't read goes to read_time."""
    if not isinstance(prop, vDDDTypes):
        return read_time(str(prop), _params(prop))
    value = prop.dt
    if isinstance(value, datetime):
        return _zoned(value, _param(prop, "TZID")), False
    if isinstance(value, date):
        return datetime.combine(value, day_time()), True
    return None, False


def _times(component: Any, name: str) -> Iterator[datetime | date]:
    """Each value of each EXDATE or RDATE line: a date for a whole day, the start of a period for a period."""
    for line in _each(component, name):
        if not isinstance(line, vDDDLists):             # one bad value: the rest of that line still counts
            for piece in str(line).split(","):
                d, whole_day = read_time(piece, _params(line))
                if d:
                    yield d.date() if whole_day else d
            continue
        for each in line.dts:
            value = each.dt[0] if isinstance(each.dt, tuple) else each.dt
            if isinstance(value, datetime):
                yield _zoned(value, _param(line, "TZID"))
            elif isinstance(value, date):
                yield value


def _midnight(d: datetime | date) -> datetime:
    return d if isinstance(d, datetime) else datetime.combine(d, day_time())


def _after(start: datetime, duration: timedelta) -> datetime | None:
    try:
        return start + duration
    except OverflowError:
        return None


def _fields(ev: Any) -> Fields:
    e = Fields(attendees=[party(str(a), _param(a, "CN")) for a in _each(ev, "ATTENDEE")])
    if (summary := _last(ev, "SUMMARY")) is not None:
        e["summary"] = str(summary).strip()
    if (location := _last(ev, "LOCATION")) is not None:
        e["location"] = str(location).strip()
    if (description := _last(ev, "DESCRIPTION")) is not None:
        e["description"] = str(description).strip()
    if (uid := _last(ev, "UID")) is not None:
        e["uid"] = str(uid).strip()
    if (status := _last(ev, "STATUS")) is not None:
        e["status"] = str(status).strip().upper()
    if (rule := _last(ev, "RRULE")) is not None:
        e["rrule"] = str(rule).strip()
    start = None
    if (dtstart := _last(ev, "DTSTART")) is not None:
        start, e["all_day"] = _time(dtstart)
        e["start"], e["tzid"] = start, _param(dtstart, "TZID") if start and start.tzinfo else ""
    if (dtend := _last(ev, "DTEND")) is not None:
        e["end"] = _time(dtend)[0]
    elif start and isinstance(duration := _last(ev, "DURATION"), vDDDTypes) and isinstance(duration.dt, timedelta):
        e["end"] = _after(start, duration.dt)
    if (organizer := _last(ev, "ORGANIZER")) is not None:
        e["organizer"] = party(str(organizer), _param(organizer, "CN"))
    if (recurrence_id := _last(ev, "RECURRENCE-ID")) is not None:
        e["recurrence_id"] = _time(recurrence_id)[0]
    # A whole day struck from a timed series stays a date, which says so; from an all-day one it's that day's midnight.
    e["exdates"] = [_midnight(d) if e.get("all_day") else d for d in _times(ev, "EXDATE")]
    if rdates := [_midnight(d) for d in _times(ev, "RDATE")]:
        e["rdates"] = rdates
    return e


def _read(text: str) -> list[Invite]:
    found: list[Invite] = []
    lines = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    for top in _Parser(lines, _FACTORY, Component.types_factory).parse():
        method = _last(top, "METHOD")
        said = str(method).strip().upper() if method is not None else ""
        for ev in top.walk("VEVENT"):
            if len(found) == _MAX_EVENTS:
                return found
            found.append(invite_of(_fields(ev), said))
    return found


def events(text: str) -> list[Invite]:
    """Every event the text holds, up to `_MAX_EVENTS`. [] for a text that isn't a calendar."""
    return [] if len(text or "") > _MAX_TEXT else _within(_READ_SECONDS, [], _read, text or "")


def parse(text: str) -> Invite | None:
    """The event a card shows: the series itself where the text holds one, else its first."""
    found = events(text)
    return next((e for e in found if not e["recurrence_id"]), found[0]) if found else None


def _rule(kv: dict[str, str], start: datetime) -> vRecur:
    """The rule the library is to expand. Raises for one it isn't to. dateutil reads a date-only UNTIL as that midnight
    and drops that day's occurrence, and refuses a floating one on a zoned series, so it gets the bound this means."""
    if kv.get("FREQ") not in _EXPANDS:
        raise ValueError("not a frequency this expands")
    parts = {**kv, "INTERVAL": str(max(1, int(kv.get("INTERVAL", "1"))))}     # below 1, dateutil would stand still for ever
    parts.pop("UNTIL", None)
    if not kv.get("COUNT", "").isdigit():                                     # says nothing, as to ics_reader
        parts.pop("COUNT", None)
    until = until_bound(kv, start)
    if until is not None:
        parts["UNTIL"] = until.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ") if until.tzinfo else until.strftime("%Y%m%dT%H%M%S")
    return vRecur.from_ical(";".join(f"{k}={v}" for k, v in parts.items()))


def _series(inv: Invite, start: datetime, kv: dict[str, str]) -> Any:
    ev = Event()
    ev.add("UID", "series")
    ev.add("DTSTART", start.date() if inv.get("all_day") else start)
    if kv:
        ev["RRULE"] = vRecur(_rule(kv, start))
    for d in inv.get("rdates") or []:
        rdate = datetime.fromisoformat(d)
        ev.add("RDATE", rdate.date() if inv.get("all_day") else rdate)
    cal = Calendar()
    cal.add_component(ev)
    return recurring_ical_events.of(cal)


def _next(inv: Invite, after: datetime) -> tuple[datetime, datetime] | None:
    start = datetime.fromisoformat(inv["start_iso"])
    end = datetime.fromisoformat(inv["end_iso"]) if inv.get("end_iso") else None
    tz = zone(inv.get("tzid")) if start.tzinfo else None
    if tz:                                  # back into its zone: DST moves wall-clock time
        start = start.astimezone(tz)
    dur = length(start, end, inv.get("all_day", False))
    now = local_naive(after)
    kv = rule_parts(inv.get("rrule"))
    if not kv and not inv.get("rdates"):
        return None if local_naive(start + dur) <= now else (start, start + dur)
    whole_days = {x for x in inv.get("exdates") or [] if len(x) == 10}
    struck = set()
    for x in inv.get("exdates") or []:
        try:
            struck.add(local_naive(datetime.fromisoformat(x)))
        except ValueError:
            pass
    since = after.astimezone(start.tzinfo) if start.tzinfo else now
    try:
        since -= dur + _DAY                 # a day over: at a change of clocks, an hour may be missing from `dur`
    except OverflowError:
        since = start
    for occurrence in _series(inv, start, kv).after(since):
        s = occurrence["DTSTART"].dt
        s = s if isinstance(s, datetime) else datetime.combine(s, day_time())
        if local_naive(s) in struck or s.date().isoformat() in whole_days \
                or (inv.get("all_day") and any(x.date() == s.date() for x in struck)):
            continue
        if local_naive(s + dur) > now:
            return s, s + dur
    return None


def next_occurrence(inv: Invite, after: datetime) -> tuple[datetime, datetime] | None:
    """(start, end) of the first occurrence of a parsed invite that hasn't ended by `after`, in the invite's own terms
    (aware in its zone, or floating). A one-off event is its only occurrence. None when every occurrence has ended, or
    for a rule this doesn't expand: one it can't read, finer than a day, or one that runs out of time."""
    return _within(_EXPAND_SECONDS, None, _next, inv, after)

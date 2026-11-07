"""Minimal iCalendar (RFC 5545) reader — stdlib only. Parses a text/calendar invite into a
display-ready dict for the reader's Invite card. Read-only; no network. Total — never raises.

  events(text)  ->  [parse(text)], or [] where that is None
  inv = parse(text)  ->  None, or
        {"summary","location","description","when","all_day","start_iso","end_iso",
         "organizer":{name,email}, "attendees":[{name,email}], "recurrence","method","status",
         "uid","rrule","exdates","recurrence_id","tzid"}
  next_occurrence(inv, after)  ->  (start, end) of the first occurrence still running at `after`
"""
import calendar
from datetime import datetime, timedelta

from mailmind.calendar.domain.invite import Fields, Invite, invite_of, party, read_time

try:
    from zoneinfo import ZoneInfo
except Exception:                       # pragma: no cover
    ZoneInfo = None

_FREQ = {"DAILY", "WEEKLY", "MONTHLY", "YEARLY"}
_WDAY = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


def _unfold(text):
    out = []
    for line in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line[:1] in (" ", "\t") and out:
            out[-1] += line[1:]          # RFC5545 line folding: continuation
        else:
            out.append(line)
    return out


def _split(line):
    i = line.find(":")
    if i < 0:
        return None, {}, ""
    head, val = line[:i], line[i + 1:]
    parts = head.split(";")
    params = {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            params[k.strip().upper()] = v.strip().strip('"')
    return parts[0].strip().upper(), params, val


def _uesc(v):
    return (v or "").replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\").strip()


def parse(text: str) -> Invite | None:
    lines = _unfold(text)
    method, in_ev = "", False
    ev: Fields | None = None
    events = []
    for ln in lines:
        name, params, val = _split(ln)
        if not name:
            continue
        up = val.strip().upper()
        if name == "METHOD":
            method = up
        elif name == "BEGIN" and up == "VEVENT":
            ev, in_ev = Fields(attendees=[]), True
        elif name == "END" and up == "VEVENT":
            if ev:
                events.append(ev)
            ev, in_ev = None, False
        elif in_ev and ev is not None:
            if name == "SUMMARY":
                ev["summary"] = _uesc(val)
            elif name == "LOCATION":
                ev["location"] = _uesc(val)
            elif name == "DESCRIPTION":
                ev["description"] = _uesc(val)
            elif name == "UID":
                ev["uid"] = val.strip()
            elif name == "STATUS":
                ev["status"] = up
            elif name == "RRULE":
                ev["rrule"] = val.strip()
            elif name == "EXDATE":
                ev.setdefault("exdates", []).extend(
                    d for d, _ in (read_time(v, params) for v in val.split(",")) if d)
            elif name == "RECURRENCE-ID":
                ev["recurrence_id"], _ = read_time(val, params)
            elif name == "DTSTART":
                start, ev["all_day"] = read_time(val, params)
                ev["start"], ev["tzid"] = start, params.get("TZID", "") if start and start.tzinfo else ""
            elif name == "DTEND":
                ev["end"], _ = read_time(val, params)
            elif name == "ORGANIZER":
                ev["organizer"] = party(val, params.get("CN", ""))
            elif name == "ATTENDEE":
                ev.setdefault("attendees", []).append(party(val, params.get("CN", "")))
    if not events:
        return None
    return invite_of(events[0], method)


def events(text: str) -> list[Invite]:
    """This reader reads only the first."""
    inv = parse(text)
    return [inv] if inv else []


# ------------------------------------------------------------- recurrence (RRULE)
_KNOWN = {"FREQ", "INTERVAL", "COUNT", "UNTIL", "BYDAY", "BYMONTHDAY", "WKST"}
_PERIODS = 20000                         # days, weeks, months or years walked before giving up


def _naive_local(d):
    return d.astimezone().replace(tzinfo=None) if d.tzinfo else d


def _rule(r):
    kv = {}
    for p in (r or "").split(";"):
        if "=" in p:
            k, v = p.split("=", 1)
            kv[k.strip().upper()] = v.strip().upper()
    return kv


def _month_days(kv, year, month, start):
    """The days of one month a MONTHLY rule picks, ascending. Raises ValueError/KeyError on a bad rule."""
    last = calendar.monthrange(year, month)[1]
    if kv.get("BYMONTHDAY"):
        days = (int(v) for v in kv["BYMONTHDAY"].split(","))
        return sorted({d if d > 0 else last + 1 + d for d in days} & set(range(1, last + 1)))
    if kv.get("BYDAY"):
        out = set()
        for v in kv["BYDAY"].split(","):
            n, wd = int(v[:-2] or 0), _WDAY[v[-2:]]
            hits = [d for d in range(1, last + 1) if calendar.weekday(year, month, d) == wd]
            if not n:
                out.update(hits)
            elif n <= len(hits) and -n <= len(hits):
                out.add(hits[n - 1 if n > 0 else n])
        return sorted(out)
    return [start.day] if start.day <= last else []


def _occurrences(start, kv):
    """Rule-generated starts from `start` on, in order, before COUNT/UNTIL/EXDATE apply. None for a
    rule this reader doesn't expand (BYSETPOS, BYMONTH, BYHOUR, ordinal BYDAY outside MONTHLY ...)."""
    freq = kv.get("FREQ")
    if freq not in _FREQ or set(kv) - _KNOWN or (kv.get("BYMONTHDAY") and freq != "MONTHLY") \
            or (kv.get("BYDAY") and freq == "YEARLY"):
        return None
    try:
        step = max(1, int(kv.get("INTERVAL", "1")))
        if freq == "MONTHLY":
            _month_days(kv, start.year, start.month, start)     # rejects a BYDAY/BYMONTHDAY it can't read
            wdays = None
        else:
            wdays = sorted(_WDAY[v] for v in kv["BYDAY"].split(",")) if kv.get("BYDAY") else None
    except (KeyError, ValueError):
        return None
    week0 = start.date() - timedelta(days=start.weekday())

    def days():
        for k in range(_PERIODS):
            if freq == "DAILY":
                d = start.date() + timedelta(days=k * step)
                if wdays is None or d.weekday() in wdays:
                    yield d
            elif freq == "WEEKLY":
                for wd in wdays or [start.weekday()]:
                    yield week0 + timedelta(weeks=k * step, days=wd)
            elif freq == "MONTHLY":
                m = start.month - 1 + k * step
                y, mo = start.year + m // 12, m % 12 + 1
                for d in _month_days(kv, y, mo, start):
                    yield start.date().replace(year=y, month=mo, day=d)
            elif start.month != 2 or start.day != 29 or calendar.isleap(start.year + k * step):
                yield start.date().replace(year=start.year + k * step)
    at = start.timetz()
    return (datetime.combine(d, at) for d in days() if d >= start.date())


def _until(kv, start):
    """UNTIL as a bound comparable with the event's own starts (inclusive), or None."""
    u, date_only = read_time(kv.get("UNTIL", ""), {})
    if u is None:
        return None
    if date_only:
        u = u.replace(hour=23, minute=59, second=59)
    if u.tzinfo and not start.tzinfo:
        return _naive_local(u)
    if start.tzinfo and not u.tzinfo:
        return u.replace(tzinfo=start.tzinfo)
    return u


def next_occurrence(inv: Invite, after: datetime) -> tuple[datetime, datetime] | None:
    """(start, end) of the first occurrence of a parsed invite that hasn't ended by `after`, in the
    invite's own terms (aware in its zone, or floating). A one-off event is its only occurrence.
    None when every occurrence has ended, or for an RRULE this reader doesn't expand."""
    try:
        start = datetime.fromisoformat(inv["start_iso"])
        end = datetime.fromisoformat(inv["end_iso"]) if inv.get("end_iso") else None
        if inv.get("tzid") and start.tzinfo and ZoneInfo:   # back into its zone: DST moves wall-clock time
            start = start.astimezone(ZoneInfo(inv["tzid"]))
    except Exception:
        return None
    dur = max((end - start) if end else timedelta(days=1 if inv.get("all_day") else 0), timedelta(0))
    now = _naive_local(after)
    kv = _rule(inv.get("rrule"))
    if not kv:
        return None if _naive_local(start + dur) <= now else (start, start + dur)
    occ = _occurrences(start, kv)
    if occ is None:
        return None
    until = _until(kv, start) if kv.get("UNTIL") else None
    count = int(kv["COUNT"]) if kv.get("COUNT", "").isdigit() else None
    skip = set()
    for x in inv.get("exdates") or []:
        try:
            skip.add(_naive_local(datetime.fromisoformat(x)))
        except ValueError:
            pass
    try:
        for i, s in enumerate(occ):
            if (count is not None and i >= count) or (until is not None and s > until):
                return None
            if _naive_local(s) in skip or (inv.get("all_day") and any(x.date() == s.date() for x in skip)):
                continue
            if _naive_local(s + dur) > now:
                return s, s + dur
    except (ValueError, OverflowError):      # walked past year 9999
        pass
    return None

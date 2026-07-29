"""Minimal iCalendar (RFC 5545) reader — stdlib only. Parses a text/calendar invite into a
display-ready dict for the reader's Invite card. Read-only; no network. Total — never raises.

  inv = ics.parse(text)  ->  None, or
        {"summary","location","description","when","all_day","start_iso","end_iso",
         "organizer":{name,email}, "attendees":[{name,email}], "recurrence","method","status"}
"""
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
except Exception:                       # pragma: no cover
    ZoneInfo = None

_FREQ = {"DAILY": "day", "WEEKLY": "week", "MONTHLY": "month", "YEARLY": "year"}
_DAYS = {"MO": "Mon", "TU": "Tue", "WE": "Wed", "TH": "Thu", "FR": "Fri", "SA": "Sat", "SU": "Sun"}


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


def _addr(val, params):
    email = (val or "").strip()
    if email.lower().startswith("mailto:"):
        email = email[7:]
    return {"name": params.get("CN", "").strip(), "email": email.strip()}


def _parse_dt(val, params):
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
    for fmt in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M"):
        try:
            d = datetime.strptime(vv, fmt)
            break
        except Exception:
            pass
    if d is None:
        return None, False
    if z:
        d = d.replace(tzinfo=timezone.utc)
    elif params.get("TZID") and ZoneInfo:
        try:
            d = d.replace(tzinfo=ZoneInfo(params["TZID"]))
        except Exception:
            pass
    return d, False


def _local(d):
    """Render an aware dt in the machine's local zone; a naive (floating) dt as-is."""
    try:
        return d.astimezone() if d.tzinfo else d
    except Exception:
        return d


def _when(start, end, all_day):
    if not start:
        return ""
    s = _local(start)
    if all_day:
        return s.strftime("%a, %b ") + str(s.day)
    head = s.strftime("%a, %b ") + str(s.day) + " · " + s.strftime("%-I:%M %p")
    if end:
        e = _local(end)
        if e.date() == s.date():
            return head + "–" + e.strftime("%-I:%M %p")
        return head + " → " + e.strftime("%a, %b ") + str(e.day) + " " + e.strftime("%-I:%M %p")
    return head


def _rrule_human(r):
    if not r:
        return ""
    kv = {}
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
        d, _ = _parse_dt(kv["UNTIL"], {})
        if d:
            base += " until " + _local(d).strftime("%b %-d, %Y")
    return base


def parse(text):
    lines = _unfold(text)
    method, ev, in_ev = "", None, False
    events = []
    for ln in lines:
        name, params, val = _split(ln)
        if not name:
            continue
        up = val.strip().upper()
        if name == "METHOD":
            method = up
        elif name == "BEGIN" and up == "VEVENT":
            ev, in_ev = {"attendees": []}, True
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
                ev["description"] = _uesc(val)[:1500]
            elif name == "UID":
                ev["uid"] = val.strip()
            elif name == "STATUS":
                ev["status"] = up
            elif name == "RRULE":
                ev["rrule"] = val.strip()
            elif name == "DTSTART":
                ev["start"], ev["all_day"] = _parse_dt(val, params)
            elif name == "DTEND":
                ev["end"], _ = _parse_dt(val, params)
            elif name == "ORGANIZER":
                ev["organizer"] = _addr(val, params)
            elif name == "ATTENDEE":
                ev["attendees"].append(_addr(val, params))
    if not events:
        return None
    e = events[0]
    start, end, all_day = e.get("start"), e.get("end"), bool(e.get("all_day"))
    return {
        "summary": e.get("summary", "(no title)"),
        "location": e.get("location", ""),
        "description": e.get("description", ""),
        "when": _when(start, end, all_day),
        "all_day": all_day,
        "start_iso": (start.isoformat() if start else ""),
        "end_iso": (end.isoformat() if end else ""),
        "organizer": e.get("organizer") or {},
        "attendees": [a for a in e.get("attendees", []) if a.get("email")][:25],
        "recurrence": _rrule_human(e.get("rrule")),
        "method": method,
        "status": e.get("status", ""),
        "uid": e.get("uid", ""),
    }


# --------------------------------------------------------- iMIP RSVP (METHOD:REPLY)
def _ics_esc(v):
    return (v or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line):
    """RFC5545 content line folding: lines SHOULD be <=75 octets; continuation = CRLF + SPACE.
    Folds on UTF-8 byte boundaries so a multibyte char is never split."""
    b = line.encode("utf-8")
    if len(b) <= 73:
        return line
    out = []
    while len(b) > 73:
        cut = 73
        while cut > 0 and (b[cut] & 0xC0) == 0x80:   # don't split a continuation byte
            cut -= 1
        if cut == 0:
            cut = 73
        out.append(b[:cut].decode("utf-8", "ignore"))
        b = b[cut:]
    out.append(b.decode("utf-8", "ignore"))
    return "\r\n ".join(out)


def _ics_dt(iso):
    """ISO datetime -> iCal form. Aware -> UTC basic 'YYYYMMDDTHHMMSSZ'; floating -> no Z. '' if unparseable."""
    try:
        d = datetime.fromisoformat(iso)
    except Exception:
        return ""
    if d.tzinfo:
        return d.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return d.strftime("%Y%m%dT%H%M%S")


def build_reply(invite, attendee_email, attendee_name, partstat):
    """Build an iMIP METHOD:REPLY VCALENDAR responding to a parsed invite, for one attendee.
    partstat in {ACCEPTED, DECLINED, TENTATIVE}. Echoes UID/ORGANIZER/DTSTART so strict
    servers (Google, Exchange) can match the response to the original event. Total — never raises."""
    org = (invite.get("organizer") or {})
    uid = (invite.get("uid") or "").strip()
    dtstamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    L = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//MailMind//iMIP//EN",
         "CALSCALE:GREGORIAN", "METHOD:REPLY", "BEGIN:VEVENT"]
    if uid:
        L.append(_fold("UID:" + uid))
    L.append("DTSTAMP:" + dtstamp)
    L.append("SEQUENCE:0")
    if invite.get("summary"):
        L.append(_fold("SUMMARY:" + _ics_esc(invite["summary"])))
    if invite.get("all_day"):
        d = (invite.get("start_iso") or "")[:10].replace("-", "")
        if len(d) == 8:
            L.append("DTSTART;VALUE=DATE:" + d)
    else:
        ds = _ics_dt(invite.get("start_iso") or "")
        if ds:
            L.append("DTSTART:" + ds)
        de = _ics_dt(invite.get("end_iso") or "")
        if de:
            L.append("DTEND:" + de)
    if org.get("email"):
        cn = (';CN="%s"' % _ics_esc(org["name"])) if org.get("name") else ""
        L.append(_fold("ORGANIZER%s:mailto:%s" % (cn, org["email"])))
    acn = (';CN="%s"' % _ics_esc(attendee_name)) if attendee_name else ""
    L.append(_fold("ATTENDEE%s;PARTSTAT=%s:mailto:%s" % (acn, partstat, attendee_email)))
    L += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(L) + "\r\n"

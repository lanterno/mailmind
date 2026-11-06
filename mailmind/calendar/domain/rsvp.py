"""An iMIP RSVP (METHOD:REPLY): what it says and how it's keyed. Total: nothing here raises."""
from datetime import datetime, timezone

PARTSTAT = {"accept": "ACCEPTED", "decline": "DECLINED", "tentative": "TENTATIVE"}
VERB = {"accept": "Accepted", "decline": "Declined", "tentative": "Tentatively accepted"}


def name_for(invite, me_email, fallback):
    """The CN the organizer used for me, if any."""
    for a in invite.get("attendees", []):
        if (a.get("email") or "").lower() == me_email.lower() and a.get("name"):
            return a["name"]
    return fallback


def key_prefix(account, invite):
    return f"rsvp:{account}:{invite}:"


def key(prefix, sent, partstat):
    """The same answer again, a double click or a retry, finds the send already queued for it. A changed mind is a new
    send, even back to an earlier answer, since that one went out long ago. `sent` is the keys so far, newest first."""
    if sent and sent[0].rsplit(":", 2)[1] == partstat:
        return sent[0]
    return f"{prefix}{partstat}:{len(sent)}"


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

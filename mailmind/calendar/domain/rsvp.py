"""An iMIP RSVP (METHOD:REPLY): what it says and how it's keyed. Total: nothing here raises."""
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Literal

from mailmind.calendar.domain.invite import Invite, Party

# What a person may answer an invite: a request's schema names it so, and each table holds every one.
type RsvpDecision = Literal["accept", "decline", "tentative"]
PARTSTAT: dict[RsvpDecision, str] = {"accept": "ACCEPTED", "decline": "DECLINED", "tentative": "TENTATIVE"}
VERB: dict[RsvpDecision, str] = {"accept": "Accepted", "decline": "Declined", "tentative": "Tentatively accepted"}


def name_for(invite: Invite, me_email: str, fallback: str) -> str:
    """The CN the organizer used for me, if any."""
    for a in invite.get("attendees", []):
        name = a.get("name")
        if (a.get("email") or "").lower() == me_email.lower() and name:
            return name
    return fallback


def key_prefix(account: str, invite: str | int) -> str:
    return f"rsvp:{account}:{invite}:"


def key(prefix: str, sent: Sequence[str], partstat: str) -> str:
    """The same answer again, a double click or a retry, finds the send already queued for it. A changed mind is a new
    send, even back to an earlier answer, since that one went out long ago. `sent` is the keys so far, newest first."""
    if sent and sent[0].rsplit(":", 2)[1] == partstat:
        return sent[0]
    return f"{prefix}{partstat}:{len(sent)}"


def _ics_esc(v: str | None) -> str:
    return (v or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _cn(name: str | None) -> str:
    """A name as a parameter, in quotes: one in the name would end them, and RFC 6868's `^'` says it instead."""
    return ';CN="%s"' % _ics_esc(name).replace('"', "^'") if name else ""


def _fold(line: str) -> str:
    """RFC5545 content line folding: lines SHOULD be <=75 octets; continuation = CRLF + SPACE.
    Folds on UTF-8 byte boundaries so a multibyte char is never split."""
    b = line.encode("utf-8")
    if len(b) <= 73:
        return line
    out: list[str] = []
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


def _ics_dt(iso: str) -> str:
    """ISO datetime -> iCal form. Aware -> UTC basic 'YYYYMMDDTHHMMSSZ'; floating -> no Z. '' if unparseable, or at the very
    edge of what a datetime holds, where UTC is past it."""
    try:
        d = datetime.fromisoformat(iso)
        if d.tzinfo:
            return d.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    except Exception:
        return ""
    return d.strftime("%Y%m%dT%H%M%S")


def build_reply(invite: Invite, attendee_email: str, attendee_name: str | None, partstat: str) -> str:
    """Build an iMIP METHOD:REPLY VCALENDAR responding to a parsed invite, for one attendee.
    partstat in {ACCEPTED, DECLINED, TENTATIVE}. Echoes UID/ORGANIZER/DTSTART so strict
    servers (Google, Exchange) can match the response to the original event. Total — never raises."""
    org = invite.get("organizer") or Party()
    uid = (invite.get("uid") or "").strip()
    dtstamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//MailMind//iMIP//EN",
         "CALSCALE:GREGORIAN", "METHOD:REPLY", "BEGIN:VEVENT"]
    if uid:
        lines.append(_fold("UID:" + uid))
    lines.append("DTSTAMP:" + dtstamp)
    lines.append("SEQUENCE:0")
    if invite.get("summary"):
        lines.append(_fold("SUMMARY:" + _ics_esc(invite["summary"])))
    if invite.get("all_day"):
        d = (invite.get("start_iso") or "")[:10].replace("-", "")
        if len(d) == 8:
            lines.append("DTSTART;VALUE=DATE:" + d)
    else:
        ds = _ics_dt(invite.get("start_iso") or "")
        if ds:
            lines.append("DTSTART:" + ds)
        de = _ics_dt(invite.get("end_iso") or "")
        if de:
            lines.append("DTEND:" + de)
    org_email, org_name = org.get("email"), org.get("name")
    if org_email:
        lines.append(_fold("ORGANIZER%s:mailto:%s" % (_cn(org_name), org_email)))
    lines.append(_fold("ATTENDEE%s;PARTSTAT=%s:mailto:%s" % (_cn(attendee_name), partstat, attendee_email)))
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(lines) + "\r\n"

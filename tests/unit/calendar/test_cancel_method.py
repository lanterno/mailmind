"""A cancellation that says so only in its METHOD. Plenty of calendars send METHOD:CANCEL and leave STATUS out, so the METHOD
alone has to end the event, as STATUS:CANCELLED alone does. test_upcoming.py has the rest of Today & next.
"""
from datetime import datetime, timedelta
from email.message import EmailMessage

import pytest

from mailmind.bootstrap import calendar
from mailmind.bootstrap.calendar import ics
from mailmind.bootstrap.mailbox import store
from mailmind.calendar.domain import agenda
from mailmind.content import mime

ACCT = "ada@example.test"
NOW = datetime(2026, 10, 30, 12, 0)
TOMORROW = NOW + timedelta(days=1)


def event(uid, start, method="REQUEST", status="", extra=""):
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"METHOD:{method}", "BEGIN:VEVENT", f"UID:{uid}", "SUMMARY:Studio sync",
             f"DTSTART:{start:%Y%m%dT%H%M%S}", f"DTEND:{start + timedelta(minutes=30):%Y%m%dT%H%M%S}", "ORGANIZER:mailto:ren@example.test"]
    if status:
        lines.append(f"STATUS:{status}")
    return "\r\n".join(lines + [extra + "END:VEVENT", "END:VCALENDAR", ""])


@pytest.fixture
def stored(data_dir):
    """Store one message holding an invite, as fetching its body would."""
    count = [0]

    def store_one(cal, days_ago=1):
        count[0] += 1
        n = count[0]
        c = store.conn()
        pk = c.execute("INSERT INTO messages (account, kind, folder, uid, message_id, thread_id, ts, from_addr, subject, recipients) "
                       "VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (ACCT, "imap", "INBOX", n, f"<m{n}@example.test>", f"t{n}", int((NOW - timedelta(days=days_ago)).timestamp()),
                        "ren@example.test", "Invitation", "")).lastrowid
        m = EmailMessage()
        m["From"], m["To"], m["Subject"] = "ren@example.test", ACCT, "Invitation"
        m.set_content("See the invite.")
        m.add_attachment(cal.encode(), maintype="text", subtype="calendar", filename="invite.ics")
        raw = m.as_bytes()
        store.store_body(c, pk, raw, NOW.isoformat())
        for a in mime.parse(raw)["attachments"]:
            c.execute("INSERT INTO attachments(message_pk,part_id,filename,content_type,size,content_id,is_inline) VALUES(?,?,?,?,?,?,?)",
                      (pk, a["part_id"], a["filename"], a["content_type"], a["size"], a["content_id"], a["is_inline"]))
        c.commit()
        c.close()
        return pk
    return store_one


def listed():
    return [(x["pk"], x["start"]) for x in calendar.agenda.upcoming(now=NOW)["items"]]


def weekly():
    first = NOW - timedelta(days=14)
    return event("s", first, extra="RRULE:FREQ=WEEKLY\r\n")


def test_the_reader_keeps_a_method_with_no_status():
    inv = ics.parse(event("c", TOMORROW, method="CANCEL"))
    assert inv["method"] == "CANCEL" and not inv.get("status")
    assert agenda.dead(inv)


@pytest.mark.parametrize("said", ["CANCEL", "cancel", "Cancel"])
def test_a_method_alone_ends_the_event_in_any_case(stored, said):
    stored(event("c", TOMORROW), days_ago=3)
    stored(event("c", TOMORROW, method=said))
    assert listed() == []


def test_the_cancellation_is_no_event_of_its_own(stored):
    stored(event("c", TOMORROW, method="CANCEL"))
    assert listed() == []


def test_a_method_ends_an_event_whose_status_still_says_confirmed(stored):
    stored(event("c", TOMORROW), days_ago=3)
    stored(event("c", TOMORROW, method="CANCEL", status="CONFIRMED"))
    assert listed() == []


def test_another_events_cancellation_leaves_this_one(stored):
    kept = stored(event("keep", TOMORROW), days_ago=3)
    stored(event("other", TOMORROW, method="CANCEL"))
    assert listed() == [(kept, "2026-10-31T12:00")]


def test_a_method_alone_ends_a_series(stored):
    stored(weekly(), days_ago=20)
    assert len(listed()) == 1
    stored(event("s", NOW - timedelta(days=14), method="CANCEL"))
    assert listed() == []


def test_a_method_alone_ends_a_series_with_the_occurrences_it_moved(stored):
    stored(weekly(), days_ago=20)
    moved = TOMORROW
    stored(event("s", moved, extra=f"RECURRENCE-ID:{NOW:%Y%m%dT%H%M%S}\r\n"), days_ago=2)
    assert len(listed()) == 2
    stored(event("s", NOW - timedelta(days=14), method="CANCEL"))
    assert listed() == []


def test_a_method_alone_ends_one_occurrence_and_leaves_the_series(stored):
    series = stored(weekly(), days_ago=20)
    assert listed() == [(series, "2026-10-30T12:00")]
    stored(event("s", NOW, method="CANCEL", extra=f"RECURRENCE-ID:{NOW:%Y%m%dT%H%M%S}\r\n"))
    assert listed() == [(series, "2026-11-06T12:00")]

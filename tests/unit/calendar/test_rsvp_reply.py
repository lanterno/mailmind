"""What an RSVP's METHOD:REPLY says of an invite a stranger wrote. tests/golden/http_send/rsvp_to_an_invite.json has one whole
exchange; test_ics_reader_contract.py that nothing in an invite adds a line. All addresses are fictional."""
import pytest

from mailmind.bootstrap.calendar import ics
from mailmind.calendar.domain import rsvp


def _invite(*lines):
    return ics.parse("\r\n".join(["BEGIN:VCALENDAR", "METHOD:REQUEST", "BEGIN:VEVENT", "UID:u", "DTSTART:20261102T100000", *lines,
                                  "END:VEVENT", "END:VCALENDAR", ""]))


def _line(reply, starts):
    return next(line for line in reply.replace("\r\n ", "").split("\r\n") if line.startswith(starts))


def test_a_quote_in_a_name_doesnt_end_the_parameter_it_goes_in():
    inv = _invite('ATTENDEE;CN="Ada ^\'Lovelace^\' Park":mailto:ada@example.test')
    inv["organizer"] = {"name": 'Ren"; ROLE=CHAIR; X="', "email": "ren@example.test"}
    reply = rsvp.build_reply(inv, "ada@example.test", 'Ada "Lovelace" Park', "ACCEPTED")
    assert _line(reply, "ORGANIZER") == "ORGANIZER;CN=\"Ren^'\\; ROLE=CHAIR\\; X=^'\":mailto:ren@example.test"
    assert _line(reply, "ATTENDEE") == "ATTENDEE;CN=\"Ada ^'Lovelace^' Park\";PARTSTAT=ACCEPTED:mailto:ada@example.test"


def test_a_name_with_no_quote_goes_as_it_did():
    inv = _invite('ORGANIZER;CN="Takeda, Ren":mailto:ren@example.test')
    assert _line(rsvp.build_reply(inv, "ada@example.test", "Ada", "DECLINED"), "ORGANIZER") == 'ORGANIZER;CN="Takeda\\, Ren":mailto:ren@example.test'


@pytest.mark.parametrize("start", ["DTSTART;TZID=Asia/Tokyo:00010101T000000", "DTSTART;TZID=America/New_York:99991231T235959"])
def test_a_time_utc_would_take_past_a_datetime_leaves_the_reply_without_it(start):
    inv = ics.parse("\r\n".join(["BEGIN:VCALENDAR", "BEGIN:VEVENT", "UID:u", start, "END:VEVENT", "END:VCALENDAR", ""]))
    reply = rsvp.build_reply(inv, "ada@example.test", "Ada", "ACCEPTED")
    assert "UID:u" in reply and "DTSTART" not in reply

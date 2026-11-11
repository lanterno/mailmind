"""An event a person confirmed, as a METHOD:PUBLISH file: for their own calendar, inviting no one, and nothing a stranger's mail
said can add a line to it."""
from datetime import datetime, timezone

import pytest

from mailmind.calendar.domain import publish
from mailmind.calendar.domain.publish import NewEvent
ME = "ada@example.test"
STAMP = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
UID = "abc@mailmind.local"


def lines(text):
    assert text.endswith("\r\n")
    return text[:-2].split("\r\n")


def unfolded(text):
    return text.replace("\r\n ", "")


def field(text, name):
    return next((ln for ln in lines(unfolded(text)) if ln.startswith(name)), None)


def built(**kw):
    return publish.build(NewEvent(**{"title": "Design review", "start": "2026-11-12T15:00", "end": "2026-11-12T16:00", "place": "Room 2", **kw}), ME, UID, STAMP)


def test_it_is_a_publish_with_the_event_and_its_when():
    text = built()
    assert lines(text)[:6] == ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//MailMind//Suggestion//EN", "CALSCALE:GREGORIAN",
                               "METHOD:PUBLISH", "BEGIN:VEVENT"]
    assert lines(text)[-2:] == ["END:VEVENT", "END:VCALENDAR"]
    assert field(text, "UID:") == "UID:" + UID
    assert field(text, "DTSTAMP:") == "DTSTAMP:20261102T120000Z"
    assert field(text, "SUMMARY:") == "SUMMARY:Design review"
    assert field(text, "LOCATION:") == "LOCATION:Room 2"
    assert field(text, "DTSTART") == "DTSTART:20261112T150000"
    assert field(text, "DTEND") == "DTEND:20261112T160000"


def test_it_invites_no_one_and_is_the_persons_own():
    text = built()
    assert field(text, "ATTENDEE") is None
    assert [ln for ln in lines(unfolded(text)) if ln.startswith("ORGANIZER")] == ["ORGANIZER:mailto:" + ME]


def test_an_aware_time_goes_to_utc():
    text = built(start="2026-11-12T15:00-05:00", end="2026-11-12T16:30-05:00")
    assert field(text, "DTSTART") == "DTSTART:20261112T200000Z"
    assert field(text, "DTEND") == "DTEND:20261112T213000Z"


def test_a_time_with_no_end_runs_an_hour():
    assert field(built(end=None), "DTEND") == "DTEND:20261112T160000"
    assert field(built(start="2026-11-12T23:30-05:00", end=None), "DTEND") == "DTEND:20261113T053000Z"


def test_a_whole_day_ends_the_day_after_as_a_calendar_says_it():
    text = built(start="2026-11-12", end=None)
    assert field(text, "DTSTART") == "DTSTART;VALUE=DATE:20261112"
    assert field(text, "DTEND") == "DTEND;VALUE=DATE:20261113"
    assert field(built(start="2026-12-30", end="2027-01-02"), "DTEND") == "DTEND;VALUE=DATE:20270103"


def test_no_place_is_no_location():
    assert field(built(place=""), "LOCATION") is None


def test_text_is_escaped_as_a_calendar_reads_it():
    assert field(built(title="Lunch, then; a\\b"), "SUMMARY:") == "SUMMARY:Lunch\\, then\\; a\\\\b"


@pytest.mark.parametrize("attack", [
    "Lunch\r\nATTENDEE:mailto:x@example.test",
    "Lunch\rATTENDEE:mailto:x@example.test",
    "Lunch\nATTENDEE:mailto:x@example.test",
    "Lunch ATTENDEE:mailto:x@example.test",
    "Lunch\x85ATTENDEE:mailto:x@example.test",
    "Lunch\r\nEND:VEVENT\r\nBEGIN:VEVENT",
])
@pytest.mark.parametrize("where", ["title", "place"])
def test_nothing_in_the_text_can_add_a_line(attack, where):
    text = built(**{where: attack})
    assert not any(ln.startswith("ATTENDEE") for ln in lines(unfolded(text)))
    assert sum(ln == "BEGIN:VEVENT" for ln in lines(unfolded(text))) == 1
    assert sum(ln == "END:VEVENT" for ln in lines(unfolded(text))) == 1
    assert "\r" not in unfolded(text).replace("\r\n", "")


def test_a_long_title_is_folded_on_character_boundaries():
    text = built(title="Reunión anual de diseño para el año próximo, en la sala grande del edificio principal " * 2)
    assert all(len(ln.encode("utf-8")) <= 75 for ln in lines(text))
    assert "Reunión anual" in unfolded(text)


def test_the_same_event_has_the_same_uid_so_a_calendar_replaces_it():
    event = NewEvent("Design review", "2026-11-12T15:00", None, "")
    assert publish.uid_for(ME, 7, event) == publish.uid_for(ME, 7, NewEvent("Design review", "2026-11-12T15:00", "2026-11-12T16:00", "Room"))
    assert publish.uid_for(ME, 7, event) != publish.uid_for(ME, 8, event)
    assert publish.uid_for(ME, 7, event) != publish.uid_for(ME, 7, NewEvent("Other", event.start, None, ""))
    assert publish.uid_for(ME, 7, event).endswith("@mailmind.local")

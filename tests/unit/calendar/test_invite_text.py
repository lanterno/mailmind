"""What an invite's text comes to: who an RSVP goes to, the dates it says, and what is left when a line won't read. A stranger
wrote each of these, so what's kept is what would send a reply astray, show a wrong date or lose the invite. Dates can be
checked against a calendar: Oct 5 2026 is a Monday. All fictional.

test_ics_reader_contract.py holds what any text may not do to the reader, test_upcoming.py what Today & next makes of it.
"""
from datetime import datetime

import pytest

from mailmind.bootstrap.calendar import ics
from mailmind.calendar.application.agenda import Agenda
from mailmind.calendar.domain import agenda, rsvp

BEFORE = datetime(2026, 9, 1, 12)           # naive is this machine's clock, and every series here starts after it
NOW = datetime(2026, 11, 1, 12)

MON = "DTSTART:20261005T100000"             # floating
MON_END = "DTEND:20261005T103000"


_BUDGET = ics._EXPAND_SECONDS               # what the reader allows itself for a rule, before _time_to_read raises it


@pytest.fixture(autouse=True)
def _time_to_read(monkeypatch):
    """Time enough for each text and rule: these tests are about what the dates are, and a machine every core of which is
    busy shouldn't run a rule out of its CPU budget. test_ics_reader_contract.py holds the budget itself."""
    monkeypatch.setattr(ics, "_READ_SECONDS", 5.0)
    monkeypatch.setattr(ics, "_EXPAND_SECONDS", 5.0)


def cal(*lines, method="REQUEST"):
    """One event, UID and SUMMARY first so a line can say them again."""
    head = ["BEGIN:VCALENDAR", "VERSION:2.0"] + ([f"METHOD:{method}"] if method else [])
    return "\r\n".join([*head, "BEGIN:VEVENT", "UID:u1@example.test", "SUMMARY:Studio sync", *lines, "END:VEVENT", "END:VCALENDAR", ""])


class _Stored:
    """One message, holding this text."""
    def __init__(self, text):
        self.text = text

    def invite_parts(self, _since, _skipped):
        yield {"pk": 1, "message_key": "k", "uid": 1, "account": "a@example.test", "folder": "INBOX", "thread_id": "t"}, self.text


def _listed(text):
    return [f"{x['summary']} {x['start']} to {x['end']}" for x in Agenda(_Stored(text), ics).upcoming(now=NOW, days=4000)["items"]]


def _walk(inv, count=4):
    """The first `count` occurrences of a series, each found by asking what comes once the last has ended."""
    found, after = [], BEFORE
    while len(found) < count and (occ := ics.next_occurrence(inv, after)):
        found.append(occ)
        after = occ[1]
    return found


def _days(*lines, count=4):
    return [occ[0].date().isoformat() for occ in _walk(ics.parse(cal(*lines)), count)]


# ── who an RSVP answers ───────────────────────────────────────────────────────

REN = "ren@example.test"
ORGANIZERS = {
    "colon-in-a-quoted-name": ('ORGANIZER;CN="Ren: Takeda":mailto:ren@example.test', {"name": "Ren: Takeda", "email": REN}),
    "semicolon-in-a-quoted-name": ('ORGANIZER;CN="Takeda; Ren":mailto:ren@example.test', {"name": "Takeda; Ren", "email": REN}),
    "comma-in-a-quoted-name": ('ORGANIZER;CN="Takeda, Ren":mailto:ren@example.test', {"name": "Takeda, Ren", "email": REN}),
    "mailto-in-capitals": ("ORGANIZER:MAILTO:ren@example.test", {"name": "", "email": REN}),
    "a-parameter-in-lower-case": ("ORGANIZER;cn=Ren:mailto:ren@example.test", {"name": "Ren", "email": REN}),
    "a-folded-name": ("ORGANIZER;CN=Ren\r\n  Takeda:mailto:ren@example.test", {"name": "Ren Takeda", "email": REN}),
    "a-name-not-in-ascii": ("ORGANIZER;CN=Zoë Müller:mailto:ren@example.test", {"name": "Zoë Müller", "email": REN}),
    "rfc-6868-quote": ('ORGANIZER;CN="Ren ^\'Rocket^\' Takeda":mailto:ren@example.test', {"name": 'Ren "Rocket" Takeda', "email": REN}),
    "rfc-6868-line-break": ('ORGANIZER;CN="Ren^nTakeda":mailto:ren@example.test', {"name": "Ren Takeda", "email": REN}),
}


@pytest.mark.parametrize("line, organizer", ORGANIZERS.values(), ids=ORGANIZERS.keys())
def test_the_organizer_is_read_and_the_reply_goes_to_that_address(line, organizer):
    inv = ics.parse(cal(MON, line))
    assert inv["organizer"] == organizer
    reply = rsvp.build_reply(inv, "ada@example.test", "Ada", "ACCEPTED").replace("\r\n ", "")
    assert next(each for each in reply.split("\r\n") if each.startswith("ORGANIZER")).endswith(f":mailto:{REN}")


def test_a_quote_where_none_may_be_leaves_no_organizer_to_answer_rather_than_a_guess():
    """A quote is no part of an unquoted parameter, so that line is no line."""
    assert ics.parse(cal(MON, 'ORGANIZER;CN=Ren"x:mailto:ren@example.test'))["organizer"] == {}


def test_a_line_with_a_nameless_parameter_doesnt_replace_the_uid_a_reply_echoes():
    assert ics.parse(cal(MON, "UID;:x"))["uid"] == "u1@example.test"


def test_an_attendee_with_no_address_is_no_attendee():
    inv = ics.parse(cal(MON, "ATTENDEE;CN=Nobody:", "ATTENDEE:mailto:bo@example.test"))
    assert inv["attendees"] == [{"name": "", "email": "bo@example.test"}]


def test_no_more_than_25_attendees_are_kept():
    inv = ics.parse(cal(MON, *(f"ATTENDEE;CN=P{i}:mailto:p{i}@example.test" for i in range(40))))
    assert len(inv["attendees"]) == 25 and inv["attendees"][0] == {"name": "P0", "email": "p0@example.test"}


def test_an_alarm_says_nothing_for_its_event_wherever_it_comes():
    alarm = ("BEGIN:VALARM", "ACTION:DISPLAY", "SUMMARY:Alarm", "DESCRIPTION:A reminder", "ATTENDEE:mailto:alarm@example.test", "TRIGGER:-PT15M", "END:VALARM")
    for lines in ((MON, "DESCRIPTION:Bring the plans", *alarm), (MON, *alarm, "DESCRIPTION:Bring the plans")):
        inv = ics.parse(cal(*lines))
        assert (inv["summary"], inv["description"], inv["attendees"]) == ("Studio sync", "Bring the plans", [])


# ── what cancels ──────────────────────────────────────────────────────────────

ON_NOV_5 = "DTSTART:20261105T100000"


def test_a_method_inside_an_event_is_no_method():
    """METHOD is the calendar's. Taken for the event's, this would cancel it."""
    text = cal(ON_NOV_5, "METHOD:CANCEL", method="")
    assert ics.parse(text)["method"] == ""
    assert _listed(text) == ["Studio sync 2026-11-05T10:00 to 2026-11-05T10:00"]


def test_one_calendars_cancel_leaves_another_calendars_request():
    text = "\r\n".join(["BEGIN:VCALENDAR", "METHOD:REQUEST",
                        "BEGIN:VEVENT", "UID:a@example.test", "SUMMARY:First", "DTSTART:20261110T100000", "END:VEVENT", "END:VCALENDAR",
                        "BEGIN:VCALENDAR", "METHOD:CANCEL",
                        "BEGIN:VEVENT", "UID:c@example.test", "SUMMARY:Third", "DTSTART:20261112T100000", "END:VEVENT", "END:VCALENDAR", ""])
    assert ics.parse(text)["method"] == "REQUEST"
    assert _listed(text) == ["First 2026-11-10T10:00 to 2026-11-10T10:00"]


def test_a_status_in_lower_case_still_cancels():
    assert ics.parse(cal(ON_NOV_5, "STATUS:cancelled"))["status"] == "CANCELLED"
    assert _listed(cal(ON_NOV_5, "STATUS:cancelled")) == []


# ── the card's words ──────────────────────────────────────────────────────────

WORDS = {
    "escapes": (r"SUMMARY:One\, two\; three\\four", "summary", "One, two; three\\four"),
    "a-capital-n-is-a-line-break": (r"DESCRIPTION:Line one\nLine two\NLine three", "description", "Line one\nLine two\nLine three"),
    "an-escaped-backslash-before-an-n-is-no-line-break": (r"SUMMARY:C:\\new\\notes", "summary", "C:\\new\\notes"),
    "a-language-parameter": ("LOCATION;LANGUAGE=en-US:Room 2", "location", "Room 2"),
    "not-ascii": ("SUMMARY:Résumé review 東京", "summary", "Résumé review 東京"),
    "folded": ("DESCRIPTION:Line that\r\n  goes on\r\n\tand on", "description", "Line that goes onand on"),
    "said-twice": ("SUMMARY:Later", "summary", "Later"),
    "said-empty": ("SUMMARY:", "summary", ""),
    "every-two-weeks-so-many-times": ("RRULE:INTERVAL=2;BYDAY=MO;FREQ=WEEKLY;COUNT=9", "recurrence", "Every 2 weeks on Mon ×9"),
    "until-a-date": ("RRULE:FREQ=WEEKLY;UNTIL=20261102", "recurrence", "Every week until Nov 2, 2026"),
}


@pytest.mark.parametrize("line, field, said", WORDS.values(), ids=WORDS.keys())
def test_what_the_card_says(line, field, said):
    assert ics.parse(cal(MON, line))[field] == said


def test_an_event_with_no_summary_has_no_title():
    assert ics.parse("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:n\r\nDTSTART:20261105T100000\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")["summary"] == "(no title)"


def test_a_long_description_is_cut():
    assert len(ics.parse(cal(MON, "DESCRIPTION:" + "words " * 400))["description"]) == 1500


# ── how the lines come ────────────────────────────────────────────────────────

PLAIN = cal(MON, MON_END)
ROUGH = {
    "lf-only": PLAIN.replace("\r\n", "\n"),
    "cr-only": PLAIN.replace("\r\n", "\r"),
    "byte-order-mark": "\ufeff" + PLAIN,
    "blank-lines": cal(MON, "", "", MON_END),
    "a-header-before-it": "Content-Type: text/calendar\r\n\r\n" + PLAIN,
    "a-signature-after-it": PLAIN + "-- \r\nsent from a phone\r\n",
    "a-line-it-cannot-read": cal("not a line at all", MON, ":y", MON_END),
    "a-line-it-cannot-read-before-it-all": "not a line at all\r\n" + PLAIN,
    "cut-short-after-the-event": PLAIN.replace("END:VCALENDAR\r\n", ""),
}


@pytest.mark.parametrize("text", ROUGH.values(), ids=ROUGH.keys())
def test_however_the_lines_come_it_is_the_same_event(text):
    assert ics.events(text) == [ics.parse(PLAIN)]


def test_an_event_with_no_calendar_around_it_is_still_an_event():
    inv = ics.parse("BEGIN:VEVENT\r\nUID:b\r\nSUMMARY:Bare\r\nDTSTART:20261105T100000\r\nEND:VEVENT\r\n")
    assert (inv["uid"], inv["summary"], inv["start_iso"], inv["method"]) == ("b", "Bare", "2026-11-05T10:00:00", "")


NO_EVENT = {
    "empty": "",
    "not-a-calendar": "not a calendar at all",
    "no-event": "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n",
    "only-a-todo": "BEGIN:VCALENDAR\r\nBEGIN:VTODO\r\nUID:t\r\nSUMMARY:Task\r\nEND:VTODO\r\nEND:VCALENDAR\r\n",
    "cut-short-in-the-event": "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:c\r\nDTSTART:20261105T100000\r\n",
}


@pytest.mark.parametrize("text", NO_EVENT.values(), ids=NO_EVENT.keys())
def test_what_holds_no_event_has_no_card(text):
    assert ics.parse(text) is None and ics.events(text) == [] and _listed(text) == []


NO_DATE = {"no-start": (), "garbage": ("DTSTART:garbage", "DTEND:also garbage"), "an-impossible-date": ("DTSTART:20261345T100000",),
           "a-duration": ("DTSTART:PT1H",), "a-time": ("DTSTART:100000",), "a-period": ("DTSTART:20261105T100000/PT1H",)}


@pytest.mark.parametrize("lines", NO_DATE.values(), ids=NO_DATE.keys())
def test_an_event_with_no_date_it_can_read_has_a_card_no_place_on_the_list_and_a_reply_without_one(lines):
    text = cal(*lines)
    inv = ics.parse(text)
    assert (inv["uid"], inv["start_iso"], inv["when"]) == ("u1@example.test", "", "")
    assert ics.next_occurrence(inv, NOW) is None and _listed(text) == []
    reply = rsvp.build_reply(inv, "ada@example.test", "Ada", "ACCEPTED")
    assert "UID:u1@example.test" in reply and "DTSTART" not in reply


# ── when a one-off happens ────────────────────────────────────────────────────

BERLIN_AT_10 = "DTSTART;TZID=Europe/Berlin:20261105T100000"
ONE_OFFS = {  # the lines: start_iso, end_iso, the card's words in this suite's UTC, and how many minutes it runs
    "utc": (("DTSTART:20261105T100000Z", "DTEND:20261105T103000Z"), "2026-11-05T10:00:00+00:00", "2026-11-05T10:30:00+00:00", "Thu, Nov 5 · 10:00 AM–10:30 AM", 30),
    "a-zone": ((BERLIN_AT_10, "DTEND;TZID=Europe/Berlin:20261105T103000"), "2026-11-05T10:00:00+01:00", "2026-11-05T10:30:00+01:00", "Thu, Nov 5 · 9:00 AM–9:30 AM", 30),
    "a-tzid-in-lower-case": (("DTSTART;tzid=Europe/Berlin:20261105T100000",), "2026-11-05T10:00:00+01:00", "", "Thu, Nov 5 · 9:00 AM", 0),
    "a-tzid-in-quotes": (('DTSTART;TZID="Europe/Berlin":20261105T100000',), "2026-11-05T10:00:00+01:00", "", "Thu, Nov 5 · 9:00 AM", 0),
    "a-zone-and-a-z": (("DTSTART;TZID=Europe/Berlin:20261105T100000Z",), "2026-11-05T10:00:00+00:00", "", "Thu, Nov 5 · 10:00 AM", 0),
    "across-midnight": (("DTSTART:20261105T233000", "DTEND:20261106T003000"), "2026-11-05T23:30:00", "2026-11-06T00:30:00", "Thu, Nov 5 · 11:30 PM → Fri, Nov 6 12:30 AM", 60),
    "an-end-before-its-start": ((ON_NOV_5, "DTEND:20261105T090000"), "2026-11-05T10:00:00", "2026-11-05T09:00:00", "Thu, Nov 5 · 10:00 AM–9:00 AM", 0),
    "a-duration": ((ON_NOV_5, "DURATION:PT1H"), "2026-11-05T10:00:00", "2026-11-05T11:00:00", "Thu, Nov 5 · 10:00 AM–11:00 AM", 60),
    "all-day": (("DTSTART;VALUE=DATE:20261105", "DTEND;VALUE=DATE:20261106"), "2026-11-05T00:00:00", "2026-11-06T00:00:00", "Thu, Nov 5", 1440),
    "all-day-no-end": (("DTSTART;VALUE=DATE:20261105",), "2026-11-05T00:00:00", "", "Thu, Nov 5", 1440),
    "all-day-saying-only-a-date": (("DTSTART:20261105", "DTEND:20261106"), "2026-11-05T00:00:00", "2026-11-06T00:00:00", "Thu, Nov 5", 1440),
    "several-days": (("DTSTART;VALUE=DATE:20261105", "DTEND;VALUE=DATE:20261108"), "2026-11-05T00:00:00", "2026-11-08T00:00:00", "Thu, Nov 5", 4320),
    "several-days-by-duration": (("DTSTART;VALUE=DATE:20261105", "DURATION:P3D"), "2026-11-05T00:00:00", "2026-11-08T00:00:00", "Thu, Nov 5", 4320),
    "a-date-value-with-a-time": (("DTSTART;VALUE=DATE:20261105T100000",), "2026-11-05T10:00:00", "", "Thu, Nov 5 · 10:00 AM", 0),
    "said-twice": ((MON, ON_NOV_5), "2026-11-05T10:00:00", "", "Thu, Nov 5 · 10:00 AM", 0),
    "an-exdate-on-a-one-off": ((ON_NOV_5, "EXDATE:20261105T100000"), "2026-11-05T10:00:00", "", "Thu, Nov 5 · 10:00 AM", 0),
}


@pytest.mark.parametrize("lines, start, end, when, minutes", ONE_OFFS.values(), ids=ONE_OFFS.keys())
def test_when_a_one_off_happens(lines, start, end, when, minutes):
    inv = ics.parse(cal(*lines))
    assert (inv["start_iso"], inv["end_iso"], inv["when"]) == (start, end, when)
    occ = ics.next_occurrence(inv, NOW)
    assert (occ[1] - occ[0]).total_seconds() / 60 == minutes


def test_a_time_with_no_seconds_is_read_as_hours_and_minutes():
    assert ics.parse(cal("DTSTART:20261105T1000", "DTEND:20261105T1030"))["end_iso"] == "2026-11-05T10:30:00"


def test_a_time_in_a_zone_shows_at_this_machines_time(machine_zone):
    text = cal(BERLIN_AT_10, "DTEND;TZID=Europe/Berlin:20261105T103000")
    when, listed = {"UTC": ("Thu, Nov 5 · 9:00 AM–9:30 AM", "Studio sync 2026-11-05T09:00 to 2026-11-05T09:30"),
                    "Asia/Tokyo": ("Thu, Nov 5 · 6:00 PM–6:30 PM", "Studio sync 2026-11-05T18:00 to 2026-11-05T18:30"),
                    "America/New_York": ("Thu, Nov 5 · 4:00 AM–4:30 AM", "Studio sync 2026-11-05T04:00 to 2026-11-05T04:30")}[machine_zone]
    assert ics.parse(text)["when"] == when
    assert _listed(text) == [listed]


# ── when a series happens ─────────────────────────────────────────────────────

STARTS = {
    "floating": (MON, MON_END),
    "a-zone": ("DTSTART;TZID=Europe/Berlin:20261005T100000", "DTEND;TZID=Europe/Berlin:20261005T103000"),
    "utc": ("DTSTART:20261005T100000Z", "DTEND:20261005T103000Z"),
    "all-day": ("DTSTART;VALUE=DATE:20261005", "DTEND;VALUE=DATE:20261006"),
    "an-unknown-tzid": ("DTSTART;TZID=Nowhere/Land:20261005T100000",),
    "late-evening": ("DTSTART;TZID=America/New_York:20261005T233000", "DTEND;TZID=America/New_York:20261006T003000"),
}
WEEKS = "2026-10-05 2026-10-12 2026-10-19 2026-10-26 2026-11-02 2026-11-09"     # over the day Berlin's clocks change, and New York's
EVERY_RULE = {
    "FREQ=DAILY": "2026-10-05 2026-10-06 2026-10-07 2026-10-08 2026-10-09 2026-10-10",
    "FREQ=DAILY;INTERVAL=2": "2026-10-05 2026-10-07 2026-10-09 2026-10-11 2026-10-13 2026-10-15",
    "FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR": "2026-10-05 2026-10-06 2026-10-07 2026-10-08 2026-10-09 2026-10-12",
    "FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR;INTERVAL=2": "2026-10-05 2026-10-07 2026-10-09 2026-10-13 2026-10-15 2026-10-19",
    "FREQ=WEEKLY": WEEKS,
    "FREQ=WEEKLY;INTERVAL=2": "2026-10-05 2026-10-19 2026-11-02 2026-11-16 2026-11-30 2026-12-14",
    "FREQ=WEEKLY;BYDAY=MO": WEEKS,
    "FREQ=WEEKLY;BYDAY=WE,MO": "2026-10-05 2026-10-07 2026-10-12 2026-10-14 2026-10-19 2026-10-21",
    "FREQ=WEEKLY;BYDAY=WE,MO;INTERVAL=2": "2026-10-05 2026-10-07 2026-10-19 2026-10-21 2026-11-02 2026-11-04",
    "FREQ=MONTHLY": "2026-10-05 2026-11-05 2026-12-05 2027-01-05 2027-02-05 2027-03-05",
    "FREQ=MONTHLY;INTERVAL=2": "2026-10-05 2026-12-05 2027-02-05 2027-04-05 2027-06-05 2027-08-05",
    "FREQ=MONTHLY;BYMONTHDAY=5": "2026-10-05 2026-11-05 2026-12-05 2027-01-05 2027-02-05 2027-03-05",
    "FREQ=YEARLY": "2026-10-05 2027-10-05 2028-10-05 2029-10-05 2030-10-05 2031-10-05",
    "FREQ=YEARLY;INTERVAL=2": "2026-10-05 2028-10-05 2030-10-05 2032-10-05 2034-10-05 2036-10-05",
}


def _clock_and_length(occ):
    return occ[0].strftime("%H:%M"), occ[1] - occ[0]


@pytest.mark.parametrize("start", STARTS.values(), ids=STARTS.keys())
def test_every_rule_comes_to_these_dates_however_the_start_is_said(machine_zone, start):
    """And each at the time and for the length of the first: the clocks change, and a series doesn't."""
    came_to = {}
    for rule in EVERY_RULE:
        found = _walk(ics.parse(cal(*start, f"RRULE:{rule}")), count=6)
        assert {_clock_and_length(occ) for occ in found} == {_clock_and_length(found[0])}, rule
        came_to[rule] = " ".join(occ[0].date().isoformat() for occ in found)
    assert came_to == EVERY_RULE


OCT_26, NOV_2 = "2026-10-26", "2026-11-02"
ENDINGS = {  # the last date, Nov 2 unless the start says another, on this suite's UTC
    "COUNT=5": {},
    "UNTIL=20261102T090000Z": {"floating": OCT_26, "utc": OCT_26, "an-unknown-tzid": OCT_26, "late-evening": OCT_26},    # 09:00 UTC is when Berlin's 10:00 is
    "UNTIL=20261102T100000": {"late-evening": OCT_26},          # in the start's own terms
    "UNTIL=20261102": {},                                       # all of that day
}


@pytest.mark.parametrize("ending", ENDINGS)
@pytest.mark.parametrize("start", STARTS, ids=STARTS.keys())
def test_where_a_series_ends(start, ending):
    assert _days(*STARTS[start], f"RRULE:FREQ=WEEKLY;{ending}", count=9)[-1] == ENDINGS[ending].get(start, NOV_2)


def test_a_utc_until_on_a_floating_series_goes_by_this_machines_clock(machine_zone):
    last = {"UTC": OCT_26, "Asia/Tokyo": NOV_2, "America/New_York": OCT_26}[machine_zone]
    assert _days(*STARTS["floating"], "RRULE:FREQ=WEEKLY;UNTIL=20261102T090000Z", count=9)[-1] == last


STRUCK = {  # the start, and each way of striking Nov 2 from it
    "floating": ("floating", "EXDATE:20261102T100000"),
    "a-list": ("floating", "EXDATE:20261102T100000,20261116T100000"),
    "lines": ("floating", "EXDATE:20261102T100000\r\nEXDATE:20261116T100000"),
    "a-bad-value-beside-it": ("floating", "EXDATE:garbage,20261102T100000"),
    "a-whole-day-of-a-timed-series": ("floating", "EXDATE;VALUE=DATE:20261102"),
    "a-zone": ("a-zone", "EXDATE;TZID=Europe/Berlin:20261102T100000"),
    "another-zone": ("a-zone", "EXDATE:20261102T090000Z"),
    "utc": ("utc", "EXDATE:20261102T100000Z"),
    "a-date-of-an-all-day-series": ("all-day", "EXDATE;VALUE=DATE:20261102"),
    "a-time-of-an-all-day-series": ("all-day", "EXDATE:20261102T090000"),
    "an-unknown-tzid": ("an-unknown-tzid", "EXDATE:20261102T100000"),
    "late-evening": ("late-evening", "EXDATE;TZID=America/New_York:20261102T233000"),
}


@pytest.mark.parametrize("start, exdate", STRUCK.values(), ids=STRUCK.keys())
def test_an_exdate_strikes_that_occurrence_and_no_other(machine_zone, start, exdate):
    assert _days(*STARTS[start], "RRULE:FREQ=WEEKLY", *exdate.split("\r\n"), count=5) == "2026-10-05 2026-10-12 2026-10-19 2026-10-26 2026-11-09".split()


def test_an_exdate_that_wont_read_strikes_nothing():
    assert _days(MON, MON_END, "RRULE:FREQ=WEEKLY", "EXDATE:garbage", count=5)[4] == NOV_2


SERIES = {  # the start, the rest of what the event says: what it comes to
    # The ways a rule is said.
    "parts-in-any-order": (MON, "RRULE:INTERVAL=2;BYDAY=MO;FREQ=WEEKLY;COUNT=3", "2026-10-05 2026-10-19 2026-11-02"),
    "in-lower-case": (MON, "RRULE:freq=weekly;interval=2;byday=mo", "2026-10-05 2026-10-19 2026-11-02 2026-11-16"),
    "said-twice": (MON, "RRULE:FREQ=DAILY\r\nRRULE:FREQ=WEEKLY", "2026-10-05 2026-10-12 2026-10-19 2026-10-26"),
    "wkst-monday": (MON, "RRULE:FREQ=WEEKLY;WKST=MO;BYDAY=MO", "2026-10-05 2026-10-12 2026-10-19 2026-10-26"),
    "no-rule-at-all": (MON, "RRULE:whenever", "2026-10-05"),
    # Months and years that haven't the day.
    "the-31st": ("DTSTART:20261231T100000", "RRULE:FREQ=MONTHLY", "2026-12-31 2027-01-31 2027-03-31 2027-05-31"),
    "the-first-tuesday": ("DTSTART:20261006T100000", "RRULE:FREQ=MONTHLY;BYDAY=1TU", "2026-10-06 2026-11-03 2026-12-01 2027-01-05"),
    "the-last-friday": ("DTSTART:20261030T100000", "RRULE:FREQ=MONTHLY;BYDAY=-1FR", "2026-10-30 2026-11-27 2026-12-25 2027-01-29"),
    "the-last-day": ("DTSTART:20261031T100000", "RRULE:FREQ=MONTHLY;BYMONTHDAY=-1", "2026-10-31 2026-11-30 2026-12-31 2027-01-31"),
    "29-february": ("DTSTART:20280229T100000", "RRULE:FREQ=YEARLY", "2028-02-29 2032-02-29 2036-02-29 2040-02-29"),
    "the-13th-if-a-friday": ("DTSTART:20261113T100000", "RRULE:FREQ=MONTHLY;BYMONTHDAY=13;BYDAY=FR", "2026-11-13 2027-08-13 2028-10-13 2029-04-13"),
    # WKST moves which days share a week, and so which come round under an INTERVAL. RFC 5545's own example.
    "wkst-sunday": ("DTSTART:20261004T100000", "RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=SU,MO;WKST=SU", "2026-10-04 2026-10-05 2026-10-18 2026-10-19"),
    # Parts a rule from a real calendar may have.
    "bysetpos": (MON, "RRULE:FREQ=MONTHLY;BYDAY=MO;BYSETPOS=1", "2026-10-05 2026-11-02 2026-12-07 2027-01-04"),
    "bymonth": (MON, "RRULE:FREQ=YEARLY;BYMONTH=10,11", "2026-10-05 2026-11-05 2027-10-05 2027-11-05"),
    "bymonth-and-byday": (MON, "RRULE:FREQ=MONTHLY;BYMONTH=11,12;BYDAY=1MO", "2026-10-05 2026-11-02 2026-12-07 2027-11-01"),
    "byweekno": (MON, "RRULE:FREQ=YEARLY;BYWEEKNO=41,45;BYDAY=MO", "2026-10-05 2026-11-02 2027-10-11 2027-11-08"),
    "byyearday": (MON, "RRULE:FREQ=YEARLY;BYYEARDAY=278,310", "2026-10-05 2026-11-06 2027-10-05 2027-11-06"),
    "a-yearly-byday": (MON, "RRULE:FREQ=YEARLY;BYDAY=1MO", "2026-10-05 2027-01-04 2028-01-03 2029-01-01"),
    # What the RFC forbids or leaves open, and dateutil reads.
    "a-weekly-bymonthday": (MON, "RRULE:FREQ=WEEKLY;BYMONTHDAY=5", "2026-10-05 2026-11-05 2026-12-05 2027-01-05"),
    "a-weekly-ordinal-byday": (MON, "RRULE:FREQ=WEEKLY;BYDAY=1MO", "2026-10-05 2026-10-12 2026-10-19 2026-10-26"),
    "a-start-not-in-its-rule": (MON, "RRULE:FREQ=WEEKLY;BYDAY=TU", "2026-10-05 2026-10-06 2026-10-13 2026-10-20"),
    # How it ends, or doesn't.
    "a-count": (MON, "RRULE:FREQ=WEEKLY;COUNT=3", "2026-10-05 2026-10-12 2026-10-19"),
    "a-count-of-none": (MON, "RRULE:FREQ=WEEKLY;COUNT=0", "2026-10-05"),
    "a-count-in-words": (MON, "RRULE:FREQ=WEEKLY;COUNT=many", "2026-10-05 2026-10-12 2026-10-19 2026-10-26"),
    "a-huge-count": (MON, "RRULE:FREQ=WEEKLY;COUNT=999999999", "2026-10-05 2026-10-12 2026-10-19 2026-10-26"),
    "an-until-in-words": (MON, "RRULE:FREQ=WEEKLY;UNTIL=soon", "2026-10-05 2026-10-12 2026-10-19 2026-10-26"),
    "an-until-before-it-starts": (MON, "RRULE:FREQ=WEEKLY;UNTIL=20261001T000000", ""),
    "an-until-in-year-9999": (MON, "RRULE:FREQ=WEEKLY;UNTIL=99991231T235959Z", "2026-10-05 2026-10-12 2026-10-19 2026-10-26"),
    "an-interval-of-none": (MON, "RRULE:FREQ=DAILY;INTERVAL=0", "2026-10-05 2026-10-06 2026-10-07 2026-10-08"),
    "a-negative-interval": (MON, "RRULE:FREQ=DAILY;INTERVAL=-3", "2026-10-05 2026-10-06 2026-10-07 2026-10-08"),
    "an-interval-past-year-9999": (MON, "RRULE:FREQ=YEARLY;INTERVAL=9000", "2026-10-05"),
    # A rule it won't expand has no next date, and the Agenda falls back on its first.
    "secondly": (MON, "RRULE:FREQ=SECONDLY", ""),
    "minutely": (MON, "RRULE:FREQ=MINUTELY;INTERVAL=5", ""),
    "hourly": (MON, "RRULE:FREQ=HOURLY", ""),
    "a-frequency-in-words": (MON, "RRULE:FREQ=FORTNIGHTLY", ""),
    "no-frequency": (MON, "RRULE:BYDAY=MO", ""),
    "a-weekday-in-words": (MON, "RRULE:FREQ=WEEKLY;BYDAY=XX", ""),
    "an-interval-in-words": (MON, "RRULE:FREQ=WEEKLY;INTERVAL=often", ""),
    "a-part-nobody-knows": (MON, "RRULE:FREQ=WEEKLY;X-EXAMPLE=1", ""),
    # RDATE, on its own and beside a rule. A period gives its start: an occurrence runs as long as the event does.
    "an-rdate": (MON, "RDATE:20261105T100000", "2026-10-05 2026-11-05"),
    "an-rdate-beside-a-rule": (MON, "RRULE:FREQ=WEEKLY;COUNT=2\r\nRDATE:20261201T100000", "2026-10-05 2026-10-12 2026-12-01"),
    "an-rdate-period": (MON, "RDATE;VALUE=PERIOD:20261105T100000/PT2H", "2026-10-05 2026-11-05"),
    "an-rdate-struck": (MON, "RDATE:20261105T100000,20261112T100000\r\nEXDATE:20261105T100000", "2026-10-05 2026-11-12"),
    "an-rdate-of-a-whole-day": ("DTSTART;VALUE=DATE:20261005", "RDATE;VALUE=DATE:20261105", "2026-10-05 2026-11-05"),
    "an-rdate-in-its-zone": ("DTSTART;TZID=Europe/Berlin:20261005T100000", "RDATE;TZID=Europe/Berlin:20261105T100000,20261201T100000", "2026-10-05 2026-11-05 2026-12-01"),
}


@pytest.mark.parametrize("start, rest, dates", SERIES.values(), ids=SERIES.keys())
def test_what_a_series_comes_to(start, rest, dates):
    assert _days(start, *rest.split("\r\n")) == dates.split()


def test_a_rule_that_never_comes_to_a_date_comes_to_none(monkeypatch):
    """dateutil would look for a February 30 through every year it can reach: the reader's own budget is what ends it."""
    monkeypatch.setattr(ics, "_EXPAND_SECONDS", _BUDGET)
    assert _days("DTSTART:20261105T100000", "RRULE:FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=30") == []


def test_a_rule_can_change_the_time_of_day():
    def at(rule):
        return [occ[0].isoformat(timespec="minutes") for occ in _walk(ics.parse(cal(MON, MON_END, f"RRULE:{rule}")))]
    assert at("FREQ=DAILY;BYHOUR=10,16") == ["2026-10-05T10:00", "2026-10-05T16:00", "2026-10-06T10:00", "2026-10-06T16:00"]
    assert at("FREQ=DAILY;BYMINUTE=0,30") == ["2026-10-05T10:00", "2026-10-05T10:30", "2026-10-06T10:00", "2026-10-06T10:30"]


def test_a_series_keeps_its_clock_where_the_clocks_change():
    def at(start, *when):
        inv = ics.parse(cal(start, "RRULE:FREQ=WEEKLY"))
        return [ics.next_occurrence(inv, after)[0].isoformat(timespec="minutes") for after in when]
    assert at("DTSTART;TZID=Europe/Berlin:20261018T023000", datetime(2026, 10, 24, 12), datetime(2026, 10, 26, 12)) \
        == ["2026-10-25T02:30+02:00", "2026-11-01T02:30+01:00"]                                     # the hour said twice: the first of them
    assert at("DTSTART;TZID=Europe/Berlin:20270321T023000", datetime(2027, 3, 27, 12), datetime(2027, 3, 29, 12)) \
        == ["2027-03-28T02:30+01:00", "2027-04-04T02:30+02:00"]                                     # the hour that isn't there: an hour on


# ── a moved occurrence, sent with its series ──────────────────────────────────

_THE_SERIES = ("BEGIN:VEVENT", "UID:s@example.test", "SUMMARY:Standup", MON, MON_END, "RRULE:FREQ=WEEKLY", "END:VEVENT")
_ITS_MOVE = ("BEGIN:VEVENT", "UID:s@example.test", "SUMMARY:Standup (moved)", "RECURRENCE-ID:20261102T100000", "DTSTART:20261103T150000",
             "DTEND:20261103T153000", "END:VEVENT")


@pytest.mark.parametrize("parts", [_THE_SERIES + _ITS_MOVE, _ITS_MOVE + _THE_SERIES], ids=["series-first", "move-first"])
def test_a_moved_occurrence_takes_its_slot_from_the_series_in_either_order(parts):
    text = "\r\n".join(["BEGIN:VCALENDAR", "VERSION:2.0", "METHOD:REQUEST", *parts, "END:VCALENDAR", ""])
    assert _listed(text) == ["Standup (moved) 2026-11-03T15:00 to 2026-11-03T15:30", "Standup 2026-11-09T10:00 to 2026-11-09T10:30"]
    assert ics.parse(text)["summary"] == "Standup"


def test_a_revision_count_is_read_and_what_wont_read_is_none():
    assert ics.parse(cal(MON, "SEQUENCE:4"))["sequence"] == 4
    assert ics.parse(cal(MON))["sequence"] == 0
    for said in ("SEQUENCE:later", "SEQUENCE:-3", "SEQUENCE:" + "9" * 5000, f"SEQUENCE:{2 ** 31}"):
        assert ics.parse(cal(MON, said))["sequence"] == 0, said


@pytest.mark.parametrize("line", ["RECURRENCE-ID;RANGE=THISANDFUTURE:20261102T100000", "RECURRENCE-ID;RANGE=thisandfuture:20261102T100000"])
def test_a_range_says_so_and_only_where_it_said_it(line):
    assert ics.parse(cal(MON, line))["this_and_future"] is True


@pytest.mark.parametrize("lines", [(), ("RECURRENCE-ID:20261102T100000",), ("RECURRENCE-ID;RANGE=THISANDPRIOR:20261102T100000",), ("RECURRENCE-ID;RANGE=THISANDFUTURE:x",)])
def test_nothing_else_says_a_range(lines):
    assert "this_and_future" not in ics.parse(cal(MON, *lines))


_ROOT = ["BEGIN:VEVENT", "UID:s@example.test", "SUMMARY:Standup", MON, MON_END, "RRULE:FREQ=WEEKLY", "END:VEVENT"]


def _moved_on(summary, recurrence_id, start, end, *rest):
    return ["BEGIN:VEVENT", "UID:s@example.test", f"SUMMARY:{summary}", f"RECURRENCE-ID;RANGE=THISANDFUTURE:{recurrence_id}",
            f"DTSTART:{start}", f"DTEND:{end}", *rest, "END:VEVENT"]


def _series(*parts, method="REQUEST"):
    return "\r\n".join(["BEGIN:VCALENDAR", "VERSION:2.0", f"METHOD:{method}", *[line for part in parts for line in part], "END:VCALENDAR", ""])


def test_this_and_future_moves_every_occurrence_from_that_one_on():
    text = _series(_ROOT, _moved_on("Standup (later)", "20261116T100000", "20261116T150000", "20261116T153000"))
    assert _listed(text) == ["Standup 2026-11-02T10:00 to 2026-11-02T10:30", "Standup (later) 2026-11-16T15:00 to 2026-11-16T15:30"]
    after = Agenda(_Stored(text), ics).upcoming(now=datetime(2026, 11, 17, 12), days=4000)["items"]
    assert [(x["summary"], x["start"]) for x in after] == [("Standup (later)", "2026-11-23T15:00")]


def test_once_the_old_part_is_over_the_new_part_goes_on_week_on_week():
    text = _series(_ROOT, _moved_on("Standup (later)", "20261005T100000", "20261005T150000", "20261005T160000"))
    assert _listed(text) == ["Standup (later) 2026-11-02T15:00 to 2026-11-02T16:00"]


def test_this_and_future_may_move_to_another_day_as_the_rule_still_counts_mondays():
    text = _series(_ROOT, _moved_on("Standup (Wed)", "20261005T100000", "20261007T100000", "20261007T103000"))
    assert _listed(text) == ["Standup (Wed) 2026-11-04T10:00 to 2026-11-04T10:30"]


def test_this_and_future_may_move_earlier():
    text = _series(_ROOT, _moved_on("Standup (early)", "20261005T100000", "20261005T080000", "20261005T083000"))
    assert _listed(text) == ["Standup (early) 2026-11-02T08:00 to 2026-11-02T08:30"]


def test_a_series_that_ran_out_stays_run_out_after_a_range_moves_it():
    text = _series(["BEGIN:VEVENT", "UID:s@example.test", "SUMMARY:Standup", MON, MON_END, "RRULE:FREQ=WEEKLY;COUNT=5", "END:VEVENT"],
                   _moved_on("Standup (later)", "20261019T100000", "20261019T150000", "20261019T153000"))
    assert _listed(text) == ["Standup (later) 2026-11-02T15:00 to 2026-11-02T15:30"]
    assert Agenda(_Stored(text), ics).upcoming(now=datetime(2026, 11, 3, 12), days=4000)["items"] == []


def test_the_rule_cuts_at_the_first_of_two_ranges_and_each_runs_to_the_next():
    text = _series(_ROOT, _moved_on("Later", "20261109T100000", "20261109T150000", "20261109T153000"),
                   _moved_on("Later still", "20261123T100000", "20261123T170000", "20261123T173000"))
    assert _listed(text) == ["Standup 2026-11-02T10:00 to 2026-11-02T10:30", "Later 2026-11-09T15:00 to 2026-11-09T15:30",
                             "Later still 2026-11-23T17:00 to 2026-11-23T17:30"]
    later = Agenda(_Stored(text), ics).upcoming(now=datetime(2026, 11, 17, 12), days=4000)["items"]
    assert [(x["summary"], x["start"]) for x in later] == [("Later still", "2026-11-23T17:00")]


def test_a_range_from_the_first_occurrence_leaves_nothing_of_the_old_one():
    assert _listed(_series(_ROOT, _moved_on("Later", "20261005T100000", "20261005T150000", "20261005T153000"))) == ["Later 2026-11-02T15:00 to 2026-11-02T15:30"]


def test_an_occurrence_moved_on_its_own_after_a_range_is_still_struck_from_it():
    alone = ("BEGIN:VEVENT", "UID:s@example.test", "SUMMARY:Just once", "RECURRENCE-ID:20261102T100000", "DTSTART:20261103T090000", "DTEND:20261103T093000", "END:VEVENT")
    text = _series(_ROOT, _moved_on("Later", "20261005T100000", "20261005T150000", "20261005T153000"), alone)
    assert _listed(text) == ["Just once 2026-11-03T09:00 to 2026-11-03T09:30", "Later 2026-11-09T15:00 to 2026-11-09T15:30"]


def test_a_cancelled_range_ends_the_series_there():
    gone = ["BEGIN:VEVENT", "UID:s@example.test", "SUMMARY:Standup", "RECURRENCE-ID;RANGE=THISANDFUTURE:20261109T100000", "DTSTART:20261109T100000", "STATUS:CANCELLED", "END:VEVENT"]
    text = _series(_ROOT, gone)
    assert _listed(text) == ["Standup 2026-11-02T10:00 to 2026-11-02T10:30"]
    assert Agenda(_Stored(text), ics).upcoming(now=datetime(2026, 11, 3, 12), days=4000)["items"] == []


def test_a_range_with_no_series_beside_it_is_a_word_about_one_date():
    assert _listed(_series(_moved_on("Later", "20261109T100000", "20261109T150000", "20261109T153000"))) == ["Later 2026-11-09T15:00 to 2026-11-09T15:30"]


def test_a_range_over_a_cancelled_series_is_gone_with_it():
    text = _series(["BEGIN:VEVENT", "UID:s@example.test", "SUMMARY:Standup", MON, "RRULE:FREQ=WEEKLY", "STATUS:CANCELLED", "END:VEVENT"],
                   _moved_on("Later", "20261109T100000", "20261109T150000", "20261109T153000"))
    assert _listed(text) == []


def test_a_range_in_a_zone_keeps_its_wall_clock_across_a_change_of_clocks():
    root = ["BEGIN:VEVENT", "UID:s@example.test", "SUMMARY:Standup", "DTSTART;TZID=W. Europe Standard Time:20261005T100000",
            "DTEND;TZID=W. Europe Standard Time:20261005T103000", "RRULE:FREQ=WEEKLY", "END:VEVENT"]
    moved = ["BEGIN:VEVENT", "UID:s@example.test", "SUMMARY:Later", "RECURRENCE-ID;RANGE=THISANDFUTURE;TZID=W. Europe Standard Time:20261012T100000",
             "DTSTART;TZID=W. Europe Standard Time:20261012T120000", "DTEND;TZID=W. Europe Standard Time:20261012T123000", "END:VEVENT"]
    [_series_part, (_, later)] = agenda.cut_up([(1, inv) for inv in ics.events(_series(root, moved))])
    assert [d.isoformat() for d in ics.next_occurrence(later, datetime(2026, 11, 1, 12))] == ["2026-11-02T12:00:00+01:00", "2026-11-02T12:30:00+01:00"]


def test_a_moved_occurrence_names_the_slot_it_left_in_its_zone():
    inv = ics.parse(cal("DTSTART;TZID=Europe/Berlin:20261110T150000", "RECURRENCE-ID;TZID=Europe/Berlin:20261109T100000"))
    assert (inv["start_iso"], inv["recurrence_id"]) == ("2026-11-10T15:00:00+01:00", "2026-11-09T10:00:00+01:00")

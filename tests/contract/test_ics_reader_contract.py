"""The reader of invites' contract: `icalendar_acl`, on `icalendar` and `recurring-ical-events`. An invite is a stranger's text,
read on request threads: nothing raises however broken it is, what comes back is plain data, and no text or rule costs more than a
bound, in time or in memory. tests/unit/calendar/test_invite_text.py says what a text comes to. All addresses are fictional.
"""
import gc
import json
import sys
import time
import tracemalloc
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from dateutil.rrule import DAILY, rrule
from hypothesis import given, strategies as st
from icalendar.timezone import tzp

from mailmind.bootstrap.calendar import ics
from mailmind.calendar.adapters import icalendar_acl
from mailmind.calendar.domain import invite, rsvp
from property_runs import runs

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
CPU_SECONDS = 1.0           # far above either budget, far below what any bomb here costs without one
MEMORY_BYTES = 32_000_000


def cal(*lines):
    return "\r\n".join(["BEGIN:VCALENDAR", "VERSION:2.0", "METHOD:REQUEST", *lines, "END:VCALENDAR", ""])


def event(*lines, uid="u"):
    return cal("BEGIN:VEVENT", f"UID:{uid}", "SUMMARY:Standup", *lines, "END:VEVENT")


FULL = ["BEGIN:VCALENDAR", "PRODID:-//Example//EN", "VERSION:2.0", "METHOD:REQUEST", "BEGIN:VTIMEZONE", "TZID:Europe/Berlin",
        "BEGIN:STANDARD", "DTSTART:19701025T030000", "TZOFFSETFROM:+0200", "TZOFFSETTO:+0100", "RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU",
        "END:STANDARD", "END:VTIMEZONE", "BEGIN:VEVENT", "UID:full@example.test", "SEQUENCE:2", "DTSTAMP:20261001T080000Z",
        "SUMMARY:Studio sync\\, weekly", "LOCATION:Roof room", "DESCRIPTION:Bring\\nnotes", "STATUS:CONFIRMED",
        "DTSTART;TZID=Europe/Berlin:20261005T100000", "DTEND;TZID=Europe/Berlin:20261005T103000", "RRULE:FREQ=WEEKLY;BYDAY=MO;COUNT=9",
        "EXDATE;TZID=Europe/Berlin:20261012T100000", "ORGANIZER;CN=Ren Takeda:mailto:ren@example.test",
        'ATTENDEE;CN="Ada, L.";PARTSTAT=NEEDS-ACTION:mailto:ada@example.test', "BEGIN:VALARM", "ACTION:AUDIO", "TRIGGER:-PT15M",
        "END:VALARM", "END:VEVENT", "END:VCALENDAR", ""]

GARBAGE = {
    "empty": "", "space": " ", "line-breaks": "\r\n\r\n", "words": "hello there", "a-colon": ":", "a-semicolon": ";", "colons": ":::",
    "begin-alone": "BEGIN", "begin-of-nothing": "BEGIN:", "begin-calendar": "BEGIN:VCALENDAR", "end-calendar": "END:VCALENDAR",
    "begin-event": "BEGIN:VEVENT", "end-event": "END:VEVENT", "event-alone": "BEGIN:VEVENT\r\nUID:a\r\nEND:VEVENT",
    "only-ends": "END:VEVENT\r\nEND:VEVENT\r\nEND:VCALENDAR", "ends-crossed": "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nEND:VCALENDAR\r\nEND:VEVENT",
    "wrong-end": "BEGIN:VCALENDAR\r\nEND:VEVENT\r\nEND:VCALENDAR", "cut-short": "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nDTSTART:2026",
    "control-bytes": "\x00\x01\x02\x7f", "a-nul-in-a-line": "BEGIN:VCALENDAR\x00\r\nBEGIN:VEVENT\r\nUID:\x00\r\nEND:VEVENT\r\nEND:VCALENDAR",
    "lone-surrogate": "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:\ud800\r\nEND:VEVENT\r\nEND:VCALENDAR", "bom": "﻿", "a-long-word": "a" * 100_000,
    "nested-deep": "BEGIN:VEVENT\r\n" * 3000, "nested-calendars": "BEGIN:VCALENDAR\r\n" * 3000, "folds": " x\r\n" * 3000,
    "empty-values": event("DTSTART:", "DTEND:", "RRULE:", "EXDATE:", "RDATE:", "DURATION:", "ORGANIZER:", "ATTENDEE:", "RECURRENCE-ID:"),
    "no-values": event("DTSTART", "DTEND", "RRULE", "EXDATE", "RDATE", "DURATION", "ORGANIZER", "ATTENDEE", "RECURRENCE-ID"),
    "bad-start": event("DTSTART:99999999T999999"), "short-start": event("DTSTART:0"), "month-13": event("DTSTART:20261302T100000"),
    "start-in-letters": event("DTSTART:tomorrow"), "date-and-time-mixed": event("DTSTART;VALUE=DATE:20261102T100000"),
    "tzid-a-path": event("DTSTART;TZID=../../etc/passwd:20261102T100000"), "tzid-empty": event("DTSTART;TZID=:20261102T100000"),
    "tzid-nul": event("DTSTART;TZID=\x00:20261102T100000"), "rule-in-letters": event("DTSTART:20261102T100000", "RRULE:every monday"),
    "rule-half": event("DTSTART:20261102T100000", "RRULE:FREQ"), "rule-of-nothing": event("DTSTART:20261102T100000", "RRULE:=;=;="),
    "interval-letters": event("DTSTART:20261102T100000", "RRULE:FREQ=DAILY;INTERVAL=x"), "count-letters": event("DTSTART:20261102T100000", "RRULE:FREQ=DAILY;COUNT=x"),
    "until-letters": event("DTSTART:20261102T100000", "RRULE:FREQ=DAILY;UNTIL=x"), "byday-letters": event("DTSTART:20261102T100000", "RRULE:FREQ=WEEKLY;BYDAY=XX,99,!!"),
    "bad-exdate": event("DTSTART:20261102T100000", "RRULE:FREQ=WEEKLY", "EXDATE:x,,20261109T100000,y"),
    "bad-rdate": event("DTSTART:20261102T100000", "RDATE:x,,20261109T100000,y"), "bad-duration": event("DTSTART:20261102T100000", "DURATION:PxT"),
    "huge-duration": event("DTSTART:20261102T100000", "DURATION:P99999999W"), "end-before-start": event("DTSTART:20261102T100000", "DTEND:20261101T100000"),
    "only-a-name": event("ATTENDEE;CN=", "ORGANIZER;CN=\"unterminated:mailto:x"), "valarm-broken": event("DTSTART:20261102T100000", "BEGIN:VALARM", "TRIGGER:x", "END:VALARM"),
    "year-1": event("DTSTART:00010101T000000Z"), "year-9999": event("DTSTART:99991231T235959Z", "RRULE:FREQ=DAILY"),
    "year-0": event("DTSTART:00000101T000000"),
    "sequence-letters": event("DTSTART:20261102T100000", "SEQUENCE:later"), "sequence-empty": event("DTSTART:20261102T100000", "SEQUENCE:"),
    "sequence-negative": event("DTSTART:20261102T100000", "SEQUENCE:-3"), "sequence-past-any-int": event("DTSTART:20261102T100000", "SEQUENCE:" + "9" * 5000),
}


@pytest.mark.parametrize("text", GARBAGE.values(), ids=GARBAGE.keys())
def test_no_broken_text_makes_the_reader_raise(text, monkeypatch):
    monkeypatch.setattr(icalendar_acl, "_READ_SECONDS", 30.0)      # the budget has tests of its own: a busy machine mustn't decide this
    found, card = ics.events(text), ics.parse(text)
    assert isinstance(found, list)
    assert (card is None) == (not found)
    assert card is None or card in found
    for inv in found:
        ics.next_occurrence(inv, NOW)
        ics.next_occurrence(inv, datetime(2026, 11, 1, 12, 0))


@pytest.mark.parametrize("inv", [{}, {"start_iso": ""}, {"start_iso": "x"}, {"start_iso": "2026-11-02T10:00:00", "end_iso": "x"},
                                 {"start_iso": "2026-11-02T10:00:00", "rrule": "x"}, {"start_iso": "2026-11-02T10:00:00+01:00", "tzid": "x"}])
def test_a_broken_invite_raises_nothing_when_asked_when_it_next_happens(inv):
    ics.next_occurrence(inv, NOW)


_LINES = FULL + [r"UID:x\ny", r"ORGANIZER;CN=a^nb:mailto:c\nd@example.test", r"ATTENDEE;CN=a^nb:mailto:ada@example.test", r"SUMMARY:a\nb", r"LOCATION:a\,b",
                 "DTSTART;TZID=W. Europe Standard Time:20261005T100000", "SEQUENCE:12"]


def _mangled():
    """A real invite, with its lines dropped, moved, cut, or swapped for other text."""
    line = st.sampled_from(_LINES) | st.sampled_from(_LINES).flatmap(lambda s: st.integers(0, len(s)).map(lambda at: s[:at])) \
        | st.sampled_from(_LINES).flatmap(lambda s: st.text(max_size=8).map(lambda x: s + x)) | st.text(max_size=20)
    return st.lists(line, max_size=30).map("\r\n".join)


def _plain(value):
    """Is this made of what json makes, and nothing that merely behaves like it?"""
    if isinstance(value, dict):
        return type(value) is dict and all(type(k) is str and _plain(v) for k, v in value.items())
    if isinstance(value, list):
        return type(value) is list and all(_plain(v) for v in value)
    return type(value) in (str, bool, int, type(None))


@given(text=st.text(max_size=300) | _mangled())
@runs(120)
def test_whatever_the_text_says_the_reader_answers_in_plain_data(text):
    found, card = ics.events(text), ics.parse(text)
    assert (card is None) == (not found)
    assert card is None or card in found
    assert _plain(found)
    json.dumps(found)
    for inv in found:
        occ = ics.next_occurrence(inv, NOW)
        assert occ is None or (type(occ[0]) is datetime and type(occ[1]) is datetime and occ[1] >= occ[0])
        reply = rsvp.build_reply(inv, "ada@example.test", rsvp.name_for(inv, "ada@example.test", "Ada"), "ACCEPTED")
        assert not set("\r\n") & set(reply.replace("\r\n", "")), reply


def test_an_escape_in_a_uid_or_an_address_stays_as_it_was_said():
    inv = ics.parse(cal("BEGIN:VEVENT", r"UID:a\nb\,c@example.test", "DTSTART:20261102T100000", r"ORGANIZER:mailto:ren\nX-Evil:1@example.test",
                        r"ATTENDEE:mailto:bo\nX-Evil:1@example.test", "END:VEVENT"))
    assert inv["uid"] == r"a\nb\,c@example.test"
    assert inv["organizer"]["email"] == r"ren\nX-Evil:1@example.test"
    assert inv["attendees"][0]["email"] == r"bo\nX-Evil:1@example.test"


def test_a_name_stays_on_one_line_whatever_it_says():
    inv = ics.parse(cal("BEGIN:VEVENT", "UID:u", "DTSTART:20261102T100000", 'ORGANIZER;CN="Ren^nX-Evil":mailto:ren@example.test',
                        'ATTENDEE;CN="Ada^nX-Evil":mailto:ada@example.test', "END:VEVENT"))
    assert not set("\r\n") & set(inv["organizer"]["name"] + inv["attendees"][0]["name"])


def test_nothing_in_an_invite_puts_a_line_of_its_own_in_an_rsvp():
    inv = ics.parse(cal("BEGIN:VEVENT", r"UID:a\nX-Evil:1", "DTSTART:20261102T100000", r"SUMMARY:s\nX-Evil:1",
                        r'ORGANIZER;CN="Ren^nX-Evil:1":mailto:ren\nX-Evil:1@example.test', 'ATTENDEE;CN="Ada^nX-Evil:1":mailto:ada@example.test', "END:VEVENT"))
    reply = rsvp.build_reply(inv, "ada@example.test", rsvp.name_for(inv, "ada@example.test", "Ada"), "ACCEPTED")
    assert not [line for line in reply.replace("\r\n ", "").split("\r\n") if line.startswith("X-Evil")]
    assert not set("\r\n") & set(reply.replace("\r\n", ""))


def test_an_invite_has_these_fields():
    assert set(ics.parse("\r\n".join(FULL))) == {"summary", "location", "description", "when", "all_day", "start_iso", "end_iso",
                                                  "organizer", "attendees", "recurrence", "method", "status", "uid", "rrule",
                                                  "exdates", "recurrence_id", "tzid", "sequence"}


def test_what_the_reader_gives_is_plain_data_nothing_of_a_library():
    inv = ics.parse("\r\n".join(FULL))
    assert _plain(inv)
    assert (inv["summary"], inv["description"]) == ("Studio sync, weekly", "Bring\nnotes")
    assert inv["attendees"] == [{"name": "Ada, L.", "email": "ada@example.test"}]
    assert inv["rrule"] == "FREQ=WEEKLY;BYDAY=MO;COUNT=9"


@pytest.mark.parametrize("lines, zone_module", [
    (("DTSTART;TZID=Europe/Berlin:20261105T100000",), "zoneinfo"),
    (("DTSTART;TZID=Europe/Berlin:20261005T100000", "RRULE:FREQ=WEEKLY"), "zoneinfo"),
    (("DTSTART:20261105T080000Z",), "datetime"),
    (("DTSTART:20261005T080000Z", "RRULE:FREQ=WEEKLY"), "datetime"),
])
def test_a_next_occurrence_is_plain_datetimes_in_a_standard_zone(lines, zone_module):
    occ = ics.next_occurrence(ics.parse(event(*lines)), NOW)
    assert occ is not None
    for each in occ:
        assert type(each) is datetime
        assert type(each.tzinfo).__module__ == zone_module


def test_a_floating_next_occurrence_is_a_plain_naive_datetime():
    occ = ics.next_occurrence(ics.parse(event("DTSTART:20261005T100000", "RRULE:FREQ=WEEKLY")), NOW)
    assert occ[0] == datetime(2026, 11, 2, 10, 0) and occ[0].tzinfo is None


def test_an_all_day_next_occurrence_is_a_datetime_not_a_date():
    occ = ics.next_occurrence(ics.parse(event("DTSTART;VALUE=DATE:20261005", "RRULE:FREQ=WEEKLY")), NOW)
    assert type(occ[0]) is datetime and occ[0] == datetime(2026, 11, 2)
    assert occ[1] - occ[0] == timedelta(days=1)


def test_events_holds_the_card_and_empty_for_no_calendar():
    assert ics.events("no calendar here") == []
    text = "\r\n".join(FULL)
    assert ics.parse(text) in ics.events(text)


# ── what a TZID means ─────────────────────────────────────────────────────────

def test_a_zone_the_database_knows_is_that_zone():
    inv = ics.parse(event("DTSTART;TZID=Europe/Berlin:20261102T100000"))
    assert (inv["start_iso"], inv["tzid"]) == ("2026-11-02T10:00:00+01:00", "Europe/Berlin")


@pytest.mark.parametrize("tzid", ["Nowhere/Land", "../../etc/passwd", "/etc/passwd", "", "Europe", "x" * 500])
def test_an_unknown_tzid_makes_the_time_floating(tzid):
    inv = ics.parse(event(f"DTSTART;TZID={tzid}:20261102T100000", f"DTEND;TZID={tzid}:20261102T103000"))
    assert (inv["start_iso"], inv["end_iso"], inv["tzid"]) == ("2026-11-02T10:00:00", "2026-11-02T10:30:00", "")
    assert ics.next_occurrence(inv, NOW)[0] == datetime(2026, 11, 2, 10, 0)


@pytest.mark.parametrize("tzid", ["Customized Time Zone", "Customized Time Zone 1", "W. Europe Standard Tim", "/freeassociation.sourceforge.net/Berlin", "/a/b/c/d/e/Standup"])
def test_a_name_only_a_vtimezone_or_a_guess_could_explain_floats_too(tzid):
    """A VTIMEZONE doesn't count, and one rule says what a TZID means (`invite.zone`): the zone database's names, Windows' and a name
    from a registry that ends in one."""
    zone = ["BEGIN:VTIMEZONE", f"TZID:{tzid}", "BEGIN:STANDARD", "DTSTART:19701025T030000", "TZOFFSETFROM:+0200", "TZOFFSETTO:+0100", "END:STANDARD", "END:VTIMEZONE"]
    inv = ics.parse(cal(*zone, "BEGIN:VEVENT", "UID:u", f"DTSTART;TZID={tzid}:20261102T100000", "END:VEVENT"))
    assert (inv["start_iso"], inv["tzid"]) == ("2026-11-02T10:00:00", "")


@pytest.mark.parametrize("tzid, start, zone_name", [("W. Europe Standard Time", "2026-11-02T10:00:00+01:00", "W. Europe Standard Time"),
                                                    ("Pacific Standard Time", "2026-11-02T10:00:00-08:00", "Pacific Standard Time"),
                                                    ("/freeassociation.sourceforge.net/Europe/Berlin", "2026-11-02T10:00:00+01:00", "/freeassociation.sourceforge.net/Europe/Berlin"),
                                                    ("/softwarestudio.org/Olson_20011030_5/America/New_York", "2026-11-02T10:00:00-05:00", "/softwarestudio.org/Olson_20011030_5/America/New_York"),
                                                    ("/mozilla.org/20071231_1/America/Argentina/Buenos_Aires", "2026-11-02T10:00:00-03:00", "/mozilla.org/20071231_1/America/Argentina/Buenos_Aires")])
def test_a_windows_name_or_one_from_a_registry_is_the_zone_it_names(tzid, start, zone_name):
    """Whatever the invite's own VTIMEZONE says of it: this one, with an offset no such zone has, doesn't move it."""
    wrong = ["BEGIN:VTIMEZONE", f"TZID:{tzid}", "BEGIN:STANDARD", "DTSTART:19701025T030000", "TZOFFSETFROM:+0500", "TZOFFSETTO:+0500", "END:STANDARD", "END:VTIMEZONE"]
    inv = ics.parse(cal(*wrong, "BEGIN:VEVENT", "UID:u", f"DTSTART;TZID={tzid}:20261102T100000", f"DTEND;TZID={tzid}:20261102T103000", "END:VEVENT"))
    assert (inv["start_iso"], inv["tzid"]) == (start, zone_name)
    assert ics.next_occurrence(inv, NOW)[0].isoformat() == start


def test_every_windows_name_is_a_zone_this_machine_can_load():
    for name, iana in invite.WINDOWS_ZONES.items():
        assert invite.zone(name) == ZoneInfo(iana), name


def _zone_block(tzid, *rule, offset="+0000"):
    return ["BEGIN:VTIMEZONE", f"TZID:{tzid}", "BEGIN:STANDARD", "DTSTART:19700101T000000", f"TZOFFSETFROM:{offset}", f"TZOFFSETTO:{offset}", *rule, "END:STANDARD", "END:VTIMEZONE"]


def test_what_a_stranger_calls_a_zone_is_not_remembered():
    ics.parse(cal(*_zone_block("Stranger/Time", offset="+0500"), "BEGIN:VEVENT", "UID:a", "DTSTART;TZID=Stranger/Time:20261102T100000", "END:VEVENT"))
    inv = ics.parse(event("DTSTART;TZID=Stranger/Time:20261102T100000"))
    assert (inv["start_iso"], inv["tzid"]) == ("2026-11-02T10:00:00", "")


def test_nothing_of_a_vtimezone_is_kept():
    """A name the sender chose, in a dict the process keeps."""
    ics.parse(cal(*_zone_block("Stranger/Kept", offset="+0500"), "BEGIN:VEVENT", "UID:a", "DTSTART:20261102T100000", "END:VEVENT"))
    assert tzp.timezone("Stranger/Kept") is None


def test_a_vtimezone_cant_change_what_a_real_zone_means():
    ics.parse(cal(*_zone_block("Europe/Berlin", offset="+0500"), "BEGIN:VEVENT", "UID:a", "DTSTART;TZID=Europe/Berlin:20261102T100000", "END:VEVENT"))
    inv = ics.parse(event("DTSTART;TZID=Europe/Berlin:20261102T100000"))
    assert inv["start_iso"] == "2026-11-02T10:00:00+01:00"


# ── what it costs ─────────────────────────────────────────────────────────────

_HOURS, _MINUTES = ",".join(map(str, range(24))), ",".join(map(str, range(60)))
EVERY_SECOND = f"BYHOUR={_HOURS};BYMINUTE={_MINUTES};BYSECOND={_MINUTES}"
HOSTILE_RULES = {
    "secondly": "FREQ=SECONDLY", "minutely": "FREQ=MINUTELY", "hourly": "FREQ=HOURLY", "secondly-counted": "FREQ=SECONDLY;COUNT=999999999",
    "secondly-for-ever": "FREQ=SECONDLY;UNTIL=99991231T235959Z", "huge-count": "FREQ=DAILY;COUNT=999999999999", "far-until": "FREQ=DAILY;UNTIL=99991231T235959Z",
    "never-fires": "FREQ=DAILY;BYMONTH=2;BYMONTHDAY=30", "never-fires-yearly": "FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=31", "never-fires-weekly": "FREQ=WEEKLY;BYMONTH=2;BYMONTHDAY=30;BYDAY=MO",
    "interval-zero": "FREQ=DAILY;INTERVAL=0", "interval-negative": "FREQ=DAILY;INTERVAL=-1", "interval-huge": "FREQ=DAILY;INTERVAL=99999999999",
    "every-second-of-a-day": f"FREQ=DAILY;{EVERY_SECOND}", "every-second-of-a-year": f"FREQ=YEARLY;{EVERY_SECOND}", "every-second-counted": f"FREQ=YEARLY;{EVERY_SECOND};COUNT=999999999",
    "setpos-far": "FREQ=MONTHLY;BYDAY=MO;BYSETPOS=300", "long-list": "FREQ=DAILY;BYMONTHDAY=" + ",".join(["1", "2"] * 20_000),
    "a-week-of-years": "FREQ=YEARLY;BYWEEKNO=" + ",".join(map(str, range(1, 54))) + ";BYDAY=MO,TU,WE,TH,FR,SA,SU",
}
STARTS = {"recent": "DTSTART:20261005T100000", "year-1": "DTSTART:00010101T000000", "zoned": "DTSTART;TZID=Europe/Berlin:20261005T100000"}


def _costed(work, *args):
    """(its answer, this thread's seconds, the most memory it held at once)."""
    tracemalloc.start()
    began = time.thread_time()
    try:
        answer = work(*args)
        return answer, time.thread_time() - began, tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


@pytest.mark.parametrize("start", STARTS.values(), ids=STARTS.keys())
@pytest.mark.parametrize("rule", HOSTILE_RULES.values(), ids=HOSTILE_RULES.keys())
def test_no_rule_costs_more_than_a_bound(rule, start):
    text = event(start, f"RRULE:{rule}")
    inv, seconds, memory = _costed(ics.parse, text)
    assert seconds < CPU_SECONDS and memory < MEMORY_BYTES, (seconds, memory)
    occ, seconds, memory = _costed(ics.next_occurrence, inv, NOW)
    assert seconds < CPU_SECONDS and memory < MEMORY_BYTES, (seconds, memory)
    assert occ is None or type(occ[0]) is datetime


@pytest.mark.parametrize("rule", ["FREQ=SECONDLY", "FREQ=MINUTELY", "FREQ=HOURLY"])
def test_a_rule_finer_than_a_day_has_no_next_occurrence(rule):
    assert ics.next_occurrence(ics.parse(event("DTSTART:20261005T100000", f"RRULE:{rule}")), NOW) is None


def test_a_vtimezone_that_fires_every_second_costs_nothing():
    text = cal(*_zone_block("Bomb/Zone", "RRULE:FREQ=SECONDLY"), "BEGIN:VEVENT", "UID:a", "DTSTART;TZID=Bomb/Zone:20261102T100000", "END:VEVENT")
    inv, seconds, memory = _costed(ics.parse, text)
    assert seconds < CPU_SECONDS and memory < MEMORY_BYTES, (seconds, memory)
    assert (inv["uid"], inv["start_iso"]) == ("a", "2026-11-02T10:00:00")


def test_a_text_that_is_a_path_to_a_calendar_is_not_opened(tmp_path):
    secret = tmp_path / "secret.ics"
    secret.write_text(event("DTSTART:20261102T100000", uid="from-a-file"))
    assert ics.parse(str(secret)) is None
    assert ics.events(str(secret)) == []


def test_a_series_is_still_answered_after_decades_of_it():
    """What the budget has to leave room for."""
    occ = ics.next_occurrence(ics.parse(event("DTSTART;TZID=Europe/Berlin:19900101T100000", "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR")), NOW)
    assert occ[0] == datetime(2026, 11, 2, 10, 0, tzinfo=ZoneInfo("Europe/Berlin"))


def test_a_date_only_until_takes_in_that_day_and_no_later():
    """Which dateutil, on its own, reads as that midnight."""
    inv = ics.parse(event("DTSTART:20261005T100000", "RRULE:FREQ=WEEKLY;UNTIL=20261102"))
    assert ics.next_occurrence(inv, NOW)[0] == datetime(2026, 11, 2, 10, 0)
    assert ics.next_occurrence(inv, datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)) is None


def test_a_budget_that_runs_out_in_a_garbage_collectors_callback_still_holds(monkeypatch):
    """What's raised in a callback goes nowhere and ends the tracer. Hypothesis has one, which is how this came up."""
    spun = []

    def _step():
        pass

    def callback(_phase, _info):
        if not spun:                                # past the budget, all of it in here
            spun.append(True)
            until = time.thread_time() + 2 * icalendar_acl._EXPAND_SECONDS
            while time.thread_time() < until:
                _step()

    inv = ics.parse(event("DTSTART:20261005T100000", "RRULE:FREQ=DAILY;BYMONTH=2;BYMONTHDAY=30"))
    threshold = gc.get_threshold()
    gc.set_threshold(1)
    gc.callbacks.append(callback)
    try:
        began = time.thread_time()
        assert ics.next_occurrence(inv, NOW) is None
        seconds = time.thread_time() - began
    finally:
        gc.callbacks.remove(callback)
        gc.set_threshold(*threshold)
    assert spun and seconds < CPU_SECONDS, (spun, seconds)


def test_a_budget_that_runs_out_as_an_old_generator_is_closed_still_holds(monkeypatch):
    """A walk an earlier call abandoned waits in a cycle for the collector, who closes it during some later call. What's raised
    there goes nowhere and ends the tracer, which is how a long test session came to run one rule on towards year 9999."""
    spun, dropped = [], []
    walk_on = icalendar_acl._next

    def _step():
        pass

    def callback(phase, _info):
        if phase == "start" and not spun:           # past the budget, just as the old walk is closed
            spun.append(True)
            until = time.thread_time() + 2 * icalendar_acl._EXPAND_SECONDS
            while time.thread_time() < until:
                _step()

    def collecting_as_it_starts(*args):             # with the tracer on, and only now: the old walk has to last until then
        gc.callbacks.append(callback)
        try:
            gc.collect(0)
        finally:
            gc.callbacks.remove(callback)
            gc.enable()
        return walk_on(*args)

    inv = ics.parse(event("DTSTART:20261005T100000", "RRULE:FREQ=DAILY;BYMONTH=2;BYMONTHDAY=30"))
    monkeypatch.setattr(icalendar_acl, "_next", collecting_as_it_starts)
    monkeypatch.setattr(sys, "unraisablehook", dropped.append)
    gc.collect()
    gc.disable()
    old_walk = iter(rrule(DAILY, dtstart=datetime(2026, 1, 1)))
    next(old_walk)
    cycle = [old_walk]
    cycle.append(cycle)
    del old_walk, cycle
    try:
        began = time.thread_time()
        assert ics.next_occurrence(inv, NOW) is None
        seconds = time.thread_time() - began
    finally:
        gc.enable()
    assert spun and not dropped and seconds < CPU_SECONDS, (spun, dropped, seconds)


def test_the_reader_leaves_the_thread_as_it_found_it():
    def other_tracer(_frame, _event, _arg):
        return None

    never = ics.parse(event("DTSTART:20261005T100000", "RRULE:FREQ=DAILY;BYMONTH=2;BYMONTHDAY=30"))
    before = sys.gettrace()
    sys.settrace(other_tracer)
    try:
        for call, args in [(ics.parse, ("\r\n".join(FULL),)), (ics.events, ("nonsense",)), (ics.next_occurrence, (never, NOW))]:
            call(*args)
            assert sys.gettrace() is other_tracer
    finally:
        sys.settrace(before)


# ── more than one event, and what an event may say besides ─────────────────────

TWO = cal("BEGIN:VEVENT", "UID:a", "SUMMARY:First", "DTSTART:20261110T100000", "END:VEVENT",
          "BEGIN:VEVENT", "UID:b", "SUMMARY:Second", "DTSTART:20261111T100000", "END:VEVENT")


def test_every_event_of_a_text_is_read():
    assert [e["summary"] for e in ics.events(TWO)] == ["First", "Second"]
    assert ics.parse(TWO)["summary"] == "First"


def test_the_card_of_a_series_and_its_moved_occurrence_is_the_series():
    text = cal("BEGIN:VEVENT", "UID:a", "SUMMARY:Moved", "RECURRENCE-ID:20261109T100000", "DTSTART:20261110T100000", "END:VEVENT",
               "BEGIN:VEVENT", "UID:a", "SUMMARY:Series", "DTSTART:20261005T100000", "RRULE:FREQ=WEEKLY", "END:VEVENT")
    assert ics.parse(text)["summary"] == "Series"


def test_a_duration_stands_where_there_is_no_end():
    inv = ics.parse(event("DTSTART:20261102T100000", "DURATION:PT45M"))
    assert inv["end_iso"] == "2026-11-02T10:45:00"


def test_an_rdate_is_an_occurrence_and_only_shows_where_there_is_one():
    inv = ics.parse(event("DTSTART:20261005T100000", "DTEND:20261005T103000", "RDATE:20261203T150000"))
    assert inv["rdates"] == ["2026-12-03T15:00:00"]
    assert ics.next_occurrence(inv, NOW) == (datetime(2026, 12, 3, 15, 0), datetime(2026, 12, 3, 15, 30))
    assert "rdates" not in ics.parse(event("DTSTART:20261005T100000"))


def test_an_alarm_doesnt_speak_for_its_event():
    inv = ics.parse(event("DTSTART:20261102T100000", "BEGIN:VALARM", "ACTION:EMAIL", "SUMMARY:Alarm", "DESCRIPTION:Soon",
                              "ATTENDEE:mailto:alarm@example.test", "END:VALARM"))
    assert (inv["summary"], inv["description"], inv["attendees"]) == ("Standup", "", [])


def test_no_more_than_so_many_events_of_one_text_are_read():
    many = [f"BEGIN:VEVENT\r\nUID:e{i}\r\nDTSTART:20261110T100000\r\nEND:VEVENT" for i in range(icalendar_acl._MAX_EVENTS + 5)]
    assert len(ics.events(cal(*many))) == icalendar_acl._MAX_EVENTS


def test_a_text_past_the_cap_is_no_calendar():
    long = event("DTSTART:20261102T100000", "DESCRIPTION:" + "x" * icalendar_acl._MAX_TEXT)
    began = time.thread_time()
    assert ics.parse(long) is None
    assert time.thread_time() - began < CPU_SECONDS


@pytest.mark.parametrize("shape", ["\r\n".join(["DESCRIPTION:x"] * 60_000), "DESCRIPTION:" + "\\," * 450_000, " x\r\n" * 200_000, "EXDATE:" + "x," * 450_000, "ATTENDEE;" + "X=y;" * 200_000 + ":z"],
                         ids=["many-lines", "many-escapes", "many-folds", "many-commas", "many-parameters"])
def test_a_text_within_the_cap_stops_at_the_budget_for_reading(shape):
    began = time.thread_time()
    ics.events(event("DTSTART:20261102T100000", shape))
    assert time.thread_time() - began < 2 * CPU_SECONDS

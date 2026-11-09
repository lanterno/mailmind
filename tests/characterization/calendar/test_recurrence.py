"""Reading an invite: which event of a calendar a card shows, and what COUNT and EXDATE do to a series."""
from datetime import datetime


def _cal(*events):
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0"]
    for ev in events:
        lines += ["BEGIN:VEVENT", *ev, "END:VEVENT"]
    return "\r\n".join(lines + ["END:VCALENDAR", ""])


def _next(rule, *extra, start="20261005T100000", end="20261005T103000", after=datetime(2026, 11, 1, 12, 0)):
    from mailmind.bootstrap.calendar import ics
    head = "DTSTART;VALUE=DATE:" if len(start) == 8 else "DTSTART:"
    tail = "DTEND;VALUE=DATE:" if len(end) == 8 else "DTEND:"
    inv = ics.parse(_cal(["UID:r", head + start, tail + end, "RRULE:" + rule, *extra]))
    got = ics.next_occurrence(inv, after)
    return got and got[0]


def test_hwf_only_the_first_event_of_a_calendar_is_read():     # what a card shows: `events` has the others
    from mailmind.bootstrap.calendar import ics
    inv = ics.parse(_cal(["UID:a", "SUMMARY:First", "DTSTART:20261110T100000"],
                         ["UID:b", "SUMMARY:Second", "DTSTART:20261111T100000"]))
    assert (inv["uid"], inv["summary"], inv["start_iso"]) == ("a", "First", "2026-11-10T10:00:00")


def test_hwf_count_includes_occurrences_an_exdate_removes():
    # Mondays from Oct 5: COUNT=5 ends on Nov 2, which EXDATE removes; it still counts
    assert _next("FREQ=WEEKLY;COUNT=5", "EXDATE:20261102T100000") is None
    assert _next("FREQ=WEEKLY;COUNT=6", "EXDATE:20261102T100000") == datetime(2026, 11, 9, 10, 0)


def test_hwf_an_all_day_series_skips_an_exdate_by_its_date():
    assert _next("FREQ=WEEKLY", "EXDATE:20261102T090000", start="20261005", end="20261006") == datetime(2026, 11, 9)

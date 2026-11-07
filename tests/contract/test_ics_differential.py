"""The two readers of an invite side by side: `ics_reader`, the hand-rolled one the old front door runs on, and
`icalendar_acl`, over icalendar and recurring-ical-events, which the new door runs on. Every text below goes through both
at every instant, in three machine zones, and what comes back must agree: the card (`parse`), the events found, the next
occurrence and what Today & next lists.

Except where `DIFFERS` says otherwise. Each entry names exactly what comes out differently, both values as this machine in
UTC gets them, and why, under what kind of difference it is. A difference not listed fails, in any zone, and so does a
listed one that's gone or has changed. In another zone a listed answer may say another hour, and nothing else may differ.
All fictional.
"""
import itertools
import os
import time
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from mailmind.calendar.adapters import icalendar_acl as library
from mailmind.calendar.adapters import ics_reader as hand_rolled
from mailmind.calendar.application.agenda import Agenda

BERLIN = ZoneInfo("Europe/Berlin")
# Naive is this machine's clock.
INSTANTS = {"before": datetime(2026, 9, 1, 12),         # the corpus's series start on Oct 5, its one-offs in November
            "during": datetime(2026, 10, 5, 10, 15),
            "now": datetime(2026, 11, 1, 12),           # the clocks have just moved
            "spring": datetime(2027, 3, 28, 12),        # and moved back
            "later": datetime(2031, 1, 1, 12)}
NOW = INSTANTS["now"]                   # what Today & next is asked at


def cal(*lines, method="REQUEST"):
    """One event, UID and SUMMARY first so a line can say them again."""
    head = ["BEGIN:VCALENDAR", "VERSION:2.0"] + ([f"METHOD:{method}"] if method else [])
    return "\r\n".join([*head, "BEGIN:VEVENT", "UID:u1@example.test", "SUMMARY:Studio sync", *lines, "END:VEVENT", "END:VCALENDAR", ""])


MON = "DTSTART:20261005T100000"         # a Monday, floating
MON_END = "DTEND:20261005T103000"
ZONED = ("DTSTART;TZID=Europe/Berlin:20261005T100000", "DTEND;TZID=Europe/Berlin:20261005T103000")
ALL_DAY = ("DTSTART;VALUE=DATE:20261005", "DTEND;VALUE=DATE:20261006")
ALARM = ("BEGIN:VALARM", "ACTION:DISPLAY", "SUMMARY:Alarm", "DESCRIPTION:This is a reminder",
         "ATTENDEE:mailto:alarm@example.test", "TRIGGER:-PT15M", "END:VALARM")
ZONE_BLOCK = ("BEGIN:VTIMEZONE", "TZID:W. Europe Standard Time", "BEGIN:STANDARD", "DTSTART:16010101T030000", "TZOFFSETFROM:+0200",
              "TZOFFSETTO:+0100", "RRULE:FREQ=YEARLY;INTERVAL=1;BYDAY=-1SU;BYMONTH=10", "END:STANDARD", "END:VTIMEZONE")
TWO_EVENTS = ("BEGIN:VCALENDAR", "VERSION:2.0", "METHOD:REQUEST",
              "BEGIN:VEVENT", "UID:a@example.test", "SUMMARY:First", "DTSTART:20261110T100000", "END:VEVENT",
              "BEGIN:VEVENT", "UID:b@example.test", "SUMMARY:Second", "DTSTART:20261111T100000", "END:VEVENT", "END:VCALENDAR", "")


def _series_with_override(*order):
    parts = {"series": ("BEGIN:VEVENT", "UID:s@example.test", "SUMMARY:Standup", MON, MON_END, "RRULE:FREQ=WEEKLY", "END:VEVENT"),
             "moved": ("BEGIN:VEVENT", "UID:s@example.test", "SUMMARY:Standup (moved)", "RECURRENCE-ID:20261102T100000",
                       "DTSTART:20261103T150000", "DTEND:20261103T153000", "END:VEVENT")}
    return "\r\n".join(["BEGIN:VCALENDAR", "VERSION:2.0", "METHOD:REQUEST", *(line for o in order for line in parts[o]),
                        "END:VCALENDAR", ""])


CORPUS: dict[str, str] = {
    # One-off events, each way of saying when.
    "floating": cal(MON, MON_END),
    "no-end": cal(MON),
    "utc": cal("DTSTART:20261105T100000Z", "DTEND:20261105T103000Z"),
    "zoned": cal("DTSTART;TZID=Europe/Berlin:20261105T100000", "DTEND;TZID=Europe/Berlin:20261105T103000"),
    "across-midnight": cal("DTSTART:20261105T233000", "DTEND:20261106T003000"),
    "end-before-start": cal("DTSTART:20261105T100000", "DTEND:20261105T090000"),
    "all-day": cal("DTSTART;VALUE=DATE:20261105", "DTEND;VALUE=DATE:20261106"),
    "all-day-no-end": cal("DTSTART;VALUE=DATE:20261105"),
    "all-day-no-value-param": cal("DTSTART:20261105", "DTEND:20261106"),
    "several-days": cal("DTSTART;VALUE=DATE:20261105", "DTEND;VALUE=DATE:20261108"),
    "zone-and-z": cal("DTSTART;TZID=Europe/Berlin:20261105T100000Z"),
    "no-seconds": cal("DTSTART:20261105T1000", "DTEND:20261105T1030"),
    "value-date-with-a-time": cal("DTSTART;VALUE=DATE:20261105T100000"),
    "lower-case-params": cal("DTSTART;tzid=Europe/Berlin:20261105T100000", "ORGANIZER;cn=Ren:mailto:ren@example.test"),
    "quoted-tzid": cal('DTSTART;TZID="Europe/Berlin":20261105T100000'),
    # A TZID that names no zone we know floats, whatever else the text says about it.
    "unknown-tzid": cal("DTSTART;TZID=Nowhere/Land:20261105T100000", "DTEND;TZID=Nowhere/Land:20261105T103000"),
    "unknown-tzid-on-the-end-only": cal("DTSTART;TZID=Europe/Berlin:20261105T100000", "DTEND;TZID=Nowhere/Land:20261105T103000"),
    "windows-tzid-with-its-vtimezone": "\r\n".join([
        "BEGIN:VCALENDAR", "METHOD:REQUEST", *ZONE_BLOCK, "BEGIN:VEVENT", "UID:w@example.test", "SUMMARY:Design review",
        "DTSTART;TZID=W. Europe Standard Time:20261103T140000", "DTEND;TZID=W. Europe Standard Time:20261103T150000",
        "END:VEVENT", "END:VCALENDAR", ""]),
    "vendor-prefixed-tzid": cal("DTSTART;TZID=/example.test/Europe/Berlin:20261105T100000"),
    "a-vtimezone-that-never-ends": "\r\n".join([
        "BEGIN:VCALENDAR", "BEGIN:VTIMEZONE", "TZID:Bomb/Zone", "BEGIN:STANDARD", "DTSTART:16010101T000000", "TZOFFSETFROM:+0100",
        "TZOFFSETTO:+0100", "RRULE:FREQ=SECONDLY", "END:STANDARD", "END:VTIMEZONE", "BEGIN:VEVENT", "UID:b@example.test",
        "DTSTART;TZID=Bomb/Zone:20261105T100000", "END:VEVENT", "END:VCALENDAR", ""]),
    # Every field of the card.
    "the-whole-card": cal(MON, MON_END, "LOCATION:Roof room", "DESCRIPTION:Bring the plans", "STATUS:CONFIRMED",
                          "ORGANIZER;CN=Ren Takeda:mailto:ren@example.test",
                          "ATTENDEE;CN=Ada Park;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:ada@example.test",
                          "ATTENDEE:mailto:bo@example.test"),
    "no-summary": "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:n\r\nDTSTART:20261105T100000\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n",
    "empty-summary": cal(MON, "SUMMARY:"),
    "summary-said-twice": cal(MON, "SUMMARY:Later"),
    "start-said-twice": cal(MON, "DTSTART:20261105T100000"),
    "escapes": cal(MON, r"SUMMARY:One\, two\; three\\four", r"DESCRIPTION:Line one\nLine two\NLine three", r"LOCATION:Roof\, east"),
    "language-param": cal(MON, "SUMMARY;LANGUAGE=en-US:Design review", "LOCATION;LANGUAGE=en-US:Room 2"),
    "non-ascii": cal(MON, "SUMMARY:Résumé review 東京", "ORGANIZER;CN=Zoë Müller:mailto:zoe@example.test"),
    "long-description": cal(MON, "DESCRIPTION:" + "words " * 400),
    "many-attendees": cal(MON, *(f"ATTENDEE;CN=P{i}:mailto:p{i}@example.test" for i in range(40))),
    "attendee-with-no-address": cal(MON, "ATTENDEE;CN=Nobody:", "ATTENDEE:mailto:bo@example.test"),
    "mailto-in-capitals": cal(MON, "ORGANIZER:MAILTO:ren@example.test"),
    "quoted-comma-in-a-name": cal(MON, 'ORGANIZER;CN="Takeda, Ren":mailto:ren@example.test'),
    "escaped-uid": cal(MON, r"UID:a\,b@example.test"),
    "an-escape-in-a-uid-or-an-address": cal(MON, r"UID:a\nb@example.test", r"ORGANIZER:mailto:ren\nb@example.test", r"ATTENDEE:mailto:bo\nb@example.test"),
    "no-method": cal(MON, method=""),
    "method-in-lower-case": cal(MON, method="cancel"),
    "status-in-lower-case": cal(MON, "STATUS:cancelled"),
    "cancelled": cal(MON, "STATUS:CANCELLED", method="CANCEL"),
    "one-occurrence": cal("DTSTART:20261110T150000", "RECURRENCE-ID:20261109T100000"),
    "one-occurrence-zoned": cal("DTSTART;TZID=Europe/Berlin:20261110T150000", "RECURRENCE-ID;TZID=Europe/Berlin:20261109T100000"),
    # How the lines come.
    "lf-only": cal(MON, MON_END).replace("\r\n", "\n"),
    "cr-only": cal(MON, MON_END).replace("\r\n", "\r"),
    "folded": cal(MON, "DESCRIPTION:Line that\r\n  goes on\r\n\tand on", "ORGANIZER;CN=Ren\r\n  Takeda:mailto:ren@example.test"),
    "byte-order-mark": "\ufeff" + cal(MON),
    "blank-lines": cal(MON, "", "", MON_END),
    "prologue": "Content-Type: text/calendar\r\n\r\n" + cal(MON),
    "epilogue": cal(MON) + "-- \r\nsent from a phone\r\n",
    "a-line-it-cannot-read": cal("not a line at all", MON, ":y"),
    "a-line-it-cannot-read-before-it-all": "not a line at all\r\n" + cal(MON),
    "no-calendar-around-it": "BEGIN:VEVENT\r\nUID:b\r\nSUMMARY:Bare\r\nDTSTART:20261105T100000\r\nEND:VEVENT\r\n",
    "cut-short-after-the-event": cal(MON, MON_END).replace("END:VCALENDAR\r\n", ""),
    "cut-short-in-the-event": "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:c\r\nDTSTART:20261105T100000\r\n",
    # What isn't a calendar, or an event worth the name.
    "empty": "",
    "not-a-calendar": "not a calendar at all",
    "no-event": "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n",
    "only-a-todo": "BEGIN:VCALENDAR\r\nBEGIN:VTODO\r\nUID:t\r\nSUMMARY:Task\r\nEND:VTODO\r\nEND:VCALENDAR\r\n",
    "no-start": cal(),
    "garbage-start": cal("DTSTART:garbage", "DTEND:also garbage"),
    "impossible-date": cal("DTSTART:20261345T100000"),
    "a-duration-for-a-start": cal("DTSTART:PT1H"),
    "a-time-for-a-start": cal("DTSTART:100000"),
    "a-period-for-a-start": cal("DTSTART:20261105T100000/PT1H"),
    "year-one": cal("DTSTART:00010101T000000Z"),
    "year-9999": cal("DTSTART:99991231T235959Z", "DTEND:99991231T235959Z"),
    "ends-in-year-9999": cal(MON, "DTEND:99991231T235959"),
    "starts-in-year-one-ends-in-year-9999": cal("DTSTART:00010101T000000Z", "DTEND:99991231T235959Z"),
    "duration-past-year-9999": cal("DTSTART:99991231T100000", "DURATION:P7D"),
    # The rules both read.
    "weekly": cal(MON, MON_END, "RRULE:FREQ=WEEKLY"),
    "weekly-zoned": cal(*ZONED, "RRULE:FREQ=WEEKLY"),
    "weekly-utc": cal("DTSTART:20261005T100000Z", "DTEND:20261005T103000Z", "RRULE:FREQ=WEEKLY"),
    "weekly-all-day": cal(*ALL_DAY, "RRULE:FREQ=WEEKLY"),
    "weekly-unknown-tzid": cal("DTSTART;TZID=Nowhere/Land:20261005T100000", "RRULE:FREQ=WEEKLY"),
    "weekly-parts-in-any-order": cal(MON, "RRULE:INTERVAL=2;BYDAY=MO;FREQ=WEEKLY;COUNT=9"),
    "weekly-in-lower-case": cal(MON, "RRULE:freq=weekly;interval=2;byday=mo"),
    "weekly-wkst-monday": cal(MON, "RRULE:FREQ=WEEKLY;WKST=MO;BYDAY=MO"),
    "rule-said-twice": cal(MON, "RRULE:FREQ=DAILY", "RRULE:FREQ=WEEKLY"),
    "daily-weekdays": cal(MON, MON_END, "RRULE:FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR"),
    "monthly-31st": cal("DTSTART:20261231T100000", "RRULE:FREQ=MONTHLY"),
    "monthly-first-tuesday": cal("DTSTART:20261006T100000", "RRULE:FREQ=MONTHLY;BYDAY=1TU"),
    "monthly-last-friday": cal("DTSTART:20261030T100000", "RRULE:FREQ=MONTHLY;BYDAY=-1FR"),
    "monthly-last-day": cal("DTSTART:20261031T100000", "RRULE:FREQ=MONTHLY;BYMONTHDAY=-1"),
    "yearly": cal(MON, "RRULE:FREQ=YEARLY"),
    "yearly-29-february": cal("DTSTART:20280229T100000", "RRULE:FREQ=YEARLY"),
    "count": cal(MON, "RRULE:FREQ=WEEKLY;COUNT=5"),
    "count-with-an-exdate": cal(MON, "RRULE:FREQ=WEEKLY;COUNT=5", "EXDATE:20261102T100000"),
    "count-that-says-nothing": cal(MON, "RRULE:FREQ=WEEKLY;COUNT=many"),
    "count-huge": cal(MON, "RRULE:FREQ=DAILY;COUNT=999999999"),
    "until-utc": cal(*ZONED, "RRULE:FREQ=WEEKLY;UNTIL=20261102T090000Z"),
    "until-floating-on-a-zoned-series": cal(*ZONED, "RRULE:FREQ=WEEKLY;UNTIL=20261102T100000"),
    "until-a-date-on-a-timed-series": cal(MON, "RRULE:FREQ=WEEKLY;UNTIL=20261102"),
    "until-a-date-on-an-all-day-series": cal(*ALL_DAY, "RRULE:FREQ=WEEKLY;UNTIL=20261102"),
    "until-utc-on-a-floating-series": cal(MON, "RRULE:FREQ=WEEKLY;UNTIL=20261102T100000Z"),
    "until-utc-on-an-all-day-series": cal(*ALL_DAY, "RRULE:FREQ=WEEKLY;UNTIL=20261102T000000Z"),
    "until-garbage": cal(MON, "RRULE:FREQ=WEEKLY;UNTIL=soon"),
    "until-year-9999": cal(MON, "RRULE:FREQ=DAILY;UNTIL=99991231T235959Z"),
    "until-before-it-starts": cal(MON, "RRULE:FREQ=WEEKLY;UNTIL=20261001T000000"),
    "interval-zero": cal(MON, "RRULE:FREQ=DAILY;INTERVAL=0"),
    "interval-negative": cal(MON, "RRULE:FREQ=DAILY;INTERVAL=-3"),
    "interval-past-year-9999": cal(MON, "RRULE:FREQ=YEARLY;INTERVAL=9000"),
    "exdate": cal(MON, MON_END, "RRULE:FREQ=WEEKLY", "EXDATE:20261102T100000"),
    "exdate-list": cal(MON, MON_END, "RRULE:FREQ=WEEKLY", "EXDATE:20261102T100000,20261109T100000"),
    "exdate-lines": cal(MON, MON_END, "RRULE:FREQ=WEEKLY", "EXDATE:20261102T100000", "EXDATE:20261109T100000"),
    "exdate-with-a-bad-value": cal(MON, MON_END, "RRULE:FREQ=WEEKLY", "EXDATE:garbage,20261102T100000"),
    "exdate-all-garbage": cal(MON, MON_END, "RRULE:FREQ=WEEKLY", "EXDATE:garbage"),
    "exdate-another-zone": cal(*ZONED, "RRULE:FREQ=WEEKLY", "EXDATE:20261102T090000Z"),
    "exdate-all-day-series": cal(*ALL_DAY, "RRULE:FREQ=WEEKLY", "EXDATE;VALUE=DATE:20261102"),
    "exdate-a-time-on-an-all-day-series": cal(*ALL_DAY, "RRULE:FREQ=WEEKLY", "EXDATE:20261102T090000"),
    "exdate-on-a-one-off": cal("DTSTART:20261105T100000", "EXDATE:20261105T100000"),
    "clock-gap": cal("DTSTART;TZID=Europe/Berlin:20270321T023000", "RRULE:FREQ=WEEKLY"),
    "clock-fold": cal("DTSTART;TZID=Europe/Berlin:20261018T023000", "RRULE:FREQ=WEEKLY"),
    # Rules neither expands.
    "secondly": cal(MON, "RRULE:FREQ=SECONDLY"),
    "minutely": cal(MON, "RRULE:FREQ=MINUTELY;INTERVAL=5"),
    "hourly": cal(MON, "RRULE:FREQ=HOURLY"),
    "rule-of-nonsense": cal(MON, "RRULE:FREQ=FORTNIGHTLY"),
    "rule-with-no-frequency": cal(MON, "RRULE:BYDAY=MO"),
    "rule-with-no-equals": cal(MON, "RRULE:whenever"),
    "rule-with-a-bad-weekday": cal(MON, "RRULE:FREQ=WEEKLY;BYDAY=XX"),
    "rule-with-a-bad-interval": cal(MON, "RRULE:FREQ=WEEKLY;INTERVAL=often"),
    "rule-with-an-unknown-part": cal(MON, "RRULE:FREQ=WEEKLY;X-EXAMPLE=1"),
    "a-rule-that-never-comes-to-a-date": cal("DTSTART:20261105T100000", "RRULE:FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=30"),

    # From here on the two differ, each as DIFFERS says.
    "two-events": "\r\n".join(TWO_EVENTS),
    "series-then-its-moved-occurrence": _series_with_override("series", "moved"),
    "moved-occurrence-then-its-series": _series_with_override("moved", "series"),
    "two-calendars": "\r\n".join([*TWO_EVENTS[:-1], "BEGIN:VCALENDAR", "METHOD:CANCEL", "BEGIN:VEVENT", "UID:c@example.test",
                                  "DTSTART:20261112T100000", "END:VEVENT", "END:VCALENDAR", ""]),
    "alarm-after-the-text": cal(MON, MON_END, "DESCRIPTION:Bring the plans", *ALARM),
    "alarm-before-the-text": cal(MON, MON_END, *ALARM, "DESCRIPTION:Bring the plans"),
    "duration": cal(MON, "DURATION:PT1H"),
    "duration-all-day": cal("DTSTART;VALUE=DATE:20261105", "DURATION:P3D"),
    "rdate": cal(MON, MON_END, "RDATE:20261105T100000"),
    "rdate-with-a-rule": cal(MON, MON_END, "RRULE:FREQ=WEEKLY;COUNT=2", "RDATE:20261201T100000"),
    "rdate-zoned": cal(*ZONED, "RDATE;TZID=Europe/Berlin:20261105T100000,20261201T100000"),
    "rdate-all-day": cal(*ALL_DAY, "RDATE;VALUE=DATE:20261105"),
    "rdate-period": cal(MON, MON_END, "RDATE;VALUE=PERIOD:20261105T100000/PT2H"),
    "rdate-with-an-exdate": cal(MON, MON_END, "RDATE:20261105T100000,20261112T100000", "EXDATE:20261105T100000"),
    "colon-in-a-quoted-name": cal(MON, 'ORGANIZER;CN="Ren: Takeda":mailto:ren@example.test'),
    "semicolon-in-a-quoted-name": cal(MON, 'ORGANIZER;CN="Takeda; Ren":mailto:ren@example.test'),
    "caret-escape-in-a-name": cal(MON, 'ORGANIZER;CN="Ren ^\'Rocket^\' Takeda":mailto:ren@example.test'),
    "caret-line-break-in-a-name": cal(MON, 'ORGANIZER;CN="Ren^nTakeda":mailto:ren@example.test'),
    "a-quote-in-a-name-with-none-around-it": cal(MON, 'ORGANIZER;CN=Ren"x:mailto:ren@example.test'),
    "escaped-backslash-before-n": cal(MON, r"SUMMARY:C:\\new\\notes"),
    "method-inside-the-event": cal(MON, "METHOD:CANCEL", method=""),
    "exdate-a-whole-day-of-a-timed-series": cal(MON, MON_END, "RRULE:FREQ=WEEKLY", "EXDATE;VALUE=DATE:20261102"),
    "start-not-in-its-rule": cal(MON, MON_END, "RRULE:FREQ=WEEKLY;BYDAY=TU"),
    "count-zero": cal(MON, "RRULE:FREQ=WEEKLY;COUNT=0"),
    "a-line-with-an-empty-parameter": cal(MON, "UID;:x"),
    "bysetpos": cal(MON, MON_END, "RRULE:FREQ=MONTHLY;BYDAY=MO;BYSETPOS=1"),
    "bymonth": cal(MON, MON_END, "RRULE:FREQ=YEARLY;BYMONTH=10,11"),
    "bymonth-and-byday": cal(MON, MON_END, "RRULE:FREQ=MONTHLY;BYMONTH=11,12;BYDAY=1MO"),
    "byweekno": cal(MON, MON_END, "RRULE:FREQ=YEARLY;BYWEEKNO=41,45;BYDAY=MO"),
    "byyearday": cal(MON, MON_END, "RRULE:FREQ=YEARLY;BYYEARDAY=278,310"),
    "byhour": cal(MON, MON_END, "RRULE:FREQ=DAILY;BYHOUR=10,16"),
    "byminute": cal(MON, MON_END, "RRULE:FREQ=DAILY;BYMINUTE=0,30"),
    "yearly-byday": cal(MON, MON_END, "RRULE:FREQ=YEARLY;BYDAY=1MO"),
    "weekly-bymonthday": cal(MON, MON_END, "RRULE:FREQ=WEEKLY;BYMONTHDAY=5"),
    "yearly-bymonthday": cal(MON, MON_END, "RRULE:FREQ=YEARLY;BYMONTHDAY=5"),
    "daily-bymonthday": cal(MON, MON_END, "RRULE:FREQ=DAILY;BYMONTHDAY=5"),
    "weekly-ordinal-byday": cal(MON, MON_END, "RRULE:FREQ=WEEKLY;BYDAY=1MO"),
    "monthly-13th-if-a-friday": cal("DTSTART:20261113T100000", "RRULE:FREQ=MONTHLY;BYMONTHDAY=13;BYDAY=FR"),
    "wkst-sunday-moves-the-weeks": cal("DTSTART:20261004T100000", "RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=SU,MO;WKST=SU"),
    "a-series-past-20000-days": cal("DTSTART:19600101T100000", "RRULE:FREQ=DAILY"),
}


@dataclass(frozen=True)
class Differs:
    """How the two answer one text. `why` starts with what kind of difference it is."""
    why: str
    answers: dict                       # what comes out differently: {what: (hand-rolled, library)}


BUG = "A bug in ics_reader: "
LIBRARY = "What the library reads and ics_reader doesn't: "
OPEN = "The RFC leaves this open, and neither is wrong: "
FIRST = "2026-10-05T10:00 for 30m"


def _a_rule_ics_reader_gives_up_on(what, now, spring, later):
    """A 30-minute series with this rule, from Oct 5. ics_reader answers None and the Agenda has only the first date, which
    is over. The library's dates start at DTSTART, whether the rule names that day or not (see start-not-in-its-rule)."""
    return Differs(f"{LIBRARY}{what}", {
        "agenda": ([], [f"Studio sync {now} to {now[:14]}30"]),
        "next.before": (None, FIRST),
        "next.during": (None, FIRST),
        "next.now": (None, f"{now} for 30m"),
        "next.spring": (None, f"{spring} for 30m"),
        "next.later": (None, f"{later} for 30m"),
    })


DIFFERS: dict[str, Differs] = {
    "two-events": Differs(f"{LIBRARY}every VEVENT of a text, where ics_reader stops at the first. The card is the first either way.", {
        "agenda": (["First 2026-11-10T10:00 to 2026-11-10T10:00"],
                   ["First 2026-11-10T10:00 to 2026-11-10T10:00", "Second 2026-11-11T10:00 to 2026-11-11T10:00"]),
        "events": (["a@example.test First 2026-11-10T10:00:00"],
                   ["a@example.test First 2026-11-10T10:00:00", "b@example.test Second 2026-11-11T10:00:00"]),
    }),
    "series-then-its-moved-occurrence": Differs(f"{LIBRARY}a moved occurrence sent with its series. Seeing only the series, ics_reader lists the slot it moved away from.", {
        "agenda": (["Standup 2026-11-02T10:00 to 2026-11-02T10:30"],
                   ["Standup (moved) 2026-11-03T15:00 to 2026-11-03T15:30", "Standup 2026-11-09T10:00 to 2026-11-09T10:30"]),
        "events": (["s@example.test Standup 2026-10-05T10:00:00"],
                   ["s@example.test Standup 2026-10-05T10:00:00", "s@example.test Standup (moved) 2026-11-03T15:00:00 2026-11-02T10:00:00"]),
    }),
    "moved-occurrence-then-its-series": Differs(f"{LIBRARY}the same the other way round. ics_reader sees only the moved occurrence and loses the series, and "
                                                "its card shows the one occurrence. The library's card is the series.", {
        "agenda": (["Standup (moved) 2026-11-03T15:00 to 2026-11-03T15:30"],
                   ["Standup (moved) 2026-11-03T15:00 to 2026-11-03T15:30", "Standup 2026-11-09T10:00 to 2026-11-09T10:30"]),
        "events": (["s@example.test Standup (moved) 2026-11-03T15:00:00 2026-11-02T10:00:00"],
                   ["s@example.test Standup (moved) 2026-11-03T15:00:00 2026-11-02T10:00:00", "s@example.test Standup 2026-10-05T10:00:00"]),
        "next.before": ("2026-11-03T15:00 for 30m", FIRST),
        "next.during": ("2026-11-03T15:00 for 30m", FIRST),
        "next.now": ("2026-11-03T15:00 for 30m", "2026-11-02T10:00 for 30m"),
        "next.spring": (None, "2027-03-29T10:00 for 30m"),
        "next.later": (None, "2031-01-06T10:00 for 30m"),
        "parse.end_iso": ("2026-11-03T15:30:00", "2026-10-05T10:30:00"),
        "parse.recurrence": ("", "Every week"),
        "parse.recurrence_id": ("2026-11-02T10:00:00", ""),
        "parse.rrule": ("", "FREQ=WEEKLY"),
        "parse.start_iso": ("2026-11-03T15:00:00", "2026-10-05T10:00:00"),
        "parse.summary": ("Standup (moved)", "Standup"),
        "parse.when": ("Tue, Nov 3 · 3:00 PM–3:30 PM", "Mon, Oct 5 · 10:00 AM–10:30 AM"),
    }),
    "two-calendars": Differs(f"{BUG}the last METHOD anywhere in a text speaks for its first event, so one calendar's CANCEL took another's REQUEST off the list. "
                             "The library reads each event under its own calendar.", {
        "agenda": ([], ["First 2026-11-10T10:00 to 2026-11-10T10:00", "Second 2026-11-11T10:00 to 2026-11-11T10:00"]),
        "events": (["a@example.test First 2026-11-10T10:00:00"],
                   ["a@example.test First 2026-11-10T10:00:00", "b@example.test Second 2026-11-11T10:00:00", "c@example.test (no title) 2026-11-12T10:00:00"]),
        "parse.method": ("CANCEL", "REQUEST"),
    }),
    "method-inside-the-event": Differs(f"{BUG}METHOD is the calendar's. In an event it says nothing, and here it would cancel the event.", {
        "parse.method": ("CANCEL", ""),
    }),
    "alarm-after-the-text": Differs(f"{BUG}a VALARM's SUMMARY, DESCRIPTION and ATTENDEE are taken for the event's, so the card names the reminder and whom it mails.", {
        "events": (["u1@example.test Alarm 2026-10-05T10:00:00"], ["u1@example.test Studio sync 2026-10-05T10:00:00"]),
        "parse.attendees": ([{"name": "", "email": "alarm@example.test"}], []),
        "parse.description": ("This is a reminder", "Bring the plans"),
        "parse.summary": ("Alarm", "Studio sync"),
    }),
    "alarm-before-the-text": Differs(f"{BUG}the same, with the event's own DESCRIPTION coming after to save that one.", {
        "events": (["u1@example.test Alarm 2026-10-05T10:00:00"], ["u1@example.test Studio sync 2026-10-05T10:00:00"]),
        "parse.attendees": ([{"name": "", "email": "alarm@example.test"}], []),
        "parse.summary": ("Alarm", "Studio sync"),
    }),
    "duration": Differs(f"{LIBRARY}DURATION in place of DTEND. To ics_reader the event is over as it begins.", {
        "next.before": ("2026-10-05T10:00 for 0m", "2026-10-05T10:00 for 60m"),
        "next.during": (None, "2026-10-05T10:00 for 60m"),
        "parse.end_iso": ("", "2026-10-05T11:00:00"),
        "parse.when": ("Mon, Oct 5 · 10:00 AM", "Mon, Oct 5 · 10:00 AM–11:00 AM"),
    }),
    "duration-all-day": Differs(f"{LIBRARY}DURATION in place of DTEND. To ics_reader an all-day event with no end is one day long.", {
        "agenda": (["Studio sync 2026-11-05T00:00 to 2026-11-06T00:00 all day"], ["Studio sync 2026-11-05T00:00 to 2026-11-08T00:00 all day"]),
        "next.before": ("2026-11-05T00:00 for 1440m", "2026-11-05T00:00 for 4320m"),
        "next.during": ("2026-11-05T00:00 for 1440m", "2026-11-05T00:00 for 4320m"),
        "next.now": ("2026-11-05T00:00 for 1440m", "2026-11-05T00:00 for 4320m"),
        "parse.end_iso": ("", "2026-11-08T00:00:00"),
    }),
    "rdate": Differs(f"{LIBRARY}RDATE, which ics_reader ignores.", {
        "agenda": ([], ["Studio sync 2026-11-05T10:00 to 2026-11-05T10:30"]),
        "next.now": (None, "2026-11-05T10:00 for 30m"),
        "parse.rdates": (None, ["2026-11-05T10:00:00"]),
    }),
    "rdate-with-a-rule": Differs(f"{LIBRARY}RDATE beside a rule.", {
        "agenda": ([], ["Studio sync 2026-12-01T10:00 to 2026-12-01T10:30"]),
        "next.now": (None, "2026-12-01T10:00 for 30m"),
        "parse.rdates": (None, ["2026-12-01T10:00:00"]),
    }),
    "rdate-zoned": Differs(f"{LIBRARY}RDATE, in its zone.", {
        "agenda": ([], ["Studio sync 2026-11-05T09:00 to 2026-11-05T09:30"]),
        "next.during": (None, "2026-11-05T10:00+01:00 for 30m"),
        "next.now": (None, "2026-11-05T10:00+01:00 for 30m"),
        "parse.rdates": (None, ["2026-11-05T10:00:00+01:00", "2026-12-01T10:00:00+01:00"]),
    }),
    "rdate-all-day": Differs(f"{LIBRARY}RDATE, as a date.", {
        "agenda": ([], ["Studio sync 2026-11-05T00:00 to 2026-11-06T00:00 all day"]),
        "next.now": (None, "2026-11-05T00:00 for 1440m"),
        "parse.rdates": (None, ["2026-11-05T00:00:00"]),
    }),
    "rdate-period": Differs(f"{LIBRARY}RDATE, as a period. Only its start: an occurrence runs as long as the event does, where the RFC would give this one its two hours, "
                            "a shortcut of this adapter's that the library isn't to blame for.", {
        "agenda": ([], ["Studio sync 2026-11-05T10:00 to 2026-11-05T10:30"]),
        "next.now": (None, "2026-11-05T10:00 for 30m"),
        "parse.rdates": (None, ["2026-11-05T10:00:00"]),
    }),
    "rdate-with-an-exdate": Differs(f"{LIBRARY}RDATE, and an EXDATE striking one.", {
        "agenda": ([], ["Studio sync 2026-11-12T10:00 to 2026-11-12T10:30"]),
        "next.now": (None, "2026-11-12T10:00 for 30m"),
        "parse.rdates": (None, ["2026-11-05T10:00:00", "2026-11-12T10:00:00"]),
    }),
    "colon-in-a-quoted-name": Differs(f"{BUG}it looks for the value's colon inside the quotes, garbling the address an RSVP would go to.", {
        "parse.organizer": ({"name": "Ren", "email": 'Takeda":mailto:ren@example.test'}, {"name": "Ren: Takeda", "email": "ren@example.test"}),
    }),
    "semicolon-in-a-quoted-name": Differs(f"{BUG}it ends the parameter at a semicolon inside the quotes.", {
        "parse.organizer": ({"name": "Takeda", "email": "ren@example.test"}, {"name": "Takeda; Ren", "email": "ren@example.test"}),
    }),
    "caret-escape-in-a-name": Differs(f"{LIBRARY}RFC 6868's escapes in a parameter, where ics_reader shows the caret.", {
        "parse.organizer": ({"name": "Ren ^'Rocket^' Takeda", "email": "ren@example.test"}, {"name": 'Ren "Rocket" Takeda', "email": "ren@example.test"}),
    }),
    "caret-line-break-in-a-name": Differs(f"{LIBRARY}RFC 6868's line break, which the adapter makes a space: a name goes on to an RSVP's headers.", {
        "parse.organizer": ({"name": "Ren^nTakeda", "email": "ren@example.test"}, {"name": "Ren Takeda", "email": "ren@example.test"}),
    }),
    "a-quote-in-a-name-with-none-around-it": Differs(f"{OPEN}a quote is no part of an unquoted parameter. ics_reader takes it as part of the name, and the library drops the line, "
                                                     "so to it this invite has no organizer to answer.", {
        "parse.organizer": ({"name": 'Ren"x', "email": "ren@example.test"}, {}),
    }),
    "escaped-backslash-before-n": Differs(f"{BUG}it reads `\\\\n` as a line break after a backslash, where it's a backslash and then an n.", {
        "events": (["u1@example.test C:\\\new\\\notes 2026-10-05T10:00:00"], ["u1@example.test C:\\new\\notes 2026-10-05T10:00:00"]),
        "parse.summary": ("C:\\\new\\\notes", "C:\\new\\notes"),
    }),
    "exdate-a-whole-day-of-a-timed-series": Differs(f"{BUG}it takes a date in an EXDATE for that midnight, when no occurrence of a timed series is, so the day it strikes stays. "
                                                    "The adapter strikes the day, which the library wouldn't either.", {
        "agenda": (["Studio sync 2026-11-02T10:00 to 2026-11-02T10:30"], ["Studio sync 2026-11-09T10:00 to 2026-11-09T10:30"]),
        "next.now": ("2026-11-02T10:00 for 30m", "2026-11-09T10:00 for 30m"),
        "parse.exdates": (["2026-11-02T00:00:00"], ["2026-11-02"]),
    }),
    "start-not-in-its-rule": Differs(f"{OPEN}RFC 5545 says only that a DTSTART out of step with its rule is undefined. The library keeps it as the first occurrence, "
                                     "and ics_reader starts at the first date the rule names.", {
        "next.before": ("2026-10-06T10:00 for 30m", FIRST),
        "next.during": ("2026-10-06T10:00 for 30m", FIRST),
    }),
    "count-zero": Differs(f"{OPEN}a rule that happens no times. The library still has its DTSTART, and ics_reader nothing, though the Agenda shows the first date either way.", {
        "next.before": (None, "2026-10-05T10:00 for 0m"),
    }),
    "a-line-with-an-empty-parameter": Differs(f"{OPEN}a parameter with no name is no line at all. ics_reader takes the value, and the library drops the line.", {
        "events": (["x Studio sync 2026-10-05T10:00:00"], ["u1@example.test Studio sync 2026-10-05T10:00:00"]),
        "parse.uid": ("x", "u1@example.test"),
    }),
    "monthly-13th-if-a-friday": Differs(f"{BUG}with BYMONTHDAY it ignores BYDAY, so it names any 13th. The library's are Fridays.", {
        "next.spring": ("2027-04-13T10:00 for 0m", "2027-08-13T10:00 for 0m"),
        "next.later": ("2031-01-13T10:00 for 0m", "2031-06-13T10:00 for 0m"),
    }),
    "wkst-sunday-moves-the-weeks": Differs(f"{BUG}it ignores WKST, which moves which days share a week, and so which come round under an INTERVAL. RFC 5545's own example.", {
        "agenda": (["Studio sync 2026-11-09T10:00 to 2026-11-09T10:00"], ["Studio sync 2026-11-02T10:00 to 2026-11-02T10:00"]),
        "next.during": ("2026-10-12T10:00 for 0m", "2026-10-18T10:00 for 0m"),
        "next.now": ("2026-11-09T10:00 for 0m", "2026-11-02T10:00 for 0m"),
        "next.spring": ("2027-03-29T10:00 for 0m", "2027-04-04T10:00 for 0m"),
    }),
    "a-series-past-20000-days": Differs(f"{LIBRARY}a series older than the 20000 periods ics_reader walks. The library goes on, within its budget of time.", {
        "agenda": ([], ["Studio sync 2026-11-02T10:00 to 2026-11-02T10:00"]),
        "next.before": (None, "2026-09-02T10:00 for 0m"),
        "next.during": (None, "2026-10-06T10:00 for 0m"),
        "next.now": (None, "2026-11-02T10:00 for 0m"),
        "next.spring": (None, "2027-03-29T10:00 for 0m"),
        "next.later": (None, "2031-01-02T10:00 for 0m"),
    }),
    "bysetpos": _a_rule_ics_reader_gives_up_on("BYSETPOS.", "2026-11-02T10:00", "2027-04-05T10:00", "2031-01-06T10:00"),
    "bymonth": _a_rule_ics_reader_gives_up_on("BYMONTH.", "2026-11-05T10:00", "2027-10-05T10:00", "2031-10-05T10:00"),
    "bymonth-and-byday": _a_rule_ics_reader_gives_up_on("BYMONTH with BYDAY.", "2026-11-02T10:00", "2027-11-01T10:00", "2031-11-03T10:00"),
    "byweekno": _a_rule_ics_reader_gives_up_on("BYWEEKNO.", "2026-11-02T10:00", "2027-10-11T10:00", "2031-10-06T10:00"),
    "byyearday": _a_rule_ics_reader_gives_up_on("BYYEARDAY.", "2026-11-06T10:00", "2027-10-05T10:00", "2031-10-05T10:00"),
    "byhour": _a_rule_ics_reader_gives_up_on("BYHOUR.", "2026-11-01T16:00", "2027-03-28T16:00", "2031-01-01T16:00"),
    "byminute": _a_rule_ics_reader_gives_up_on("BYMINUTE.", "2026-11-02T10:00", "2027-03-29T10:00", "2031-01-02T10:00"),
    "yearly-byday": _a_rule_ics_reader_gives_up_on("BYDAY in a yearly rule.", "2027-01-04T10:00", "2028-01-03T10:00", "2031-01-06T10:00"),
    "weekly-bymonthday": _a_rule_ics_reader_gives_up_on("BYMONTHDAY in a weekly rule, which the RFC forbids and dateutil reads anyway.",
                                                        "2026-11-05T10:00", "2027-04-05T10:00", "2031-01-05T10:00"),
    "yearly-bymonthday": _a_rule_ics_reader_gives_up_on("BYMONTHDAY in a yearly rule.", "2026-11-05T10:00", "2027-04-05T10:00", "2031-01-05T10:00"),
    "daily-bymonthday": _a_rule_ics_reader_gives_up_on("BYMONTHDAY in a daily rule.", "2026-11-05T10:00", "2027-04-05T10:00", "2031-01-05T10:00"),
    "weekly-ordinal-byday": _a_rule_ics_reader_gives_up_on("an ordinal BYDAY in a weekly rule, which the RFC forbids and dateutil takes as that weekday.",
                                                           "2026-11-02T10:00", "2027-03-29T10:00", "2031-01-06T10:00"),
}


class _Stored:
    """One message, holding this text."""
    def __init__(self, text):
        self.text = text

    def invite_parts(self, _since, _account, _skipped):
        yield {"pk": 1, "message_key": "k", "account": "a@example.test", "thread_id": "t"}, self.text


def _occurrence(occ):
    if occ is None:
        return None
    start, end = occ
    return f"{start.isoformat(timespec='minutes')} for {(end - start).total_seconds() / 60:g}m"


def _listed(item):
    return f"{item['summary']} {item['start']} to {item['end']}" + (" all day" if item["all_day"] else "")


def _answers(reader, text):
    """All that the rest of the app can learn from a reader about this text, flat, by what asks."""
    inv = reader.parse(text)
    out = {"parse": inv is not None,
           "events": [" ".join(filter(None, (e["uid"], e["summary"], e["start_iso"], e["recurrence_id"]))) for e in reader.events(text)]}
    for field, value in (inv or {}).items():
        out[f"parse.{field}"] = value
    for name, after in INSTANTS.items() if inv else ():
        out[f"next.{name}"] = _occurrence(reader.next_occurrence(inv, after))
    out["agenda"] = [_listed(x) for x in Agenda(_Stored(text), reader).upcoming(now=NOW, days=4000)["items"]]
    return out


@pytest.fixture(params=["UTC", "Asia/Tokyo", "America/New_York"])
def machine_zone(request):
    """Both readers' answers go through this machine's clock somewhere."""
    os.environ["TZ"] = request.param
    time.tzset()
    yield request.param
    os.environ["TZ"] = "UTC"
    time.tzset()


@pytest.mark.parametrize("name", CORPUS)
def test_the_readers_agree_unless_a_difference_is_listed(name, machine_zone):
    old, new = _answers(hand_rolled, CORPUS[name]), _answers(library, CORPUS[name])
    found = {k: (old.get(k), new.get(k)) for k in old.keys() | new.keys() if old.get(k) != new.get(k)}
    listed = DIFFERS[name].answers if name in DIFFERS else {}
    if machine_zone == "UTC":
        assert found == listed
    else:
        assert set(found) <= set(listed), {k: found[k] for k in set(found) - set(listed)}


@pytest.mark.parametrize("name", DIFFERS)
def test_a_listed_difference_is_in_the_corpus(name):
    assert name in CORPUS


# ── rules both read, as a product: nothing here may differ ─────────────────────────────────────────────────

STARTS = {"floating": (MON, MON_END), "zoned": ZONED, "utc": ("DTSTART:20261005T100000Z", "DTEND:20261005T103000Z"),
          "all-day": ALL_DAY, "unknown-tzid": ("DTSTART;TZID=Nowhere/Land:20261005T100000",),
          "late-evening": ("DTSTART;TZID=America/New_York:20261005T233000", "DTEND;TZID=America/New_York:20261006T003000")}
RULES = ["FREQ=DAILY", "FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR", "FREQ=WEEKLY", "FREQ=WEEKLY;BYDAY=MO", "FREQ=WEEKLY;BYDAY=WE,MO",
         "FREQ=MONTHLY", "FREQ=MONTHLY;BYMONTHDAY=5", "FREQ=YEARLY"]
PERIOD = ["", ";INTERVAL=2"]
ENDING = ["", ";COUNT=5", ";UNTIL=20261102T090000Z", ";UNTIL=20261102T100000", ";UNTIL=20261102"]
STRUCK = {"floating": ("EXDATE:20261102T100000",), "utc": ("EXDATE:20261102T090000Z",)}
AFTER_IT_BEGINS = (INSTANTS["during"], datetime(2026, 11, 1, 12, tzinfo=BERLIN), INSTANTS["spring"])


def test_every_rule_both_read_comes_to_the_same_dates(machine_zone):
    differ = []
    for (kind, start), rule, period, ending, (struck, exdate) in itertools.product(
            STARTS.items(), RULES, PERIOD, ENDING, STRUCK.items()):
        text = cal(*start, f"RRULE:{rule}{period}{ending}", *exdate)
        old, new = hand_rolled.parse(text), library.parse(text)
        if old != new:
            differ.append((kind, rule, period, ending, struck, old, new))
        for after in AFTER_IT_BEGINS:
            a, b = hand_rolled.next_occurrence(old, after), library.next_occurrence(new, after)
            if a != b:
                differ.append((kind, rule, period, ending, struck, after, a, b))
    assert differ == []

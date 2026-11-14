//! What an invite's text comes to: who an answer goes to, the dates it says, and what is left when a line won't
//! read (tests/unit/calendar/test_invite_text.py). A stranger wrote each of these, so what's kept is what would send
//! a reply astray, show a wrong date or lose the invite. Oct 5 2026 is a Monday. All fictional.

#![allow(clippy::unwrap_used)]

mod common;

use std::sync::Arc;

use common::*;
use mm_calendar::agenda;
use mm_calendar::invite::{self, Party};
use mm_calendar::rsvp;
use mm_calendar::time::{Dt, Tz};
use mm_kernel::zone::{Utc, Zone};

const MON: &str = "DTSTART:20261005T100000";
const MON_END: &str = "DTEND:20261005T103000";
const ON_NOV_5: &str = "DTSTART:20261105T100000";
const BERLIN_AT_10: &str = "DTSTART;TZID=Europe/Berlin:20261105T100000";

fn stamp() -> Dt {
    Dt::from_parts(2026, 11, 2, 12, 0, 0, Tz::Fixed(0)).unwrap()
}

fn reply(inv: &invite::Invite, partstat: &str) -> String {
    rsvp::build_reply(inv, "ada@example.test", "Ada", partstat, &stamp(), &Utc)
}

// ── who an answer goes to ───────────────────────────────────────────────────

#[test]
fn the_organizer_is_read_and_the_reply_goes_to_that_address() {
    let ren = "ren@example.test";
    let organizers = [
        ("ORGANIZER;CN=\"Ren: Takeda\":mailto:ren@example.test", "Ren: Takeda"),
        ("ORGANIZER;CN=\"Takeda; Ren\":mailto:ren@example.test", "Takeda; Ren"),
        ("ORGANIZER;CN=\"Takeda, Ren\":mailto:ren@example.test", "Takeda, Ren"),
        ("ORGANIZER:MAILTO:ren@example.test", ""),
        ("ORGANIZER;cn=Ren:mailto:ren@example.test", "Ren"),
        ("ORGANIZER;CN=Ren\r\n  Takeda:mailto:ren@example.test", "Ren Takeda"),
        ("ORGANIZER;CN=Zoë Müller:mailto:ren@example.test", "Zoë Müller"),
        ("ORGANIZER;CN=\"Ren ^'Rocket^' Takeda\":mailto:ren@example.test", "Ren \"Rocket\" Takeda"),
        ("ORGANIZER;CN=\"Ren^nTakeda\":mailto:ren@example.test", "Ren Takeda"),
    ];
    for (line, name) in organizers {
        let inv = parse(&cal(&[MON, line], "REQUEST")).unwrap();
        assert_eq!(inv.organizer, Some(Party { name: name.into(), email: ren.into() }), "{line}");
        let reply = reply(&inv, "ACCEPTED").replace("\r\n ", "");
        let said = reply.split("\r\n").find(|l| l.starts_with("ORGANIZER")).unwrap();
        assert!(said.ends_with(&format!(":mailto:{ren}")), "{line}: {said}");
    }
}

#[test]
fn a_quote_where_none_may_be_leaves_no_organizer_to_answer_rather_than_a_guess() {
    let inv = parse(&cal(&[MON, "ORGANIZER;CN=Ren\"x:mailto:ren@example.test"], "REQUEST")).unwrap();
    assert_eq!(inv.organizer, None);
    assert_eq!(inv.to_json()["organizer"], serde_json::json!({}));
}

#[test]
fn a_line_with_a_nameless_parameter_doesnt_replace_the_uid_a_reply_echoes() {
    assert_eq!(parse(&cal(&[MON, "UID;:x"], "REQUEST")).unwrap().uid, "u1@example.test");
}

#[test]
fn an_attendee_with_no_address_is_no_attendee() {
    let inv = parse(&cal(&[MON, "ATTENDEE;CN=Nobody:", "ATTENDEE:mailto:bo@example.test"], "REQUEST")).unwrap();
    assert_eq!(inv.attendees, vec![Party { name: String::new(), email: "bo@example.test".into() }]);
}

#[test]
fn no_more_than_25_attendees_are_kept() {
    let lines: Vec<String> = (0..40).map(|i| format!("ATTENDEE;CN=P{i}:mailto:p{i}@example.test")).collect();
    let mut all = vec![MON];
    all.extend(lines.iter().map(String::as_str));
    let inv = parse(&cal(&all, "REQUEST")).unwrap();
    assert_eq!(inv.attendees.len(), 25);
    assert_eq!(inv.attendees[0], Party { name: "P0".into(), email: "p0@example.test".into() });
}

#[test]
fn an_alarm_says_nothing_for_its_event_wherever_it_comes() {
    let alarm = [
        "BEGIN:VALARM",
        "ACTION:DISPLAY",
        "SUMMARY:Alarm",
        "DESCRIPTION:A reminder",
        "ATTENDEE:mailto:alarm@example.test",
        "TRIGGER:-PT15M",
        "END:VALARM",
    ];
    let before: Vec<&str> = [MON, "DESCRIPTION:Bring the plans"].into_iter().chain(alarm).collect();
    let after: Vec<&str> = [MON].into_iter().chain(alarm).chain(["DESCRIPTION:Bring the plans"]).collect();
    for lines in [before, after] {
        let inv = parse(&cal(&lines, "REQUEST")).unwrap();
        assert_eq!((inv.summary.as_str(), inv.description.as_str()), ("Studio sync", "Bring the plans"));
        assert!(inv.attendees.is_empty());
    }
}

// ── what cancels ────────────────────────────────────────────────────────────

#[test]
fn a_method_inside_an_event_is_no_method() {
    let text = cal(&[ON_NOV_5, "METHOD:CANCEL"], "");
    assert_eq!(parse(&text).unwrap().method, "");
    assert_eq!(listed(&text), ["Studio sync 2026-11-05T10:00 to 2026-11-05T10:00"]);
}

#[test]
fn one_calendars_cancel_leaves_another_calendars_request() {
    let text = [
        "BEGIN:VCALENDAR",
        "METHOD:REQUEST",
        "BEGIN:VEVENT",
        "UID:a@example.test",
        "SUMMARY:First",
        "DTSTART:20261110T100000",
        "END:VEVENT",
        "END:VCALENDAR",
        "BEGIN:VCALENDAR",
        "METHOD:CANCEL",
        "BEGIN:VEVENT",
        "UID:c@example.test",
        "SUMMARY:Third",
        "DTSTART:20261112T100000",
        "END:VEVENT",
        "END:VCALENDAR",
        "",
    ]
    .join("\r\n");
    assert_eq!(parse(&text).unwrap().method, "REQUEST");
    assert_eq!(listed(&text), ["First 2026-11-10T10:00 to 2026-11-10T10:00"]);
}

#[test]
fn a_status_in_lower_case_still_cancels() {
    let text = cal(&[ON_NOV_5, "STATUS:cancelled"], "REQUEST");
    assert_eq!(parse(&text).unwrap().status, "CANCELLED");
    assert!(listed(&text).is_empty());
}

// ── the card's words ────────────────────────────────────────────────────────

/// One of the card's words, read off an invite.
type Word = fn(&invite::Invite) -> &str;

#[test]
fn what_the_card_says() {
    let words: [(&str, Word, &str); 10] = [
        (r"SUMMARY:One\, two\; three\\four", |i| &i.summary, "One, two; three\\four"),
        (r"DESCRIPTION:Line one\nLine two\NLine three", |i| &i.description, "Line one\nLine two\nLine three"),
        (r"SUMMARY:C:\\new\\notes", |i| &i.summary, "C:\\new\\notes"),
        ("LOCATION;LANGUAGE=en-US:Room 2", |i| &i.location, "Room 2"),
        ("SUMMARY:Résumé review 東京", |i| &i.summary, "Résumé review 東京"),
        ("DESCRIPTION:Line that\r\n  goes on\r\n\tand on", |i| &i.description, "Line that goes onand on"),
        ("SUMMARY:Later", |i| &i.summary, "Later"),
        ("SUMMARY:", |i| &i.summary, ""),
        ("RRULE:INTERVAL=2;BYDAY=MO;FREQ=WEEKLY;COUNT=9", |i| &i.recurrence, "Every 2 weeks on Mon ×9"),
        ("RRULE:FREQ=WEEKLY;UNTIL=20261102", |i| &i.recurrence, "Every week until Nov 2, 2026"),
    ];
    for (line, field, said) in words {
        let inv = parse(&cal(&[MON, line], "REQUEST")).unwrap();
        assert_eq!(field(&inv), said, "{line}");
    }
}

#[test]
fn an_event_with_no_summary_has_no_title() {
    let text = "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:n\r\nDTSTART:20261105T100000\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n";
    assert_eq!(parse(text).unwrap().summary, "(no title)");
}

#[test]
fn a_long_description_is_cut() {
    let line = format!("DESCRIPTION:{}", "words ".repeat(400));
    assert_eq!(parse(&cal(&[MON, &line], "REQUEST")).unwrap().description.chars().count(), 1500);
}

// ── how the lines come ──────────────────────────────────────────────────────

#[test]
fn however_the_lines_come_it_is_the_same_event() {
    let plain = cal(&[MON, MON_END], "REQUEST");
    let rough = [
        plain.replace("\r\n", "\n"),
        plain.replace("\r\n", "\r"),
        format!("\u{feff}{plain}"),
        cal(&[MON, "", "", MON_END], "REQUEST"),
        format!("Content-Type: text/calendar\r\n\r\n{plain}"),
        format!("{plain}-- \r\nsent from a phone\r\n"),
        cal(&["not a line at all", MON, ":y", MON_END], "REQUEST"),
        format!("not a line at all\r\n{plain}"),
        plain.replace("END:VCALENDAR\r\n", ""),
    ];
    let want = vec![parse(&plain).unwrap()];
    for text in rough {
        assert_eq!(invite::events(&text, here(&Utc)), want, "{text:?}");
    }
}

#[test]
fn an_event_with_no_calendar_around_it_is_still_an_event() {
    let inv = parse("BEGIN:VEVENT\r\nUID:b\r\nSUMMARY:Bare\r\nDTSTART:20261105T100000\r\nEND:VEVENT\r\n").unwrap();
    assert_eq!(
        (inv.uid.as_str(), inv.summary.as_str(), inv.start_iso.as_str(), inv.method.as_str()),
        ("b", "Bare", "2026-11-05T10:00:00", "")
    );
}

#[test]
fn what_holds_no_event_has_no_card() {
    let no_event = [
        "",
        "not a calendar at all",
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n",
        "BEGIN:VCALENDAR\r\nBEGIN:VTODO\r\nUID:t\r\nSUMMARY:Task\r\nEND:VTODO\r\nEND:VCALENDAR\r\n",
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:c\r\nDTSTART:20261105T100000\r\n",
    ];
    for text in no_event {
        assert!(parse(text).is_none(), "{text:?}");
        assert!(invite::events(text, here(&Utc)).is_empty(), "{text:?}");
        assert!(listed(text).is_empty(), "{text:?}");
    }
}

#[test]
fn an_event_with_no_date_it_can_read_has_a_card_no_place_on_the_list_and_a_reply_without_one() {
    let no_date: [&[&str]; 6] = [
        &[],
        &["DTSTART:garbage", "DTEND:also garbage"],
        &["DTSTART:20261345T100000"],
        &["DTSTART:PT1H"],
        &["DTSTART:100000"],
        &["DTSTART:20261105T100000/PT1H"],
    ];
    for lines in no_date {
        let text = cal(lines, "REQUEST");
        let inv = parse(&text).unwrap();
        assert_eq!(
            (inv.uid.as_str(), inv.start_iso.as_str(), inv.when.as_str()),
            ("u1@example.test", "", ""),
            "{lines:?}"
        );
        assert!(next(&inv, &at(2026, 11, 1, 12, 0), &Utc).is_none(), "{lines:?}");
        assert!(listed(&text).is_empty(), "{lines:?}");
        let reply = reply(&inv, "ACCEPTED");
        assert!(reply.contains("UID:u1@example.test") && !reply.contains("DTSTART"), "{lines:?}");
    }
}

// ── when a one-off happens ──────────────────────────────────────────────────

#[test]
fn when_a_one_off_happens() {
    let one_offs: [(&[&str], &str, &str, &str, i64); 16] = [
        (
            &["DTSTART:20261105T100000Z", "DTEND:20261105T103000Z"],
            "2026-11-05T10:00:00+00:00",
            "2026-11-05T10:30:00+00:00",
            "Thu, Nov 5 · 10:00 AM–10:30 AM",
            30,
        ),
        (
            &[BERLIN_AT_10, "DTEND;TZID=Europe/Berlin:20261105T103000"],
            "2026-11-05T10:00:00+01:00",
            "2026-11-05T10:30:00+01:00",
            "Thu, Nov 5 · 9:00 AM–9:30 AM",
            30,
        ),
        (&["DTSTART;tzid=Europe/Berlin:20261105T100000"], "2026-11-05T10:00:00+01:00", "", "Thu, Nov 5 · 9:00 AM", 0),
        (
            &["DTSTART;TZID=\"Europe/Berlin\":20261105T100000"],
            "2026-11-05T10:00:00+01:00",
            "",
            "Thu, Nov 5 · 9:00 AM",
            0,
        ),
        (&["DTSTART;TZID=Europe/Berlin:20261105T100000Z"], "2026-11-05T10:00:00+00:00", "", "Thu, Nov 5 · 10:00 AM", 0),
        (
            &["DTSTART:20261105T233000", "DTEND:20261106T003000"],
            "2026-11-05T23:30:00",
            "2026-11-06T00:30:00",
            "Thu, Nov 5 · 11:30 PM → Fri, Nov 6 12:30 AM",
            60,
        ),
        (
            &[ON_NOV_5, "DTEND:20261105T090000"],
            "2026-11-05T10:00:00",
            "2026-11-05T09:00:00",
            "Thu, Nov 5 · 10:00 AM–9:00 AM",
            0,
        ),
        (
            &[ON_NOV_5, "DURATION:PT1H"],
            "2026-11-05T10:00:00",
            "2026-11-05T11:00:00",
            "Thu, Nov 5 · 10:00 AM–11:00 AM",
            60,
        ),
        (
            &["DTSTART;VALUE=DATE:20261105", "DTEND;VALUE=DATE:20261106"],
            "2026-11-05T00:00:00",
            "2026-11-06T00:00:00",
            "Thu, Nov 5",
            1440,
        ),
        (&["DTSTART;VALUE=DATE:20261105"], "2026-11-05T00:00:00", "", "Thu, Nov 5", 1440),
        (&["DTSTART:20261105", "DTEND:20261106"], "2026-11-05T00:00:00", "2026-11-06T00:00:00", "Thu, Nov 5", 1440),
        (
            &["DTSTART;VALUE=DATE:20261105", "DTEND;VALUE=DATE:20261108"],
            "2026-11-05T00:00:00",
            "2026-11-08T00:00:00",
            "Thu, Nov 5",
            4320,
        ),
        (
            &["DTSTART;VALUE=DATE:20261105", "DURATION:P3D"],
            "2026-11-05T00:00:00",
            "2026-11-08T00:00:00",
            "Thu, Nov 5",
            4320,
        ),
        (&["DTSTART;VALUE=DATE:20261105T100000"], "2026-11-05T10:00:00", "", "Thu, Nov 5 · 10:00 AM", 0),
        (&[MON, ON_NOV_5], "2026-11-05T10:00:00", "", "Thu, Nov 5 · 10:00 AM", 0),
        (&[ON_NOV_5, "EXDATE:20261105T100000"], "2026-11-05T10:00:00", "", "Thu, Nov 5 · 10:00 AM", 0),
    ];
    for (lines, start, end, when, minutes) in one_offs {
        let inv = parse(&cal(lines, "REQUEST")).unwrap();
        assert_eq!((inv.start_iso.as_str(), inv.end_iso.as_str(), inv.when.as_str()), (start, end, when), "{lines:?}");
        let (s, e) = next(&inv, &at(2026, 11, 1, 12, 0), &Utc).unwrap();
        assert_eq!(e.minus(&s) / 60, minutes, "{lines:?}");
    }
}

#[test]
fn a_time_with_no_seconds_is_read_as_hours_and_minutes() {
    let inv = parse(&cal(&["DTSTART:20261105T1000", "DTEND:20261105T1030"], "REQUEST")).unwrap();
    assert_eq!(inv.end_iso, "2026-11-05T10:30:00");
}

#[test]
fn a_time_in_a_zone_shows_at_this_machines_time() {
    let text = cal(&[BERLIN_AT_10, "DTEND;TZID=Europe/Berlin:20261105T103000"], "REQUEST");
    let expected = [
        ("UTC", "Thu, Nov 5 · 9:00 AM–9:30 AM", "Studio sync 2026-11-05T09:00 to 2026-11-05T09:30"),
        ("Asia/Tokyo", "Thu, Nov 5 · 6:00 PM–6:30 PM", "Studio sync 2026-11-05T18:00 to 2026-11-05T18:30"),
        ("America/New_York", "Thu, Nov 5 · 4:00 AM–4:30 AM", "Studio sync 2026-11-05T04:00 to 2026-11-05T04:30"),
    ];
    for (zone, when, listed) in expected {
        let me = person(zone);
        assert_eq!(parse_in(&text, &*me).unwrap().when, when, "{zone}");
        assert_eq!(listed_at(&text, &at(2026, 11, 1, 12, 0), &me), [listed], "{zone}");
    }
}

// ── when a series happens ───────────────────────────────────────────────────

const STARTS: [(&str, &[&str]); 6] = [
    ("floating", &[MON, MON_END]),
    ("a-zone", &["DTSTART;TZID=Europe/Berlin:20261005T100000", "DTEND;TZID=Europe/Berlin:20261005T103000"]),
    ("utc", &["DTSTART:20261005T100000Z", "DTEND:20261005T103000Z"]),
    ("all-day", &["DTSTART;VALUE=DATE:20261005", "DTEND;VALUE=DATE:20261006"]),
    ("an-unknown-tzid", &["DTSTART;TZID=Nowhere/Land:20261005T100000"]),
    ("late-evening", &["DTSTART;TZID=America/New_York:20261005T233000", "DTEND;TZID=America/New_York:20261006T003000"]),
];

fn start(name: &str) -> &'static [&'static str] {
    STARTS.iter().find(|(n, _)| *n == name).unwrap().1
}

fn with(lines: &[&str], more: &[&str]) -> Vec<String> {
    lines.iter().chain(more).map(|l| (*l).to_owned()).collect()
}

fn refs(lines: &[String]) -> Vec<&str> {
    lines.iter().map(String::as_str).collect()
}

const WEEKS: &str = "2026-10-05 2026-10-12 2026-10-19 2026-10-26 2026-11-02 2026-11-09";

#[test]
fn every_rule_comes_to_these_dates_however_the_start_is_said() {
    let every_rule = [
        ("FREQ=DAILY", "2026-10-05 2026-10-06 2026-10-07 2026-10-08 2026-10-09 2026-10-10"),
        ("FREQ=DAILY;INTERVAL=2", "2026-10-05 2026-10-07 2026-10-09 2026-10-11 2026-10-13 2026-10-15"),
        ("FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR", "2026-10-05 2026-10-06 2026-10-07 2026-10-08 2026-10-09 2026-10-12"),
        (
            "FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR;INTERVAL=2",
            "2026-10-05 2026-10-07 2026-10-09 2026-10-13 2026-10-15 2026-10-19",
        ),
        ("FREQ=WEEKLY", WEEKS),
        ("FREQ=WEEKLY;INTERVAL=2", "2026-10-05 2026-10-19 2026-11-02 2026-11-16 2026-11-30 2026-12-14"),
        ("FREQ=WEEKLY;BYDAY=MO", WEEKS),
        ("FREQ=WEEKLY;BYDAY=WE,MO", "2026-10-05 2026-10-07 2026-10-12 2026-10-14 2026-10-19 2026-10-21"),
        ("FREQ=WEEKLY;BYDAY=WE,MO;INTERVAL=2", "2026-10-05 2026-10-07 2026-10-19 2026-10-21 2026-11-02 2026-11-04"),
        ("FREQ=MONTHLY", "2026-10-05 2026-11-05 2026-12-05 2027-01-05 2027-02-05 2027-03-05"),
        ("FREQ=MONTHLY;INTERVAL=2", "2026-10-05 2026-12-05 2027-02-05 2027-04-05 2027-06-05 2027-08-05"),
        ("FREQ=MONTHLY;BYMONTHDAY=5", "2026-10-05 2026-11-05 2026-12-05 2027-01-05 2027-02-05 2027-03-05"),
        ("FREQ=YEARLY", "2026-10-05 2027-10-05 2028-10-05 2029-10-05 2030-10-05 2031-10-05"),
        ("FREQ=YEARLY;INTERVAL=2", "2026-10-05 2028-10-05 2030-10-05 2032-10-05 2034-10-05 2036-10-05"),
    ];
    for zone in MACHINE_ZONES {
        let me = person(zone);
        for (name, lines) in STARTS {
            for (rule, dates) in every_rule {
                let rrule = format!("RRULE:{rule}");
                let inv = parse_in(&cal(&refs(&with(lines, &[&rrule])), "REQUEST"), &*me).unwrap();
                let found = walk(&inv, 6, &*me);
                // Each at the time and for the length of the first: the clocks change, and a series doesn't.
                let clock_and_length = |(s, e): &(Dt, Dt)| (s.second_of_day(), e.minus(s));
                assert!(
                    found.iter().all(|o| clock_and_length(o) == clock_and_length(&found[0])),
                    "{zone} {name} {rule}"
                );
                let came_to: Vec<String> = found.iter().map(|(s, _)| mm_calendar::time::date_iso(s.day())).collect();
                assert_eq!(came_to.join(" "), dates, "{zone} {name} {rule}");
            }
        }
    }
}

#[test]
fn where_a_series_ends() {
    let (oct_26, nov_2) = ("2026-10-26", "2026-11-02");
    let endings: [(&str, &[&str]); 4] = [
        ("COUNT=5", &[]),
        // 09:00 UTC is when Berlin's 10:00 is.
        ("UNTIL=20261102T090000Z", &["floating", "utc", "an-unknown-tzid", "late-evening"]),
        // In the start's own terms.
        ("UNTIL=20261102T100000", &["late-evening"]),
        // All of that day.
        ("UNTIL=20261102", &[]),
    ];
    for (name, lines) in STARTS {
        for (ending, earlier) in endings {
            let rule = format!("RRULE:FREQ=WEEKLY;{ending}");
            let found = days(&refs(&with(lines, &[&rule])), 9);
            let last = if earlier.contains(&name) { oct_26 } else { nov_2 };
            assert_eq!(found.last().map(String::as_str), Some(last), "{name} {ending}");
        }
    }
}

#[test]
fn a_utc_until_on_a_floating_series_goes_by_this_machines_clock() {
    for (zone, last) in [("UTC", "2026-10-26"), ("Asia/Tokyo", "2026-11-02"), ("America/New_York", "2026-10-26")] {
        let lines = with(start("floating"), &["RRULE:FREQ=WEEKLY;UNTIL=20261102T090000Z"]);
        let found = days_in(&refs(&lines), 9, &*person(zone));
        assert_eq!(found.last().map(String::as_str), Some(last), "{zone}");
    }
}

#[test]
fn an_exdate_strikes_that_occurrence_and_no_other() {
    let struck = [
        ("floating", "EXDATE:20261102T100000"),
        ("floating", "EXDATE:20261102T100000,20261116T100000"),
        ("floating", "EXDATE:20261102T100000\r\nEXDATE:20261116T100000"),
        ("floating", "EXDATE:garbage,20261102T100000"),
        ("floating", "EXDATE;VALUE=DATE:20261102"),
        ("a-zone", "EXDATE;TZID=Europe/Berlin:20261102T100000"),
        ("a-zone", "EXDATE:20261102T090000Z"),
        ("utc", "EXDATE:20261102T100000Z"),
        ("all-day", "EXDATE;VALUE=DATE:20261102"),
        ("all-day", "EXDATE:20261102T090000"),
        ("an-unknown-tzid", "EXDATE:20261102T100000"),
        ("late-evening", "EXDATE;TZID=America/New_York:20261102T233000"),
    ];
    let want = ["2026-10-05", "2026-10-12", "2026-10-19", "2026-10-26", "2026-11-09"];
    for zone in MACHINE_ZONES {
        for (name, exdate) in struck {
            let mut lines = with(start(name), &["RRULE:FREQ=WEEKLY"]);
            lines.extend(exdate.split("\r\n").map(str::to_owned));
            assert_eq!(days_in(&refs(&lines), 5, &*person(zone)), want, "{zone} {name} {exdate}");
        }
    }
}

#[test]
fn an_exdate_that_wont_read_strikes_nothing() {
    assert_eq!(days(&[MON, MON_END, "RRULE:FREQ=WEEKLY", "EXDATE:garbage"], 5)[4], "2026-11-02");
}

#[test]
fn what_a_series_comes_to() {
    let series: [(&str, &str, &str); 47] = [
        // The ways a rule is said.
        (MON, "RRULE:INTERVAL=2;BYDAY=MO;FREQ=WEEKLY;COUNT=3", "2026-10-05 2026-10-19 2026-11-02"),
        (MON, "RRULE:freq=weekly;interval=2;byday=mo", "2026-10-05 2026-10-19 2026-11-02 2026-11-16"),
        (MON, "RRULE:FREQ=DAILY\r\nRRULE:FREQ=WEEKLY", "2026-10-05 2026-10-12 2026-10-19 2026-10-26"),
        (MON, "RRULE:FREQ=WEEKLY;WKST=MO;BYDAY=MO", "2026-10-05 2026-10-12 2026-10-19 2026-10-26"),
        (MON, "RRULE:whenever", "2026-10-05"),
        // Months and years that haven't the day.
        ("DTSTART:20261231T100000", "RRULE:FREQ=MONTHLY", "2026-12-31 2027-01-31 2027-03-31 2027-05-31"),
        ("DTSTART:20261006T100000", "RRULE:FREQ=MONTHLY;BYDAY=1TU", "2026-10-06 2026-11-03 2026-12-01 2027-01-05"),
        ("DTSTART:20261030T100000", "RRULE:FREQ=MONTHLY;BYDAY=-1FR", "2026-10-30 2026-11-27 2026-12-25 2027-01-29"),
        ("DTSTART:20261031T100000", "RRULE:FREQ=MONTHLY;BYMONTHDAY=-1", "2026-10-31 2026-11-30 2026-12-31 2027-01-31"),
        ("DTSTART:20280229T100000", "RRULE:FREQ=YEARLY", "2028-02-29 2032-02-29 2036-02-29 2040-02-29"),
        (
            "DTSTART:20261113T100000",
            "RRULE:FREQ=MONTHLY;BYMONTHDAY=13;BYDAY=FR",
            "2026-11-13 2027-08-13 2028-10-13 2029-04-13",
        ),
        // WKST moves which days share a week, and so which come round under an INTERVAL. RFC 5545's own example.
        (
            "DTSTART:20261004T100000",
            "RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=SU,MO;WKST=SU",
            "2026-10-04 2026-10-05 2026-10-18 2026-10-19",
        ),
        // Parts a rule from a real calendar may have.
        (MON, "RRULE:FREQ=MONTHLY;BYDAY=MO;BYSETPOS=1", "2026-10-05 2026-11-02 2026-12-07 2027-01-04"),
        (MON, "RRULE:FREQ=YEARLY;BYMONTH=10,11", "2026-10-05 2026-11-05 2027-10-05 2027-11-05"),
        (MON, "RRULE:FREQ=MONTHLY;BYMONTH=11,12;BYDAY=1MO", "2026-10-05 2026-11-02 2026-12-07 2027-11-01"),
        (MON, "RRULE:FREQ=YEARLY;BYWEEKNO=41,45;BYDAY=MO", "2026-10-05 2026-11-02 2027-10-11 2027-11-08"),
        (MON, "RRULE:FREQ=YEARLY;BYYEARDAY=278,310", "2026-10-05 2026-11-06 2027-10-05 2027-11-06"),
        (MON, "RRULE:FREQ=YEARLY;BYDAY=1MO", "2026-10-05 2027-01-04 2028-01-03 2029-01-01"),
        // What the RFC forbids or leaves open, and dateutil reads.
        (MON, "RRULE:FREQ=WEEKLY;BYMONTHDAY=5", "2026-10-05 2026-11-05 2026-12-05 2027-01-05"),
        (MON, "RRULE:FREQ=WEEKLY;BYDAY=1MO", "2026-10-05 2026-10-12 2026-10-19 2026-10-26"),
        (MON, "RRULE:FREQ=WEEKLY;BYDAY=TU", "2026-10-05 2026-10-06 2026-10-13 2026-10-20"),
        // How it ends, or doesn't.
        (MON, "RRULE:FREQ=WEEKLY;COUNT=3", "2026-10-05 2026-10-12 2026-10-19"),
        (MON, "RRULE:FREQ=WEEKLY;COUNT=0", "2026-10-05"),
        (MON, "RRULE:FREQ=WEEKLY;COUNT=many", "2026-10-05 2026-10-12 2026-10-19 2026-10-26"),
        (MON, "RRULE:FREQ=WEEKLY;COUNT=999999999", "2026-10-05 2026-10-12 2026-10-19 2026-10-26"),
        (MON, "RRULE:FREQ=WEEKLY;UNTIL=soon", "2026-10-05 2026-10-12 2026-10-19 2026-10-26"),
        (MON, "RRULE:FREQ=WEEKLY;UNTIL=20261001T000000", ""),
        (MON, "RRULE:FREQ=WEEKLY;UNTIL=99991231T235959Z", "2026-10-05 2026-10-12 2026-10-19 2026-10-26"),
        (MON, "RRULE:FREQ=DAILY;INTERVAL=0", "2026-10-05 2026-10-06 2026-10-07 2026-10-08"),
        (MON, "RRULE:FREQ=DAILY;INTERVAL=-3", "2026-10-05 2026-10-06 2026-10-07 2026-10-08"),
        (MON, "RRULE:FREQ=YEARLY;INTERVAL=9000", "2026-10-05"),
        // A rule it won't expand has no next date, and the Agenda falls back on its first.
        (MON, "RRULE:FREQ=SECONDLY", ""),
        (MON, "RRULE:FREQ=MINUTELY;INTERVAL=5", ""),
        (MON, "RRULE:FREQ=HOURLY", ""),
        (MON, "RRULE:FREQ=FORTNIGHTLY", ""),
        (MON, "RRULE:BYDAY=MO", ""),
        (MON, "RRULE:FREQ=WEEKLY;BYDAY=XX", ""),
        (MON, "RRULE:FREQ=WEEKLY;INTERVAL=often", ""),
        (MON, "RRULE:FREQ=WEEKLY;X-EXAMPLE=1", ""),
        // RDATE, on its own and beside a rule. A period gives its start.
        (MON, "RDATE:20261105T100000", "2026-10-05 2026-11-05"),
        (MON, "RRULE:FREQ=WEEKLY;COUNT=2\r\nRDATE:20261201T100000", "2026-10-05 2026-10-12 2026-12-01"),
        (MON, "RDATE;VALUE=PERIOD:20261105T100000/PT2H", "2026-10-05 2026-11-05"),
        (MON, "RDATE:20261105T100000,20261112T100000\r\nEXDATE:20261105T100000", "2026-10-05 2026-11-12"),
        ("DTSTART;VALUE=DATE:20261005", "RDATE;VALUE=DATE:20261105", "2026-10-05 2026-11-05"),
        (
            "DTSTART;TZID=Europe/Berlin:20261005T100000",
            "RDATE;TZID=Europe/Berlin:20261105T100000,20261201T100000",
            "2026-10-05 2026-11-05 2026-12-01",
        ),
        // Said beside a time of day.
        (MON, "RRULE:FREQ=MONTHLY;BYDAY=MO,TU;BYSETPOS=-1", "2026-10-05 2026-10-27 2026-11-30 2026-12-29"),
        (MON, "RRULE:FREQ=MONTHLY;BYMONTHDAY=1,15", "2026-10-05 2026-10-15 2026-11-01 2026-11-15"),
    ];
    for (start, rest, dates) in series {
        let mut lines = vec![start];
        lines.extend(rest.split("\r\n"));
        let want: Vec<&str> = dates.split_whitespace().collect();
        assert_eq!(days(&lines, 4), want, "{start} {rest}");
    }
}

#[test]
fn a_rule_that_never_comes_to_a_date_comes_to_none() {
    assert!(days(&["DTSTART:20261105T100000", "RRULE:FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=30"], 4).is_empty());
}

#[test]
fn a_rule_can_change_the_time_of_day() {
    let at_ = |rule: &str| -> Vec<String> {
        let rrule = format!("RRULE:{rule}");
        let inv = parse(&cal(&[MON, MON_END, &rrule], "REQUEST")).unwrap();
        walk(&inv, 4, &Utc).iter().map(|(s, _)| s.minutes_iso()).collect()
    };
    assert_eq!(
        at_("FREQ=DAILY;BYHOUR=10,16"),
        ["2026-10-05T10:00", "2026-10-05T16:00", "2026-10-06T10:00", "2026-10-06T16:00"]
    );
    assert_eq!(
        at_("FREQ=DAILY;BYMINUTE=0,30"),
        ["2026-10-05T10:00", "2026-10-05T10:30", "2026-10-06T10:00", "2026-10-06T10:30"]
    );
}

#[test]
fn a_series_keeps_its_clock_where_the_clocks_change() {
    let at_ = |start: &str, when: [Dt; 2]| -> Vec<String> {
        let inv = parse(&cal(&[start, "RRULE:FREQ=WEEKLY"], "REQUEST")).unwrap();
        when.iter().map(|after| minutes(&next(&inv, after, &Utc).unwrap().0)).collect()
    };
    // The hour said twice: the first of them.
    assert_eq!(
        at_("DTSTART;TZID=Europe/Berlin:20261018T023000", [at(2026, 10, 24, 12, 0), at(2026, 10, 26, 12, 0)]),
        ["2026-10-25T02:30+02:00", "2026-11-01T02:30+01:00"]
    );
    // The hour that isn't there: an hour on.
    assert_eq!(
        at_("DTSTART;TZID=Europe/Berlin:20270321T023000", [at(2027, 3, 27, 12, 0), at(2027, 3, 29, 12, 0)]),
        ["2027-03-28T02:30+01:00", "2027-04-04T02:30+02:00"]
    );
}

// ── a moved occurrence, sent with its series ────────────────────────────────

const THE_SERIES: [&str; 7] =
    ["BEGIN:VEVENT", "UID:s@example.test", "SUMMARY:Standup", MON, MON_END, "RRULE:FREQ=WEEKLY", "END:VEVENT"];
const ITS_MOVE: [&str; 7] = [
    "BEGIN:VEVENT",
    "UID:s@example.test",
    "SUMMARY:Standup (moved)",
    "RECURRENCE-ID:20261102T100000",
    "DTSTART:20261103T150000",
    "DTEND:20261103T153000",
    "END:VEVENT",
];

fn series(parts: &[&[&str]]) -> String {
    let mut lines = vec!["BEGIN:VCALENDAR", "VERSION:2.0", "METHOD:REQUEST"];
    for part in parts {
        lines.extend(part.iter());
    }
    lines.extend(["END:VCALENDAR", ""]);
    lines.join("\r\n")
}

#[test]
fn a_moved_occurrence_takes_its_slot_from_the_series_in_either_order() {
    for text in [series(&[&THE_SERIES, &ITS_MOVE]), series(&[&ITS_MOVE, &THE_SERIES])] {
        assert_eq!(
            listed(&text),
            ["Standup (moved) 2026-11-03T15:00 to 2026-11-03T15:30", "Standup 2026-11-09T10:00 to 2026-11-09T10:30"]
        );
        assert_eq!(parse(&text).unwrap().summary, "Standup");
    }
}

#[test]
fn a_revision_count_is_read_and_what_wont_read_is_none() {
    assert_eq!(parse(&cal(&[MON, "SEQUENCE:4"], "REQUEST")).unwrap().sequence, 4);
    assert_eq!(parse(&cal(&[MON], "REQUEST")).unwrap().sequence, 0);
    let huge = format!("SEQUENCE:{}", "9".repeat(5000));
    let past = format!("SEQUENCE:{}", 1_i64 << 31);
    for said in ["SEQUENCE:later", "SEQUENCE:-3", huge.as_str(), past.as_str()] {
        assert_eq!(parse(&cal(&[MON, said], "REQUEST")).unwrap().sequence, 0, "{said}");
    }
}

#[test]
fn a_range_says_so_and_only_where_it_said_it() {
    for line in
        ["RECURRENCE-ID;RANGE=THISANDFUTURE:20261102T100000", "RECURRENCE-ID;RANGE=thisandfuture:20261102T100000"]
    {
        let inv = parse(&cal(&[MON, line], "REQUEST")).unwrap();
        assert!(inv.this_and_future && inv.to_json()["this_and_future"] == true, "{line}");
    }
    let none: [&[&str]; 4] = [
        &[],
        &["RECURRENCE-ID:20261102T100000"],
        &["RECURRENCE-ID;RANGE=THISANDPRIOR:20261102T100000"],
        &["RECURRENCE-ID;RANGE=THISANDFUTURE:x"],
    ];
    for lines in none {
        let inv = parse(&cal(&refs(&with(&[MON], lines)), "REQUEST")).unwrap();
        assert!(inv.to_json().get("this_and_future").is_none(), "{lines:?}");
    }
}

const ROOT: [&str; 7] = THE_SERIES;

fn moved_on(summary: &str, recurrence_id: &str, start: &str, end: &str, rest: &[&str]) -> Vec<String> {
    let mut lines = vec![
        "BEGIN:VEVENT".to_owned(),
        "UID:s@example.test".to_owned(),
        format!("SUMMARY:{summary}"),
        format!("RECURRENCE-ID;RANGE=THISANDFUTURE:{recurrence_id}"),
        format!("DTSTART:{start}"),
        format!("DTEND:{end}"),
    ];
    lines.extend(rest.iter().map(|l| (*l).to_owned()));
    lines.push("END:VEVENT".to_owned());
    lines
}

fn listed_on(text: &str, year: i64, month: u32, day: u32) -> Vec<(String, String)> {
    upcoming(Arc::new(Stored::one(text)), &at(year, month, day, 12, 0), 4000, Arc::new(Utc))
        .iter()
        .map(|x| (x["summary"].as_str().unwrap().to_owned(), x["start"].as_str().unwrap().to_owned()))
        .collect()
}

#[test]
fn this_and_future_moves_every_occurrence_from_that_one_on() {
    let later = moved_on("Standup (later)", "20261116T100000", "20261116T150000", "20261116T153000", &[]);
    let text = series(&[&ROOT, &refs(&later)]);
    assert_eq!(
        listed(&text),
        ["Standup 2026-11-02T10:00 to 2026-11-02T10:30", "Standup (later) 2026-11-16T15:00 to 2026-11-16T15:30"]
    );
    assert_eq!(listed_on(&text, 2026, 11, 17), [("Standup (later)".to_owned(), "2026-11-23T15:00".to_owned())]);
}

#[test]
fn once_the_old_part_is_over_the_new_part_goes_on_week_on_week() {
    let later = moved_on("Standup (later)", "20261005T100000", "20261005T150000", "20261005T160000", &[]);
    assert_eq!(listed(&series(&[&ROOT, &refs(&later)])), ["Standup (later) 2026-11-02T15:00 to 2026-11-02T16:00"]);
}

#[test]
fn this_and_future_may_move_to_another_day_or_earlier() {
    let wed = moved_on("Standup (Wed)", "20261005T100000", "20261007T100000", "20261007T103000", &[]);
    assert_eq!(listed(&series(&[&ROOT, &refs(&wed)])), ["Standup (Wed) 2026-11-04T10:00 to 2026-11-04T10:30"]);
    let early = moved_on("Standup (early)", "20261005T100000", "20261005T080000", "20261005T083000", &[]);
    assert_eq!(listed(&series(&[&ROOT, &refs(&early)])), ["Standup (early) 2026-11-02T08:00 to 2026-11-02T08:30"]);
}

#[test]
fn a_series_that_ran_out_stays_run_out_after_a_range_moves_it() {
    let five = [
        "BEGIN:VEVENT",
        "UID:s@example.test",
        "SUMMARY:Standup",
        MON,
        MON_END,
        "RRULE:FREQ=WEEKLY;COUNT=5",
        "END:VEVENT",
    ];
    let later = moved_on("Standup (later)", "20261019T100000", "20261019T150000", "20261019T153000", &[]);
    let text = series(&[&five, &refs(&later)]);
    assert_eq!(listed(&text), ["Standup (later) 2026-11-02T15:00 to 2026-11-02T15:30"]);
    assert!(listed_on(&text, 2026, 11, 3).is_empty());
}

#[test]
fn the_rule_cuts_at_the_first_of_two_ranges_and_each_runs_to_the_next() {
    let later = moved_on("Later", "20261109T100000", "20261109T150000", "20261109T153000", &[]);
    let still = moved_on("Later still", "20261123T100000", "20261123T170000", "20261123T173000", &[]);
    let text = series(&[&ROOT, &refs(&later), &refs(&still)]);
    assert_eq!(
        listed(&text),
        [
            "Standup 2026-11-02T10:00 to 2026-11-02T10:30",
            "Later 2026-11-09T15:00 to 2026-11-09T15:30",
            "Later still 2026-11-23T17:00 to 2026-11-23T17:30"
        ]
    );
    assert_eq!(listed_on(&text, 2026, 11, 17), [("Later still".to_owned(), "2026-11-23T17:00".to_owned())]);
}

#[test]
fn a_range_from_the_first_occurrence_leaves_nothing_of_the_old_one() {
    let later = moved_on("Later", "20261005T100000", "20261005T150000", "20261005T153000", &[]);
    assert_eq!(listed(&series(&[&ROOT, &refs(&later)])), ["Later 2026-11-02T15:00 to 2026-11-02T15:30"]);
}

#[test]
fn an_occurrence_moved_on_its_own_after_a_range_is_still_struck_from_it() {
    let alone = [
        "BEGIN:VEVENT",
        "UID:s@example.test",
        "SUMMARY:Just once",
        "RECURRENCE-ID:20261102T100000",
        "DTSTART:20261103T090000",
        "DTEND:20261103T093000",
        "END:VEVENT",
    ];
    let later = moved_on("Later", "20261005T100000", "20261005T150000", "20261005T153000", &[]);
    assert_eq!(
        listed(&series(&[&ROOT, &refs(&later), &alone])),
        ["Just once 2026-11-03T09:00 to 2026-11-03T09:30", "Later 2026-11-09T15:00 to 2026-11-09T15:30"]
    );
}

#[test]
fn a_cancelled_range_ends_the_series_there() {
    let gone = [
        "BEGIN:VEVENT",
        "UID:s@example.test",
        "SUMMARY:Standup",
        "RECURRENCE-ID;RANGE=THISANDFUTURE:20261109T100000",
        "DTSTART:20261109T100000",
        "STATUS:CANCELLED",
        "END:VEVENT",
    ];
    let text = series(&[&ROOT, &gone]);
    assert_eq!(listed(&text), ["Standup 2026-11-02T10:00 to 2026-11-02T10:30"]);
    assert!(listed_on(&text, 2026, 11, 3).is_empty());
}

#[test]
fn a_range_with_no_live_series_beside_it_is_a_word_about_one_date() {
    let later = moved_on("Later", "20261109T100000", "20261109T150000", "20261109T153000", &[]);
    assert_eq!(listed(&series(&[&refs(&later)])), ["Later 2026-11-09T15:00 to 2026-11-09T15:30"]);
    let cancelled = [
        "BEGIN:VEVENT",
        "UID:s@example.test",
        "SUMMARY:Standup",
        MON,
        "RRULE:FREQ=WEEKLY",
        "STATUS:CANCELLED",
        "END:VEVENT",
    ];
    assert!(listed(&series(&[&cancelled, &refs(&later)])).is_empty());
}

#[test]
fn a_range_in_a_zone_keeps_its_wall_clock_across_a_change_of_clocks() {
    let root = [
        "BEGIN:VEVENT",
        "UID:s@example.test",
        "SUMMARY:Standup",
        "DTSTART;TZID=W. Europe Standard Time:20261005T100000",
        "DTEND;TZID=W. Europe Standard Time:20261005T103000",
        "RRULE:FREQ=WEEKLY",
        "END:VEVENT",
    ];
    let moved = [
        "BEGIN:VEVENT",
        "UID:s@example.test",
        "SUMMARY:Later",
        "RECURRENCE-ID;RANGE=THISANDFUTURE;TZID=W. Europe Standard Time:20261012T100000",
        "DTSTART;TZID=W. Europe Standard Time:20261012T120000",
        "DTEND;TZID=W. Europe Standard Time:20261012T123000",
        "END:VEVENT",
    ];
    let found: Vec<(i32, invite::Invite)> =
        invite::events(&series(&[&root, &moved]), here(&Utc)).into_iter().map(|inv| (1, inv)).collect();
    let cut = agenda::cut_up(&found, &Utc);
    let (start, end) = next(&cut[1].1, &at(2026, 11, 1, 12, 0), &Utc).unwrap();
    assert_eq!(
        (start.isoformat(), end.isoformat()),
        ("2026-11-02T12:00:00+01:00".to_owned(), "2026-11-02T12:30:00+01:00".to_owned())
    );
}

#[test]
fn a_moved_occurrence_names_the_slot_it_left_in_its_zone() {
    let lines = ["DTSTART;TZID=Europe/Berlin:20261110T150000", "RECURRENCE-ID;TZID=Europe/Berlin:20261109T100000"];
    let inv = parse(&cal(&lines, "REQUEST")).unwrap();
    assert_eq!(
        (inv.start_iso.as_str(), inv.recurrence_id.as_str()),
        ("2026-11-10T15:00:00+01:00", "2026-11-09T10:00:00+01:00")
    );
}

#[test]
fn a_person_in_another_zone_reads_the_same_dates() {
    let me: Arc<dyn Zone> = person("Asia/Tokyo");
    let lines = with(start("a-zone"), &["RRULE:FREQ=WEEKLY"]);
    assert_eq!(days_in(&refs(&lines), 6, &*me).join(" "), WEEKS);
}

#[test]
fn week_numbers_count_as_dateutil_counts_them() {
    // Each as the Python app's reader expanded it: week 1 the first with four days of the year, the year's last week
    // where the next year's first days are, and a week said with no day holding all seven.
    let weeks: [(&str, &str); 8] = [
        ("RRULE:FREQ=YEARLY;BYWEEKNO=1", "2026-10-05 2027-01-04 2027-01-05 2027-01-06 2027-01-07 2027-01-08"),
        (
            "RRULE:FREQ=YEARLY;BYWEEKNO=1;BYDAY=MO,SU",
            "2026-10-05 2027-01-04 2027-01-10 2028-01-03 2028-01-09 2029-01-01",
        ),
        ("RRULE:FREQ=YEARLY;BYWEEKNO=-1;BYDAY=TH", "2026-10-05 2026-12-31 2027-12-30 2028-12-28 2029-12-27 2030-12-26"),
        ("RRULE:FREQ=YEARLY;BYWEEKNO=53;BYDAY=FR", "2026-10-05 2027-01-01 2032-12-31 2038-01-01 2044-01-01 2049-01-01"),
        (
            "RRULE:FREQ=YEARLY;BYWEEKNO=1,52;WKST=SU;BYDAY=SU,SA",
            "2026-10-05 2026-12-27 2027-01-03 2027-01-09 2027-12-26 2028-01-01",
        ),
        ("RRULE:FREQ=WEEKLY;BYWEEKNO=1,2,53", "2026-10-05 2026-12-28 2026-12-29 2026-12-30 2026-12-31 2027-01-01"),
        ("RRULE:FREQ=MONTHLY;BYWEEKNO=1;BYDAY=FR", "2026-10-05 2027-01-08 2028-01-07 2029-01-05 2030-01-04 2031-01-03"),
        (
            "RRULE:FREQ=DAILY;BYWEEKNO=52,1;BYDAY=TU",
            "2026-10-05 2026-12-22 2027-01-05 2027-12-28 2028-01-04 2028-12-26",
        ),
    ];
    for (rule, dates) in weeks {
        assert_eq!(days(&[MON, rule], 6).join(" "), dates, "{rule}");
    }
    // Weeks no year has, like February 30: the budget is what ends the walk, and there is no date.
    assert!(days(&[MON, "RRULE:FREQ=YEARLY;BYWEEKNO=0,99"], 6).is_empty());
}

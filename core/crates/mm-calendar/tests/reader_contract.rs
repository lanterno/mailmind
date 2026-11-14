//! The invite reader's contract (tests/contract/test_ics_reader_contract.py): an invite is a stranger's text, read on
//! request threads, so nothing panics however broken it is, a TZID means only what the zone database says, and no
//! text or rule costs more than a bound. invite_text.rs says what a text comes to. All fictional.

#![allow(clippy::unwrap_used)]

mod common;

use std::time::{Duration, Instant};

use common::*;
use mm_calendar::invite::{self, Invite, MAX_EVENTS, MAX_TEXT};
use mm_calendar::rsvp;
use mm_calendar::time::{Dt, Tz};
use mm_kernel::zone::Utc;

/// Far above either budget, and far below what any bomb here costs without one; a debug build is slower.
const BOUND: Duration = Duration::from_secs(1);

fn now_utc() -> Dt {
    Dt::from_parts(2026, 11, 1, 12, 0, 0, Tz::Fixed(0)).unwrap()
}

fn calendar(lines: &[&str]) -> String {
    let mut all = vec!["BEGIN:VCALENDAR", "VERSION:2.0", "METHOD:REQUEST"];
    all.extend(lines);
    all.extend(["END:VCALENDAR", ""]);
    all.join("\r\n")
}

fn event(lines: &[&str]) -> String {
    let mut all = vec!["BEGIN:VEVENT", "UID:u", "SUMMARY:Standup"];
    all.extend(lines);
    all.push("END:VEVENT");
    calendar(&all)
}

const FULL: [&str; 34] = [
    "BEGIN:VCALENDAR",
    "PRODID:-//Example//EN",
    "VERSION:2.0",
    "METHOD:REQUEST",
    "BEGIN:VTIMEZONE",
    "TZID:Europe/Berlin",
    "BEGIN:STANDARD",
    "DTSTART:19701025T030000",
    "TZOFFSETFROM:+0200",
    "TZOFFSETTO:+0100",
    "RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU",
    "END:STANDARD",
    "END:VTIMEZONE",
    "BEGIN:VEVENT",
    "UID:full@example.test",
    "SEQUENCE:2",
    "DTSTAMP:20261001T080000Z",
    "SUMMARY:Studio sync\\, weekly",
    "LOCATION:Roof room",
    "DESCRIPTION:Bring\\nnotes",
    "STATUS:CONFIRMED",
    "DTSTART;TZID=Europe/Berlin:20261005T100000",
    "DTEND;TZID=Europe/Berlin:20261005T103000",
    "RRULE:FREQ=WEEKLY;BYDAY=MO;COUNT=9",
    "EXDATE;TZID=Europe/Berlin:20261012T100000",
    "ORGANIZER;CN=Ren Takeda:mailto:ren@example.test",
    "ATTENDEE;CN=\"Ada, L.\";PARTSTAT=NEEDS-ACTION:mailto:ada@example.test",
    "BEGIN:VALARM",
    "ACTION:AUDIO",
    "TRIGGER:-PT15M",
    "END:VALARM",
    "END:VEVENT",
    "END:VCALENDAR",
    "",
];

/// The reader's answers for a text hold together: a card only where there are events, and it one of them; and no
/// event's next occurrence or reply panics or puts a line of its own in the reply.
fn holds_together(text: &str) {
    let found = invite::events(text, here(&Utc));
    let card = parse(text);
    assert_eq!(card.is_none(), found.is_empty(), "{text:?}");
    assert!(card.as_ref().is_none_or(|c| found.contains(c)), "{text:?}");
    for inv in &found {
        serde_json::to_string(&inv.to_json()).unwrap();
        for after in [now_utc(), at(2026, 11, 1, 12, 0)] {
            if let Some((start, end)) = next(inv, &after, &Utc) {
                assert!(end.minus(&start) >= 0, "{text:?}");
            }
        }
        let name = rsvp::name_for(inv, "ada@example.test", "Ada");
        let reply = rsvp::build_reply(inv, "ada@example.test", &name, "ACCEPTED", &now_utc(), &Utc);
        assert!(!reply.replace("\r\n", "").contains(['\r', '\n']), "{reply:?}");
    }
}

#[test]
fn no_broken_text_makes_the_reader_panic() {
    let long_word = "a".repeat(100_000);
    let nested = "BEGIN:VEVENT\r\n".repeat(3000);
    let calendars = "BEGIN:VCALENDAR\r\n".repeat(3000);
    let folds = " x\r\n".repeat(3000);
    let sequence = format!("SEQUENCE:{}", "9".repeat(5000));
    let garbage: Vec<String> = vec![
        String::new(),
        " ".into(),
        "\r\n\r\n".into(),
        "hello there".into(),
        ":".into(),
        ";".into(),
        ":::".into(),
        "BEGIN".into(),
        "BEGIN:".into(),
        "BEGIN:VCALENDAR".into(),
        "END:VCALENDAR".into(),
        "BEGIN:VEVENT".into(),
        "END:VEVENT".into(),
        "BEGIN:VEVENT\r\nUID:a\r\nEND:VEVENT".into(),
        "END:VEVENT\r\nEND:VEVENT\r\nEND:VCALENDAR".into(),
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nEND:VCALENDAR\r\nEND:VEVENT".into(),
        "BEGIN:VCALENDAR\r\nEND:VEVENT\r\nEND:VCALENDAR".into(),
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nDTSTART:2026".into(),
        "\u{0}\u{1}\u{2}\u{7f}".into(),
        "BEGIN:VCALENDAR\u{0}\r\nBEGIN:VEVENT\r\nUID:\u{0}\r\nEND:VEVENT\r\nEND:VCALENDAR".into(),
        "\u{feff}".into(),
        long_word,
        nested,
        calendars,
        folds,
        event(&[
            "DTSTART:",
            "DTEND:",
            "RRULE:",
            "EXDATE:",
            "RDATE:",
            "DURATION:",
            "ORGANIZER:",
            "ATTENDEE:",
            "RECURRENCE-ID:",
        ]),
        event(&["DTSTART", "DTEND", "RRULE", "EXDATE", "RDATE", "DURATION", "ORGANIZER", "ATTENDEE", "RECURRENCE-ID"]),
        event(&["DTSTART:99999999T999999"]),
        event(&["DTSTART:0"]),
        event(&["DTSTART:20261302T100000"]),
        event(&["DTSTART:tomorrow"]),
        event(&["DTSTART;VALUE=DATE:20261102T100000"]),
        event(&["DTSTART;TZID=../../etc/passwd:20261102T100000"]),
        event(&["DTSTART;TZID=:20261102T100000"]),
        event(&["DTSTART;TZID=\u{0}:20261102T100000"]),
        event(&["DTSTART:20261102T100000", "RRULE:every monday"]),
        event(&["DTSTART:20261102T100000", "RRULE:FREQ"]),
        event(&["DTSTART:20261102T100000", "RRULE:=;=;="]),
        event(&["DTSTART:20261102T100000", "RRULE:FREQ=DAILY;INTERVAL=x"]),
        event(&["DTSTART:20261102T100000", "RRULE:FREQ=DAILY;COUNT=x"]),
        event(&["DTSTART:20261102T100000", "RRULE:FREQ=DAILY;UNTIL=x"]),
        event(&["DTSTART:20261102T100000", "RRULE:FREQ=WEEKLY;BYDAY=XX,99,!!"]),
        event(&["DTSTART:20261102T100000", "RRULE:FREQ=WEEKLY", "EXDATE:x,,20261109T100000,y"]),
        event(&["DTSTART:20261102T100000", "RDATE:x,,20261109T100000,y"]),
        event(&["DTSTART:20261102T100000", "DURATION:PxT"]),
        event(&["DTSTART:20261102T100000", "DURATION:P99999999W"]),
        event(&["DTSTART:20261102T100000", "DTEND:20261101T100000"]),
        event(&["ATTENDEE;CN=", "ORGANIZER;CN=\"unterminated:mailto:x"]),
        event(&["DTSTART:20261102T100000", "BEGIN:VALARM", "TRIGGER:x", "END:VALARM"]),
        event(&["DTSTART:00010101T000000Z"]),
        event(&["DTSTART:99991231T235959Z", "RRULE:FREQ=DAILY"]),
        event(&["DTSTART:00000101T000000"]),
        event(&["DTSTART:20261102T100000", "SEQUENCE:later"]),
        event(&["DTSTART:20261102T100000", "SEQUENCE:"]),
        event(&["DTSTART:20261102T100000", "SEQUENCE:-3"]),
        event(&["DTSTART:20261102T100000", &sequence]),
        event(&["DTSTART:20261102T100000", "RECURRENCE-ID;RANGE=NOT-A-RANGE:20261102T100000"]),
        event(&["DTSTART:20261102T100000", "RECURRENCE-ID;RANGE=THISANDFUTURE:x"]),
        event(&["DTSTART:20261102T100000", "RECURRENCE-ID;RANGE=:20261102T100000"]),
        event(&["DTSTART:00010101T000000", "RECURRENCE-ID;RANGE=THISANDFUTURE:00010101T000000Z", "RRULE:FREQ=DAILY"]),
    ];
    for text in garbage {
        holds_together(&text);
    }
}

/// Lines of a real invite, and lines that would trouble one.
fn lines_to_mangle() -> Vec<&'static str> {
    let mut lines = FULL.to_vec();
    lines.extend([
        r"UID:x\ny",
        r"ORGANIZER;CN=a^nb:mailto:c\nd@example.test",
        r"ATTENDEE;CN=a^nb:mailto:ada@example.test",
        r"SUMMARY:a\nb",
        r"LOCATION:a\,b",
        "RECURRENCE-ID;RANGE=THISANDFUTURE:20261019T100000",
        "RECURRENCE-ID;RANGE=THISANDFUTURE;TZID=W. Europe Standard Time:20261019T100000",
        "DTSTART;TZID=W. Europe Standard Time:20261005T100000",
        "SEQUENCE:12",
    ]);
    lines
}

/// A small generator of the same numbers every run: xorshift.
struct Draw(u64);

impl Draw {
    fn below(&mut self, n: usize) -> usize {
        self.0 ^= self.0 << 13;
        self.0 ^= self.0 >> 7;
        self.0 ^= self.0 << 17;
        (self.0 % n.max(1) as u64) as usize
    }
}

#[test]
fn whatever_a_mangled_invite_says_the_reader_holds_together() {
    // A real invite, its lines dropped, moved, cut, or added to: what the Python suite's property test draws.
    let lines = lines_to_mangle();
    let noise = ["", ":", ";", "\\", "^", "\"", "=", ",", "\u{e9}", "\u{0}", "\r", "\n", " ", "x"];
    let mut draw = Draw(0x9e37_79b9_7f4a_7c15);
    for _ in 0..1500 {
        let mut text = Vec::new();
        for _ in 0..draw.below(31) {
            let line = lines[draw.below(lines.len())];
            let line = match draw.below(4) {
                0 => line.to_owned(),
                1 => line.chars().take(draw.below(line.chars().count() + 1)).collect(),
                2 => format!("{line}{}{}", noise[draw.below(noise.len())], noise[draw.below(noise.len())]),
                _ => (0..draw.below(8)).map(|_| noise[draw.below(noise.len())]).collect(),
            };
            text.push(line);
        }
        holds_together(&text.join("\r\n"));
    }
}

#[test]
fn an_escape_in_a_uid_or_an_address_stays_as_it_was_said() {
    let inv = parse(&calendar(&[
        "BEGIN:VEVENT",
        r"UID:a\nb\,c@example.test",
        "DTSTART:20261102T100000",
        r"ORGANIZER:mailto:ren\nX-Evil:1@example.test",
        r"ATTENDEE:mailto:bo\nX-Evil:1@example.test",
        "END:VEVENT",
    ]))
    .unwrap();
    assert_eq!(inv.uid, r"a\nb\,c@example.test");
    assert_eq!(inv.organizer.unwrap().email, r"ren\nX-Evil:1@example.test");
    assert_eq!(inv.attendees[0].email, r"bo\nX-Evil:1@example.test");
}

#[test]
fn nothing_in_an_invite_puts_a_line_of_its_own_in_a_reply() {
    let inv = parse(&calendar(&[
        "BEGIN:VEVENT",
        r"UID:a\nX-Evil:1",
        "DTSTART:20261102T100000",
        r"SUMMARY:s\nX-Evil:1",
        r#"ORGANIZER;CN="Ren^nX-Evil:1":mailto:ren\nX-Evil:1@example.test"#,
        r#"ATTENDEE;CN="Ada^nX-Evil:1":mailto:ada@example.test"#,
        "END:VEVENT",
    ]))
    .unwrap();
    assert!(!format!("{}{}", inv.organizer.as_ref().unwrap().name, inv.attendees[0].name).contains(['\r', '\n']));
    let name = rsvp::name_for(&inv, "ada@example.test", "Ada");
    let reply = rsvp::build_reply(&inv, "ada@example.test", &name, "ACCEPTED", &now_utc(), &Utc);
    assert!(!reply.replace("\r\n ", "").split("\r\n").any(|l| l.starts_with("X-Evil")));
    assert!(!reply.replace("\r\n", "").contains(['\r', '\n']));
}

#[test]
fn what_the_reader_gives_is_the_invite_the_python_app_read() {
    let inv = parse(&FULL.join("\r\n")).unwrap();
    let keys: Vec<String> = inv.to_json().as_object().unwrap().keys().cloned().collect();
    assert_eq!(
        keys,
        [
            "summary",
            "location",
            "description",
            "when",
            "all_day",
            "start_iso",
            "end_iso",
            "organizer",
            "attendees",
            "recurrence",
            "method",
            "status",
            "uid",
            "rrule",
            "exdates",
            "recurrence_id",
            "tzid",
            "sequence"
        ]
    );
    assert_eq!((inv.summary.as_str(), inv.description.as_str()), ("Studio sync, weekly", "Bring\nnotes"));
    assert_eq!(inv.to_json()["attendees"], serde_json::json!([{"name": "Ada, L.", "email": "ada@example.test"}]));
    assert_eq!(inv.rrule, "FREQ=WEEKLY;BYDAY=MO;COUNT=9");
}

#[test]
fn a_next_occurrence_is_in_the_zone_it_was_said_in() {
    for (lines, aware) in [
        (&["DTSTART;TZID=Europe/Berlin:20261105T100000"][..], true),
        (&["DTSTART;TZID=Europe/Berlin:20261005T100000", "RRULE:FREQ=WEEKLY"][..], true),
        (&["DTSTART:20261105T080000Z"][..], true),
        (&["DTSTART:20261005T080000Z", "RRULE:FREQ=WEEKLY"][..], true),
        (&["DTSTART:20261005T100000", "RRULE:FREQ=WEEKLY"][..], false),
    ] {
        let (start, end) = next(&parse(&event(lines)).unwrap(), &now_utc(), &Utc).unwrap();
        assert_eq!((start.is_aware(), end.is_aware()), (aware, aware), "{lines:?}");
    }
    let floating = next(&parse(&event(&["DTSTART:20261005T100000", "RRULE:FREQ=WEEKLY"])).unwrap(), &now_utc(), &Utc);
    assert_eq!(minutes(&floating.unwrap().0), "2026-11-02T10:00");
}

#[test]
fn an_all_day_next_occurrence_is_a_whole_day_from_midnight() {
    let (start, end) =
        next(&parse(&event(&["DTSTART;VALUE=DATE:20261005", "RRULE:FREQ=WEEKLY"])).unwrap(), &now_utc(), &Utc).unwrap();
    assert_eq!((minutes(&start), end.minus(&start)), ("2026-11-02T00:00".to_owned(), 86_400));
}

#[test]
fn events_holds_the_card_and_none_for_no_calendar() {
    assert!(invite::events("no calendar here", here(&Utc)).is_empty());
    let text = FULL.join("\r\n");
    assert!(invite::events(&text, here(&Utc)).contains(&parse(&text).unwrap()));
}

// ── what a TZID means ───────────────────────────────────────────────────────

#[test]
fn a_zone_the_database_knows_is_that_zone() {
    let inv = parse(&event(&["DTSTART;TZID=Europe/Berlin:20261102T100000"])).unwrap();
    assert_eq!((inv.start_iso.as_str(), inv.tzid.as_str()), ("2026-11-02T10:00:00+01:00", "Europe/Berlin"));
}

#[test]
fn an_unknown_tzid_makes_the_time_floating() {
    let long = "x".repeat(500);
    for tzid in ["Nowhere/Land", "../../etc/passwd", "/etc/passwd", "", "Europe", long.as_str()] {
        let start = format!("DTSTART;TZID={tzid}:20261102T100000");
        let end = format!("DTEND;TZID={tzid}:20261102T103000");
        let inv = parse(&event(&[&start, &end])).unwrap();
        assert_eq!(
            (inv.start_iso.as_str(), inv.end_iso.as_str(), inv.tzid.as_str()),
            ("2026-11-02T10:00:00", "2026-11-02T10:30:00", ""),
            "{tzid}"
        );
        assert_eq!(minutes(&next(&inv, &now_utc(), &Utc).unwrap().0), "2026-11-02T10:00", "{tzid}");
    }
}

fn zone_block(tzid: &str, offset: &str, rule: Option<&str>) -> Vec<String> {
    let mut lines = vec![
        "BEGIN:VTIMEZONE".to_owned(),
        format!("TZID:{tzid}"),
        "BEGIN:STANDARD".into(),
        "DTSTART:19700101T000000".into(),
        format!("TZOFFSETFROM:{offset}"),
        format!("TZOFFSETTO:{offset}"),
    ];
    lines.extend(rule.map(str::to_owned));
    lines.extend(["END:STANDARD".into(), "END:VTIMEZONE".into()]);
    lines
}

fn zoned_event(block: &[String], tzid: &str, end: bool) -> String {
    let mut lines: Vec<String> = block.to_vec();
    lines.extend(["BEGIN:VEVENT".into(), "UID:u".into(), format!("DTSTART;TZID={tzid}:20261102T100000")]);
    if end {
        lines.push(format!("DTEND;TZID={tzid}:20261102T103000"));
    }
    lines.push("END:VEVENT".into());
    calendar(&lines.iter().map(String::as_str).collect::<Vec<_>>())
}

#[test]
fn a_name_only_a_vtimezone_or_a_guess_could_explain_floats_too() {
    for tzid in [
        "Customized Time Zone",
        "Customized Time Zone 1",
        "W. Europe Standard Tim",
        "/freeassociation.sourceforge.net/Berlin",
        "/a/b/c/d/e/Standup",
    ] {
        let inv = parse(&zoned_event(&zone_block(tzid, "+0100", None), tzid, false)).unwrap();
        assert_eq!((inv.start_iso.as_str(), inv.tzid.as_str()), ("2026-11-02T10:00:00", ""), "{tzid}");
    }
}

#[test]
fn a_windows_name_or_one_from_a_registry_is_the_zone_it_names() {
    for (tzid, start) in [
        ("W. Europe Standard Time", "2026-11-02T10:00:00+01:00"),
        ("Pacific Standard Time", "2026-11-02T10:00:00-08:00"),
        ("/freeassociation.sourceforge.net/Europe/Berlin", "2026-11-02T10:00:00+01:00"),
        ("/softwarestudio.org/Olson_20011030_5/America/New_York", "2026-11-02T10:00:00-05:00"),
        ("/mozilla.org/20071231_1/America/Argentina/Buenos_Aires", "2026-11-02T10:00:00-03:00"),
    ] {
        // Whatever the invite's own VTIMEZONE says of it: this one, with an offset no such zone has, doesn't move it.
        let inv = parse(&zoned_event(&zone_block(tzid, "+0500", None), tzid, true)).unwrap();
        assert_eq!((inv.start_iso.as_str(), inv.tzid.as_str()), (start, tzid));
        assert_eq!(next(&inv, &now_utc(), &Utc).unwrap().0.isoformat(), start);
    }
}

#[test]
fn a_vtimezone_neither_names_a_zone_nor_changes_a_real_one() {
    parse(&zoned_event(&zone_block("Stranger/Time", "+0500", None), "Stranger/Time", false));
    let inv = parse(&event(&["DTSTART;TZID=Stranger/Time:20261102T100000"])).unwrap();
    assert_eq!((inv.start_iso.as_str(), inv.tzid.as_str()), ("2026-11-02T10:00:00", ""));
    parse(&zoned_event(&zone_block("Europe/Berlin", "+0500", None), "Europe/Berlin", false));
    let inv = parse(&event(&["DTSTART;TZID=Europe/Berlin:20261102T100000"])).unwrap();
    assert_eq!(inv.start_iso, "2026-11-02T10:00:00+01:00");
}

// ── what it costs ───────────────────────────────────────────────────────────

fn costed<T>(work: impl FnOnce() -> T) -> (T, Duration) {
    let began = Instant::now();
    let answer = work();
    (answer, began.elapsed())
}

#[test]
fn no_rule_costs_more_than_a_bound() {
    let hours = (0..24).map(|h| h.to_string()).collect::<Vec<_>>().join(",");
    let minutes_ = (0..60).map(|m| m.to_string()).collect::<Vec<_>>().join(",");
    let every_second = format!("BYHOUR={hours};BYMINUTE={minutes_};BYSECOND={minutes_}");
    let weeks = (1..54).map(|w| w.to_string()).collect::<Vec<_>>().join(",");
    let hostile = [
        "FREQ=SECONDLY".to_owned(),
        "FREQ=MINUTELY".into(),
        "FREQ=HOURLY".into(),
        "FREQ=SECONDLY;COUNT=999999999".into(),
        "FREQ=SECONDLY;UNTIL=99991231T235959Z".into(),
        "FREQ=DAILY;COUNT=999999999999".into(),
        "FREQ=DAILY;UNTIL=99991231T235959Z".into(),
        "FREQ=DAILY;BYMONTH=2;BYMONTHDAY=30".into(),
        "FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=31".into(),
        "FREQ=WEEKLY;BYMONTH=2;BYMONTHDAY=30;BYDAY=MO".into(),
        "FREQ=DAILY;INTERVAL=0".into(),
        "FREQ=DAILY;INTERVAL=-1".into(),
        "FREQ=DAILY;INTERVAL=99999999999".into(),
        format!("FREQ=DAILY;{every_second}"),
        format!("FREQ=YEARLY;{every_second}"),
        format!("FREQ=YEARLY;{every_second};COUNT=999999999"),
        "FREQ=MONTHLY;BYDAY=MO;BYSETPOS=300".into(),
        format!("FREQ=DAILY;BYMONTHDAY={}", ["1", "2"].repeat(20_000).join(",")),
        format!("FREQ=YEARLY;BYWEEKNO={weeks};BYDAY=MO,TU,WE,TH,FR,SA,SU"),
    ];
    let starts = ["DTSTART:20261005T100000", "DTSTART:00010101T000000", "DTSTART;TZID=Europe/Berlin:20261005T100000"];
    for rule in &hostile {
        for start in starts {
            let text = event(&[start, &format!("RRULE:{rule}")]);
            let (inv, took) = costed(|| parse(&text).unwrap());
            assert!(took < BOUND, "{rule} {start}: {took:?}");
            let (_, took) = costed(|| next(&inv, &now_utc(), &Utc));
            assert!(took < BOUND, "{rule} {start}: {took:?}");
        }
    }
}

#[test]
fn a_rule_finer_than_a_day_has_no_next_occurrence() {
    for rule in ["FREQ=SECONDLY", "FREQ=MINUTELY", "FREQ=HOURLY"] {
        let inv = parse(&event(&["DTSTART:20261005T100000", &format!("RRULE:{rule}")])).unwrap();
        assert!(next(&inv, &now_utc(), &Utc).is_none(), "{rule}");
    }
}

#[test]
fn a_vtimezone_that_fires_every_second_costs_nothing() {
    let text = zoned_event(&zone_block("Bomb/Zone", "+0000", Some("RRULE:FREQ=SECONDLY")), "Bomb/Zone", false);
    let (inv, took) = costed(|| parse(&text).unwrap());
    assert!(took < BOUND, "{took:?}");
    assert_eq!((inv.uid.as_str(), inv.start_iso.as_str()), ("u", "2026-11-02T10:00:00"));
}

#[test]
fn a_text_that_is_a_path_to_a_calendar_is_not_opened() {
    let path = std::env::temp_dir().join(format!("mm-calendar-{}.ics", std::process::id()));
    std::fs::write(&path, event(&["DTSTART:20261102T100000"])).unwrap();
    let said = path.to_string_lossy().into_owned();
    let (card, found) = (parse(&said), invite::events(&said, here(&Utc)));
    std::fs::remove_file(&path).unwrap();
    assert!(card.is_none() && found.is_empty());
}

#[test]
fn a_series_is_still_answered_after_decades_of_it() {
    let inv = parse(&event(&["DTSTART;TZID=Europe/Berlin:19900101T100000", "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"]))
        .unwrap();
    assert_eq!(next(&inv, &now_utc(), &Utc).unwrap().0.isoformat(), "2026-11-02T10:00:00+01:00");
}

#[test]
fn a_date_only_until_takes_in_that_day_and_no_later() {
    let inv = parse(&event(&["DTSTART:20261005T100000", "RRULE:FREQ=WEEKLY;UNTIL=20261102"])).unwrap();
    assert_eq!(minutes(&next(&inv, &now_utc(), &Utc).unwrap().0), "2026-11-02T10:00");
    let after = Dt::from_parts(2026, 11, 3, 12, 0, 0, Tz::Fixed(0)).unwrap();
    assert!(next(&inv, &after, &Utc).is_none());
}

// ── more than one event, and what an event may say besides ──────────────────

#[test]
fn every_event_of_a_text_is_read_and_the_card_is_the_first() {
    let two = calendar(&[
        "BEGIN:VEVENT",
        "UID:a",
        "SUMMARY:First",
        "DTSTART:20261110T100000",
        "END:VEVENT",
        "BEGIN:VEVENT",
        "UID:b",
        "SUMMARY:Second",
        "DTSTART:20261111T100000",
        "END:VEVENT",
    ]);
    let summaries: Vec<String> = invite::events(&two, here(&Utc)).into_iter().map(|e| e.summary).collect();
    assert_eq!(summaries, ["First", "Second"]);
    assert_eq!(parse(&two).unwrap().summary, "First");
}

#[test]
fn the_card_of_a_series_and_its_moved_occurrence_is_the_series() {
    let text = calendar(&[
        "BEGIN:VEVENT",
        "UID:a",
        "SUMMARY:Moved",
        "RECURRENCE-ID:20261109T100000",
        "DTSTART:20261110T100000",
        "END:VEVENT",
        "BEGIN:VEVENT",
        "UID:a",
        "SUMMARY:Series",
        "DTSTART:20261005T100000",
        "RRULE:FREQ=WEEKLY",
        "END:VEVENT",
    ]);
    assert_eq!(parse(&text).unwrap().summary, "Series");
}

#[test]
fn a_duration_stands_where_there_is_no_end() {
    assert_eq!(parse(&event(&["DTSTART:20261102T100000", "DURATION:PT45M"])).unwrap().end_iso, "2026-11-02T10:45:00");
}

#[test]
fn an_rdate_is_an_occurrence_and_only_shows_where_there_is_one() {
    let inv = parse(&event(&["DTSTART:20261005T100000", "DTEND:20261005T103000", "RDATE:20261203T150000"])).unwrap();
    assert_eq!(inv.to_json()["rdates"], serde_json::json!(["2026-12-03T15:00:00"]));
    let (start, end) = next(&inv, &now_utc(), &Utc).unwrap();
    assert_eq!((minutes(&start), minutes(&end)), ("2026-12-03T15:00".to_owned(), "2026-12-03T15:30".to_owned()));
    assert!(parse(&event(&["DTSTART:20261005T100000"])).unwrap().to_json().get("rdates").is_none());
}

#[test]
fn no_more_than_so_many_events_of_one_text_are_read() {
    let many: Vec<String> = (0..MAX_EVENTS + 5)
        .map(|i| format!("BEGIN:VEVENT\r\nUID:e{i}\r\nDTSTART:20261110T100000\r\nEND:VEVENT"))
        .collect();
    let text = calendar(&many.iter().map(String::as_str).collect::<Vec<_>>());
    assert_eq!(invite::events(&text, here(&Utc)).len(), MAX_EVENTS);
}

#[test]
fn a_text_past_the_cap_is_no_calendar() {
    let long = event(&["DTSTART:20261102T100000", &format!("DESCRIPTION:{}", "x".repeat(MAX_TEXT))]);
    let (card, took) = costed(|| parse(&long));
    assert!(card.is_none() && took < BOUND, "{took:?}");
}

#[test]
fn a_text_within_the_cap_is_read_within_a_bound() {
    let shapes = [
        vec!["DESCRIPTION:x"; 60_000].join("\r\n"),
        format!("DESCRIPTION:{}", "\\,".repeat(450_000)),
        " x\r\n".repeat(200_000),
        format!("EXDATE:{}", "x,".repeat(450_000)),
        format!("ATTENDEE;{}:z", "X=y;".repeat(200_000)),
    ];
    for shape in shapes {
        let text = event(&["DTSTART:20261102T100000", &shape]);
        let (_, took): (Vec<Invite>, _) = costed(|| invite::events(&text, here(&Utc)));
        assert!(took < 2 * BOUND, "{}: {took:?}", &shape[..20]);
    }
}

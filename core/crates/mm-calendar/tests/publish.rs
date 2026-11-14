//! An event a person confirmed, as a METHOD:PUBLISH file for their own calendar: it invites no one, nothing a
//! stranger's mail said can add a line to it, and a date that won't do is refused in words
//! (tests/unit/calendar/test_publish.py). All fictional.

#![allow(clippy::unwrap_used)]

mod common;

use std::sync::Arc;

use common::Frozen;
use mm_calendar::ports::Message;
use mm_calendar::publish::{self, Asked, EventMessages, NewEvent, Publishing};
use mm_calendar::time::{Dt, Tz};
use mm_kernel::errors::{Failure, Fault, Kind};
use mm_kernel::zone::Utc;
use serde_json::Value;

const ME: &str = "ada@example.test";
const UID: &str = "abc@mailmind.local";
/// 2026-11-02 12:00 UTC.
const STAMP: i64 = 1_793_620_800;

fn lines(text: &str) -> Vec<&str> {
    text.strip_suffix("\r\n").unwrap().split("\r\n").collect()
}

fn unfolded(text: &str) -> String {
    text.replace("\r\n ", "")
}

fn field(text: &str, name: &str) -> Option<String> {
    let text = unfolded(text);
    lines(&text).into_iter().find(|l| l.starts_with(name)).map(str::to_owned)
}

fn event(title: &str, start: &str, end: Option<&str>, place: &str) -> NewEvent {
    NewEvent { title: title.into(), start: start.into(), end: end.map(str::to_owned), place: place.into() }
}

fn design_review() -> NewEvent {
    event("Design review", "2026-11-12T15:00", Some("2026-11-12T16:00"), "Room 2")
}

fn built(event: &NewEvent) -> String {
    publish::build(event, ME, UID, &Dt::new(STAMP, Tz::Fixed(0)), &Utc)
}

#[test]
fn it_is_the_publish_the_python_app_wrote() {
    assert_eq!(
        built(&design_review()),
        [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//MailMind//Suggestion//EN",
            "CALSCALE:GREGORIAN",
            "METHOD:PUBLISH",
            "BEGIN:VEVENT",
            "UID:abc@mailmind.local",
            "DTSTAMP:20261102T120000Z",
            "SEQUENCE:0",
            "SUMMARY:Design review",
            "DTSTART:20261112T150000",
            "DTEND:20261112T160000",
            "LOCATION:Room 2",
            "ORGANIZER:mailto:ada@example.test",
            "END:VEVENT",
            "END:VCALENDAR",
            "",
        ]
        .join("\r\n")
    );
}

#[test]
fn it_invites_no_one_and_is_the_persons_own() {
    let text = built(&design_review());
    assert_eq!(field(&text, "ATTENDEE"), None);
    let text = unfolded(&text);
    let organizers: Vec<&str> = lines(&text).into_iter().filter(|l| l.starts_with("ORGANIZER")).collect();
    assert_eq!(organizers, [format!("ORGANIZER:mailto:{ME}")]);
}

#[test]
fn an_aware_time_goes_to_utc() {
    let text = built(&event("Design review", "2026-11-12T15:00-05:00", Some("2026-11-12T16:30-05:00"), "Room 2"));
    assert_eq!(field(&text, "DTSTART").as_deref(), Some("DTSTART:20261112T200000Z"));
    assert_eq!(field(&text, "DTEND").as_deref(), Some("DTEND:20261112T213000Z"));
}

#[test]
fn a_time_with_no_end_runs_an_hour() {
    assert_eq!(
        field(&built(&event("Design review", "2026-11-12T15:00", None, "")), "DTEND").as_deref(),
        Some("DTEND:20261112T160000")
    );
    assert_eq!(
        field(&built(&event("Design review", "2026-11-12T23:30-05:00", None, "")), "DTEND").as_deref(),
        Some("DTEND:20261113T053000Z")
    );
}

#[test]
fn a_whole_day_ends_the_day_after_as_a_calendar_says_it() {
    let text = built(&event("Design review", "2026-11-12", None, ""));
    assert_eq!(field(&text, "DTSTART").as_deref(), Some("DTSTART;VALUE=DATE:20261112"));
    assert_eq!(field(&text, "DTEND").as_deref(), Some("DTEND;VALUE=DATE:20261113"));
    let days = built(&event("Design review", "2026-12-30", Some("2027-01-02"), ""));
    assert_eq!(field(&days, "DTEND").as_deref(), Some("DTEND;VALUE=DATE:20270103"));
}

#[test]
fn no_place_is_no_location() {
    assert_eq!(field(&built(&event("Design review", "2026-11-12T15:00", None, "")), "LOCATION"), None);
}

#[test]
fn text_is_escaped_as_a_calendar_reads_it() {
    let text = built(&event("Lunch, then; a\\b", "2026-11-12T15:00", None, ""));
    assert_eq!(field(&text, "SUMMARY:").as_deref(), Some("SUMMARY:Lunch\\, then\\; a\\\\b"));
}

#[test]
fn nothing_in_the_text_can_add_a_line() {
    let attacks = [
        "Lunch\r\nATTENDEE:mailto:x@example.test",
        "Lunch\rATTENDEE:mailto:x@example.test",
        "Lunch\nATTENDEE:mailto:x@example.test",
        "Lunch\u{2028}ATTENDEE:mailto:x@example.test",
        "Lunch\u{85}ATTENDEE:mailto:x@example.test",
        "Lunch\r\nEND:VEVENT\r\nBEGIN:VEVENT",
    ];
    for attack in attacks {
        for event in [event(attack, "2026-11-12T15:00", None, ""), event("Lunch", "2026-11-12T15:00", None, attack)] {
            let text = unfolded(&built(&event));
            let lines = lines(&text);
            assert!(!lines.iter().any(|l| l.starts_with("ATTENDEE")), "{attack:?}");
            assert_eq!(lines.iter().filter(|l| **l == "BEGIN:VEVENT").count(), 1, "{attack:?}");
            assert_eq!(lines.iter().filter(|l| **l == "END:VEVENT").count(), 1, "{attack:?}");
            assert!(!text.replace("\r\n", "").contains('\r'), "{attack:?}");
        }
    }
}

#[test]
fn a_long_title_is_folded_on_character_boundaries() {
    let title = "Reunión anual de diseño para el año próximo, en la sala grande del edificio principal ".repeat(2);
    let text = built(&event(&title, "2026-11-12T15:00", None, ""));
    assert!(lines(&text).iter().all(|l| l.len() <= 75));
    assert!(unfolded(&text).contains("Reunión anual"));
}

#[test]
fn the_same_event_has_the_same_uid_so_a_calendar_replaces_it() {
    let first = event("Design review", "2026-11-12T15:00", None, "");
    let uid = publish::uid_for(ME, Some(7), &first);
    // As the Python app made it.
    assert_eq!(uid, "3a388e78461cfd64a1a137392944c400@mailmind.local");
    assert_eq!(
        uid,
        publish::uid_for(ME, Some(7), &event("Design review", "2026-11-12T15:00", Some("2026-11-12T16:00"), "Room"))
    );
    assert_ne!(uid, publish::uid_for(ME, Some(8), &first));
    assert_ne!(uid, publish::uid_for(ME, Some(7), &event("Other", "2026-11-12T15:00", None, "")));
}

// ── what a person confirmed ─────────────────────────────────────────────────

/// The one message an event is confirmed from.
#[derive(Debug)]
struct OneMessage;

impl EventMessages for OneMessage {
    fn message(&self, pk: i64) -> Result<Option<Message>, Fault> {
        Ok((pk == 7).then(|| Message { pk: 7, key: Some(41), account: ME.into() }))
    }
}

fn lunch() -> Asked {
    Asked {
        pk: Some(7),
        title: Some("Lunch".into()),
        start: Some("2026-11-12T12:30".into()),
        end: Some("2026-11-12T13:30".into()),
        place: Some("Cafe".into()),
    }
}

fn confirmed(asked: Asked) -> Result<Value, Failure> {
    Publishing::new(Arc::new(OneMessage), Arc::new(Frozen(STAMP)), Arc::new(Utc)).event(&asked)
}

#[test]
fn what_a_person_confirmed_is_the_file_for_their_own_calendar() {
    let answer = confirmed(lunch()).unwrap();
    assert_eq!((answer["ok"].as_bool(), answer["filename"].as_str()), (Some(true), Some("event.ics")));
    let ics = answer["ics"].as_str().unwrap();
    assert_eq!(field(ics, "METHOD").as_deref(), Some("METHOD:PUBLISH"));
    assert_eq!(field(ics, "ORGANIZER"), Some(format!("ORGANIZER:mailto:{ME}")));
}

#[test]
fn what_wont_do_is_refused_in_words() {
    let wont: [(Asked, &str); 6] = [
        (Asked { title: Some("  ".into()), ..lunch() }, "an event needs a title"),
        (Asked { start: Some(String::new()), ..lunch() }, "that start isn\u{2019}t a date this can use"),
        (Asked { start: Some("tomorrow".into()), ..lunch() }, "that start isn\u{2019}t a date this can use"),
        (Asked { start: Some("2199-01-01T10:00".into()), ..lunch() }, "that start isn\u{2019}t a date this can use"),
        (Asked { end: Some("2026-11-12T11:00".into()), ..lunch() }, "that end doesn\u{2019}t come after the start"),
        (Asked { end: Some("whenever".into()), ..lunch() }, "that end doesn\u{2019}t come after the start"),
    ];
    for (asked, said) in wont {
        match confirmed(asked) {
            Err(Failure::Problem(problem)) => assert_eq!((problem.kind, problem.said.as_str()), (Kind::Refused, said)),
            other => panic!("{said}: {other:?}"),
        }
    }
}

#[test]
fn an_empty_end_is_no_end() {
    let answer = confirmed(Asked { end: Some(String::new()), ..lunch() }).unwrap();
    assert_eq!(field(answer["ics"].as_str().unwrap(), "DTEND").as_deref(), Some("DTEND:20261112T133000"));
}

#[test]
fn a_message_with_no_copy_left_is_not_found() {
    match confirmed(Asked { pk: Some(8), ..lunch() }) {
        Err(Failure::Problem(problem)) => assert_eq!(problem.kind, Kind::NotFound),
        other => panic!("{other:?}"),
    }
}

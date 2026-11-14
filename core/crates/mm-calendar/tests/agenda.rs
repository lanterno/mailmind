//! Today & next (`Agenda`) over invites held in memory: which events make the list, when a repeating one next
//! happens, what a cancellation ends, and which revision counts (tests/unit/calendar/test_upcoming.py and
//! test_cancel_method.py). What the store hands over, which mail and which part, is mm-store's. All fictional.

#![allow(clippy::unwrap_used)]

mod common;

use std::sync::Arc;

use common::*;
use mm_calendar::agenda;
use mm_calendar::ports::InviteRow;
use mm_calendar::time::{DAY, Dt, Tz};
use mm_kernel::zone::{Utc, Zone};
use serde_json::Value;

const ACCT: &str = "ada@example.test";

/// A fixed local "now", Friday 2026-10-30 at noon.
fn now() -> Dt {
    at(2026, 10, 30, 12, 0)
}

fn later(by: i64) -> Dt {
    now().plus(by).unwrap()
}

const HOUR: i64 = 3600;

/// An invite of one event, as the Python tests' `_ics` writes one.
#[derive(Clone)]
struct Ics {
    uid: String,
    start: Dt,
    end: Option<Dt>,
    summary: String,
    method: String,
    status: String,
    all_day: bool,
    extra: Vec<String>,
}

fn ics(uid: &str, start: Dt) -> Ics {
    Ics {
        uid: uid.into(),
        start,
        end: None,
        summary: "Studio sync".into(),
        method: "REQUEST".into(),
        status: String::new(),
        all_day: false,
        extra: Vec::new(),
    }
}

impl Ics {
    fn end(mut self, end: Dt) -> Ics {
        self.end = Some(end);
        self
    }
    fn summary(mut self, summary: &str) -> Ics {
        self.summary = summary.into();
        self
    }
    fn method(mut self, method: &str) -> Ics {
        self.method = method.into();
        self
    }
    fn status(mut self, status: &str) -> Ics {
        self.status = status.into();
        self
    }
    fn all_day(mut self) -> Ics {
        self.all_day = true;
        self
    }
    fn with(mut self, line: &str) -> Ics {
        self.extra.push(line.into());
        self
    }

    fn text(&self) -> String {
        let said = |at: &Dt| if self.all_day { at.basic()[..8].to_owned() } else { at.basic() };
        let value = if self.all_day { ";VALUE=DATE:" } else { ":" };
        let mut lines = vec![
            "BEGIN:VCALENDAR".to_owned(),
            "VERSION:2.0".into(),
            format!("METHOD:{}", self.method),
            "BEGIN:VEVENT".into(),
            format!("UID:{}", self.uid),
            format!("SUMMARY:{}", self.summary),
            "LOCATION:Roof room".into(),
            format!("DTSTART{value}{}", said(&self.start)),
            "ORGANIZER;CN=Ren Takeda:mailto:ren@example.test".into(),
        ];
        if let Some(end) = &self.end {
            lines.push(format!("DTEND{value}{}", said(end)));
        }
        if !self.status.is_empty() {
            lines.push(format!("STATUS:{}", self.status));
        }
        lines.extend(self.extra.iter().cloned());
        lines.extend(["END:VEVENT".into(), "END:VCALENDAR".into(), String::new()]);
        lines.join("\r\n")
    }
}

/// Mail in memory, one message at a time, each with its own copy.
struct Mail {
    stored: Arc<Stored>,
    person: Arc<dyn Zone>,
    count: i64,
}

impl Mail {
    fn new() -> Mail {
        Mail::in_zone(Arc::new(Utc))
    }

    fn in_zone(person: Arc<dyn Zone>) -> Mail {
        Mail { stored: Arc::new(Stored::default()), person, count: 0 }
    }

    /// A message holding `text`, which came in `days_ago`, in `folder` of `account`. Its key.
    fn msg_in(&mut self, text: &str, days_ago: f64, folder: &str, account: &str) -> i64 {
        self.count += 1;
        let n = self.count;
        let row = InviteRow {
            pk: n,
            key: Some(100 + n),
            uid: Some(n),
            account: account.into(),
            folder: folder.into(),
            thread_id: Some(format!("t{n}")),
        };
        let ts = moment(&now(), &*self.person) - (days_ago * DAY as f64) as i64;
        self.stored.add(row, ts, text);
        100 + n
    }

    fn msg(&mut self, text: &str, days_ago: f64) -> i64 {
        self.msg_in(text, days_ago, "INBOX", ACCT)
    }

    fn up(&self) -> Vec<Value> {
        upcoming(self.stored.clone(), &now(), 60, self.person.clone())
    }

    fn listed(&self) -> Vec<(i64, String)> {
        self.up().iter().map(|x| (x["key"].as_i64().unwrap(), x["start"].as_str().unwrap().to_owned())).collect()
    }

    fn said(&self) -> Vec<(String, String)> {
        self.up()
            .iter()
            .map(|x| (x["summary"].as_str().unwrap().to_owned(), x["start"].as_str().unwrap().to_owned()))
            .collect()
    }

    fn keys(&self) -> Vec<i64> {
        self.up().iter().map(|x| x["key"].as_i64().unwrap()).collect()
    }
}

fn pairs(items: &[(&str, &str)]) -> Vec<(String, String)> {
    items.iter().map(|(a, b)| ((*a).to_owned(), (*b).to_owned())).collect()
}

// ── what makes the list ─────────────────────────────────────────────────────

#[test]
fn future_events_are_listed_soonest_first_at_local_times() {
    let mut mail = Mail::new();
    let hike = mail.msg(&ics("b", later(2 * DAY)).summary("Hike").text(), 1.0);
    let soon = mail.msg(&ics("a", later(3 * HOUR)).end(later(3 * HOUR + 1800)).text(), 1.0);
    let items = mail.up();
    assert_eq!(mail.keys(), [soon, hike]);
    let a = &items[0];
    assert_eq!((a["summary"].as_str(), a["location"].as_str()), (Some("Studio sync"), Some("Roof room")));
    assert_eq!((a["start"].as_str(), a["end"].as_str()), (Some("2026-10-30T15:00"), Some("2026-10-30T15:30")));
    assert_eq!(
        (a["organizer"].as_str(), a["invite"].as_bool(), a["account"].as_str()),
        (Some("Ren Takeda"), Some(true), Some(ACCT))
    );
}

#[test]
fn an_event_that_has_ended_drops_out_but_one_in_progress_stays() {
    let mut mail = Mail::new();
    mail.msg(&ics("done", later(-3 * HOUR)).end(later(-2 * HOUR)).text(), 1.0);
    let on = mail.msg(&ics("now", later(-1800)).end(later(1800)).text(), 1.0);
    assert_eq!(mail.keys(), [on]);
}

#[test]
fn utc_times_come_back_as_local_wall_clock() {
    for zone in MACHINE_ZONES {
        let mut mail = Mail::in_zone(person(zone));
        let utc = Dt::new(moment(&later(DAY), &*person(zone)), Tz::Fixed(0));
        mail.msg(
            &ics("z", later(DAY))
                .text()
                .replace(&format!("DTSTART:{}", later(DAY).basic()), &format!("DTSTART:{}Z", utc.basic())),
            1.0,
        );
        assert_eq!(mail.up()[0]["start"], "2026-10-31T12:00", "{zone}");
    }
}

#[test]
fn an_all_day_event_today_stays_until_midnight() {
    let mut mail = Mail::new();
    let today = at(2026, 10, 30, 0, 0);
    let key = mail.msg(&ics("d", today).all_day().text(), 1.0);
    let items = mail.up();
    assert_eq!(mail.keys(), [key]);
    assert_eq!((items[0]["all_day"].as_bool(), items[0]["end"].as_str()), (Some(true), Some("2026-10-31T00:00")));
}

#[test]
fn an_update_replaces_the_original() {
    let mut mail = Mail::new();
    mail.msg(&ics("u", later(DAY)).summary("Old time").text(), 3.0);
    let new = mail.msg(&ics("u", later(2 * DAY)).summary("New time").text(), 1.0);
    assert_eq!(mail.keys(), [new]);
    assert_eq!(mail.up()[0]["summary"], "New time");
}

#[test]
fn a_cancellation_removes_the_event() {
    let mut mail = Mail::new();
    mail.msg(&ics("c", later(DAY)).text(), 3.0);
    mail.msg(&ics("c", later(DAY)).method("CANCEL").status("CANCELLED").text(), 1.0);
    mail.msg(&ics("s", later(DAY)).status("CANCELLED").text(), 1.0);
    assert!(mail.up().is_empty());
}

#[test]
fn trash_junk_and_old_mail_are_not_read() {
    let mut mail = Mail::new();
    mail.msg_in(&ics("t", later(DAY)).text(), 1.0, "Trash", ACCT);
    mail.msg_in(&ics("j", later(DAY)).text(), 1.0, "Spam", ACCT);
    mail.msg(&ics("o", later(DAY)).text(), 90.0);
    assert!(mail.up().is_empty());
}

#[test]
fn every_account_and_a_limit() {
    let mut mail = Mail::new();
    for i in 0..7 {
        mail.msg(&ics(&format!("m{i}"), later((i + 1) * DAY)).text(), 1.0);
    }
    mail.msg_in(&ics("x", later(HOUR)).text(), 1.0, "INBOX", "other@example.test");
    let items = mail.up();
    assert_eq!((items.len(), items[0]["account"].as_str()), (5, Some("other@example.test")));
}

#[test]
fn a_broken_calendar_part_is_skipped() {
    let mut mail = Mail::new();
    mail.msg("not a calendar at all", 1.0);
    let ok = mail.msg(&ics("k", later(DAY)).text(), 1.0);
    assert_eq!(mail.keys(), [ok]);
}

// ── a series ────────────────────────────────────────────────────────────────

/// A weekly series whose first meeting was two weeks ago, at now's time of day.
fn weekly(uid: &str, summary: &str, extra: &[&str]) -> String {
    let first = later(-14 * DAY);
    let mut event = ics(uid, first.clone()).end(first.plus(1800).unwrap()).summary(summary).with("RRULE:FREQ=WEEKLY");
    for line in extra {
        event = event.with(line);
    }
    event.text()
}

/// One occurrence of series "s", moved to `start`: its RECURRENCE-ID the start it had.
fn one(start: Dt, rid: &Dt) -> Ics {
    ics("s", start.clone()).end(start.plus(1800).unwrap()).with(&format!("RECURRENCE-ID:{}", rid.basic()))
}

#[test]
fn a_series_is_listed_at_its_next_occurrence() {
    let mut mail = Mail::new();
    let key = mail.msg(&weekly("s", "Standup", &[]), 20.0);
    mail.msg(&weekly("p", "Yoga", &[&format!("EXDATE:{}", now().basic())]), 20.0);
    let items = mail.up();
    // Today's is in progress.
    assert_eq!(mail.said(), pairs(&[("Standup", "2026-10-30T12:00"), ("Yoga", "2026-11-06T12:00")]));
    assert_eq!((items[0]["key"].as_i64(), items[0]["recurrence"].as_str()), (Some(key), Some("Every week")));
}

#[test]
fn moving_or_cancelling_one_occurrence_leaves_the_series() {
    let mut mail = Mail::new();
    mail.msg(&weekly("s", "Standup", &[]), 20.0);
    mail.msg(&one(later(DAY), &now()).summary("Standup (moved)").text(), 2.0);
    mail.msg(&one(later(7 * DAY), &later(7 * DAY)).method("CANCEL").status("CANCELLED").text(), 1.0);
    assert_eq!(mail.said(), pairs(&[("Standup (moved)", "2026-10-31T12:00"), ("Standup", "2026-11-13T12:00")]));
}

fn revised(uid: &str, start: Dt, sequence: i64, summary: &str) -> Ics {
    ics(uid, start).summary(summary).with(&format!("SEQUENCE:{sequence}"))
}

#[test]
fn a_later_revision_counts_though_a_copy_of_an_older_one_came_after_it() {
    let mut mail = Mail::new();
    let wins = mail.msg(&revised("r", later(3 * DAY), 2, "Moved here").text(), 3.0);
    mail.msg(&revised("r", later(DAY), 1, "Where it was").text(), 1.0);
    assert_eq!(mail.said(), pairs(&[("Moved here", "2026-11-02T12:00")]));
    assert_eq!(mail.keys(), [wins]);
}

#[test]
fn the_same_revision_goes_by_the_newest_message() {
    let mut mail = Mail::new();
    mail.msg(&revised("r", later(3 * DAY), 2, "Older copy").text(), 3.0);
    mail.msg(&revised("r", later(DAY), 2, "Newer copy").text(), 1.0);
    assert_eq!(mail.said(), pairs(&[("Newer copy", "2026-10-31T12:00")]));
}

#[test]
fn a_stale_invite_doesnt_bring_back_what_a_later_revision_cancelled() {
    let mut mail = Mail::new();
    mail.msg(&revised("r", later(3 * DAY), 3, "Studio sync").method("CANCEL").status("CANCELLED").text(), 3.0);
    mail.msg(&revised("r", later(3 * DAY), 1, "Studio sync").text(), 1.0);
    assert!(mail.up().is_empty());
}

#[test]
fn a_revision_is_weighed_within_one_occurrence() {
    let mut mail = Mail::new();
    mail.msg(&weekly("s", "Standup", &[]), 20.0);
    mail.msg(&one(later(2 * DAY), &now()).summary("Revised").with("SEQUENCE:2").text(), 4.0);
    mail.msg(&one(later(DAY), &now()).summary("First go").with("SEQUENCE:1").text(), 3.0);
    assert_eq!(mail.said(), pairs(&[("Revised", "2026-11-01T12:00"), ("Standup", "2026-11-06T12:00")]));
}

#[test]
fn a_stored_invite_from_outlook_shows_at_its_own_hour_in_your_zone() {
    // 2026-10-31 12:00 UTC is 13:00 in Berlin, its summer time over.
    let outlook = ics("o", later(DAY))
        .text()
        .replace(&format!("DTSTART:{}", later(DAY).basic()), "DTSTART;TZID=W. Europe Standard Time:20261031T130000");
    for (zone, start) in
        [("UTC", "2026-10-31T12:00"), ("Asia/Tokyo", "2026-10-31T21:00"), ("America/New_York", "2026-10-31T08:00")]
    {
        let mut mail = Mail::in_zone(person(zone));
        mail.msg(&outlook, 1.0);
        assert_eq!(mail.up()[0]["start"], start, "{zone}");
    }
}

#[test]
fn cancelling_the_series_drops_its_moved_occurrences_too() {
    let mut mail = Mail::new();
    mail.msg(&weekly("s", "Standup", &[]), 20.0);
    mail.msg(&one(later(DAY), &now()).text(), 2.0);
    mail.msg(&ics("s", later(-14 * DAY)).method("CANCEL").status("CANCELLED").text(), 1.0);
    assert!(mail.up().is_empty());
}

#[test]
fn a_rule_it_cannot_expand_shows_its_first_date_while_ahead() {
    let mut mail = Mail::new();
    mail.msg(&ics("a", later(3 * DAY)).with("RRULE:FREQ=HOURLY").text(), 1.0);
    mail.msg(&ics("b", later(-3 * DAY)).with("RRULE:FREQ=HOURLY").text(), 1.0);
    let starts: Vec<Value> = mail.up().iter().map(|x| x["start"].clone()).collect();
    assert_eq!(starts, ["2026-11-02T12:00"]);
}

#[test]
fn rules_that_never_come_to_a_date_cant_hold_a_list_for_long() {
    // Two hundred of them in one message: each stops at its own budget, and the list at its own.
    let mut lines = vec!["BEGIN:VCALENDAR".to_owned(), "METHOD:REQUEST".into()];
    for i in 0..200 {
        lines.extend([
            "BEGIN:VEVENT".to_owned(),
            format!("UID:s{i}"),
            "SUMMARY:Series".into(),
            format!("DTSTART:{}", later(-3 * DAY).basic()),
            "RRULE:FREQ=DAILY;BYMONTH=2;BYMONTHDAY=30".into(),
            "END:VEVENT".into(),
        ]);
    }
    lines.extend(["END:VCALENDAR".into(), String::new()]);
    let mut mail = Mail::new();
    mail.msg(&lines.join("\r\n"), 1.0);
    let ok = mail.msg(&ics("k", later(DAY)).text(), 1.0);
    let began = std::time::Instant::now();
    assert!(mail.keys().contains(&ok));
    assert!(began.elapsed().as_secs() < 2, "{:?}", began.elapsed());
}

#[test]
fn a_time_at_the_edge_of_a_datetime_costs_only_that_event_its_place() {
    let edges: [&[&str]; 8] = [
        &["DTSTART:00010101T000000Z"],
        &["DTSTART:99991231T235959Z"],
        &["DTSTART:00010101T000000Z", "DTEND:99990101T000000Z"],
        &["DTSTART:20261001T100000", "DTEND:99991231T235959"],
        &["DTSTART:99991231T100000", "DURATION:P7D"],
        &["DTSTART:20261001T100000", "RRULE:FREQ=DAILY;UNTIL=99991231T235959Z"],
        &["DTSTART:20261001T100000", "RRULE:FREQ=YEARLY;INTERVAL=9000"],
        &["DTSTART;TZID=Asia/Tokyo:00010101T000000", "RRULE:FREQ=YEARLY"],
    ];
    for zone in MACHINE_ZONES {
        for times in edges {
            let mut mail = Mail::in_zone(person(zone));
            let mut lines = vec!["BEGIN:VCALENDAR", "BEGIN:VEVENT", "UID:w", "SUMMARY:Odd"];
            lines.extend(times);
            lines.extend(["END:VEVENT", "END:VCALENDAR", ""]);
            mail.msg(&lines.join("\r\n"), 2.0);
            let ok = mail.msg(&ics("k", later(DAY)).text(), 1.0);
            assert!(mail.keys().contains(&ok), "{zone} {times:?}");
        }
    }
}

// ── a cancellation that says so only in its METHOD ──────────────────────────

/// An event of half an hour, as test_cancel_method.py's `event` writes one.
fn event(uid: &str, start: Dt, method: &str) -> Ics {
    let end = start.plus(1800).unwrap();
    ics(uid, start).end(end).method(method)
}

fn tomorrow() -> Dt {
    later(DAY)
}

#[test]
fn the_reader_keeps_a_method_with_no_status() {
    let inv = parse(&event("c", tomorrow(), "CANCEL").text()).unwrap();
    assert_eq!((inv.method.as_str(), inv.status.as_str()), ("CANCEL", ""));
    assert!(agenda::dead(&inv));
}

#[test]
fn a_method_alone_ends_the_event_in_any_case() {
    for said in ["CANCEL", "cancel", "Cancel"] {
        let mut mail = Mail::new();
        mail.msg(&event("c", tomorrow(), "REQUEST").text(), 3.0);
        mail.msg(&event("c", tomorrow(), said).text(), 1.0);
        assert!(mail.listed().is_empty(), "{said}");
    }
}

#[test]
fn the_cancellation_is_no_event_of_its_own() {
    let mut mail = Mail::new();
    mail.msg(&event("c", tomorrow(), "CANCEL").text(), 1.0);
    assert!(mail.listed().is_empty());
}

#[test]
fn a_method_ends_an_event_whose_status_still_says_confirmed() {
    let mut mail = Mail::new();
    mail.msg(&event("c", tomorrow(), "REQUEST").text(), 3.0);
    mail.msg(&event("c", tomorrow(), "CANCEL").status("CONFIRMED").text(), 1.0);
    assert!(mail.listed().is_empty());
}

#[test]
fn another_events_cancellation_leaves_this_one() {
    let mut mail = Mail::new();
    let kept = mail.msg(&event("keep", tomorrow(), "REQUEST").text(), 3.0);
    mail.msg(&event("other", tomorrow(), "CANCEL").text(), 1.0);
    assert_eq!(mail.listed(), [(kept, "2026-10-31T12:00".to_owned())]);
}

fn a_weekly() -> String {
    event("s", later(-14 * DAY), "REQUEST").with("RRULE:FREQ=WEEKLY").text()
}

#[test]
fn a_method_alone_ends_a_series() {
    let mut mail = Mail::new();
    mail.msg(&a_weekly(), 20.0);
    assert_eq!(mail.listed().len(), 1);
    mail.msg(&event("s", later(-14 * DAY), "CANCEL").text(), 1.0);
    assert!(mail.listed().is_empty());
}

#[test]
fn a_method_alone_ends_a_series_with_the_occurrences_it_moved() {
    let mut mail = Mail::new();
    mail.msg(&a_weekly(), 20.0);
    mail.msg(&event("s", tomorrow(), "REQUEST").with(&format!("RECURRENCE-ID:{}", now().basic())).text(), 2.0);
    assert_eq!(mail.listed().len(), 2);
    mail.msg(&event("s", later(-14 * DAY), "CANCEL").text(), 1.0);
    assert!(mail.listed().is_empty());
}

#[test]
fn a_method_alone_ends_one_occurrence_and_leaves_the_series() {
    let mut mail = Mail::new();
    let series = mail.msg(&a_weekly(), 20.0);
    assert_eq!(mail.listed(), [(series, "2026-10-30T12:00".to_owned())]);
    mail.msg(&event("s", now(), "CANCEL").with(&format!("RECURRENCE-ID:{}", now().basic())).text(), 1.0);
    assert_eq!(mail.listed(), [(series, "2026-11-06T12:00".to_owned())]);
}

// ── when a series next happens ──────────────────────────────────────────────

/// The Sunday after Berlin leaves summer time, at noon there.
fn sun() -> Dt {
    zoned(2026, 11, 1, 12, 0, "Europe/Berlin")
}

fn sat() -> Dt {
    zoned(2026, 10, 31, 12, 0, "Europe/Berlin")
}

/// The next occurrence of a Monday 10:00-10:30 Berlin event that first ran on Oct 5, to the minute.
fn next_of(rule: &str, extra: &str, start: &str, end: &str, after: &Dt) -> Option<(String, String)> {
    let tz = if start.contains('T') { ";TZID=Europe/Berlin:" } else { ";VALUE=DATE:" };
    let mut lines = vec![
        "BEGIN:VCALENDAR".to_owned(),
        "BEGIN:VEVENT".into(),
        "UID:r".into(),
        "SUMMARY:Standup".into(),
        format!("DTSTART{tz}{start}"),
        format!("DTEND{tz}{end}"),
    ];
    if !rule.is_empty() {
        lines.push(format!("RRULE:{rule}"));
    }
    if !extra.is_empty() {
        lines.push(extra.into());
    }
    lines.extend(["END:VEVENT".into(), "END:VCALENDAR".into(), String::new()]);
    let inv = parse(&lines.join("\r\n")).unwrap();
    next(&inv, after, &Utc).map(|(s, e)| (minutes(&s), minutes(&e)))
}

fn next_start(rule: &str, extra: &str, after: &Dt) -> Option<String> {
    next_of(rule, extra, "20261005T100000", "20261005T103000", after).map(|(s, _)| s)
}

#[test]
fn a_weekly_event_keeps_its_wall_clock_time_and_length_across_daylight_saving() {
    assert_eq!(
        next_of("FREQ=WEEKLY", "", "20261005T100000", "20261005T103000", &sun()),
        Some(("2026-11-02T10:00+01:00".into(), "2026-11-02T10:30+01:00".into()))
    );
    for (rule, start) in [
        ("FREQ=WEEKLY;INTERVAL=2", "2026-11-02T10:00+01:00"),
        ("FREQ=WEEKLY;INTERVAL=3", "2026-11-16T10:00+01:00"),
        ("FREQ=WEEKLY;BYDAY=WE,MO", "2026-11-02T10:00+01:00"),
    ] {
        assert_eq!(next_start(rule, "", &sun()).as_deref(), Some(start), "{rule}");
    }
}

#[test]
fn count_until_and_exdate_end_or_skip_occurrences() {
    for (rule, extra, start) in [
        ("FREQ=WEEKLY;COUNT=5", "", "2026-11-02T10:00+01:00"),
        ("FREQ=WEEKLY;UNTIL=20261102T090000Z", "", "2026-11-02T10:00+01:00"),
        ("FREQ=WEEKLY", "EXDATE;TZID=Europe/Berlin:20261102T100000", "2026-11-09T10:00+01:00"),
    ] {
        assert_eq!(next_start(rule, extra, &sun()).as_deref(), Some(start), "{rule}");
    }
    for rule in ["FREQ=WEEKLY;COUNT=4", "FREQ=WEEKLY;UNTIL=20261102T085959Z"] {
        assert_eq!(next_start(rule, "", &sun()), None, "{rule}");
    }
}

#[test]
fn daily_monthly_and_yearly_rules() {
    for (rule, after, start) in [
        ("FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR", sat(), "2026-11-02T10:00+01:00"),
        ("FREQ=MONTHLY", sun(), "2026-11-05T10:00+01:00"),
        ("FREQ=MONTHLY;BYDAY=1TU", sun(), "2026-11-03T10:00+01:00"),
        ("FREQ=MONTHLY;BYDAY=-1FR", sun(), "2026-11-27T10:00+01:00"),
        ("FREQ=MONTHLY;BYMONTHDAY=31", sun(), "2026-12-31T10:00+01:00"),
        ("FREQ=YEARLY", sun(), "2027-10-05T10:00+02:00"),
    ] {
        assert_eq!(next_start(rule, "", &after).as_deref(), Some(start), "{rule}");
    }
}

#[test]
fn an_occurrence_in_progress_is_still_next() {
    let during = zoned(2026, 11, 2, 10, 15, "Europe/Berlin");
    assert_eq!(next_start("FREQ=DAILY", "", &during).as_deref(), Some("2026-11-02T10:00+01:00"));
}

#[test]
fn an_all_day_series_and_a_floating_one() {
    assert_eq!(
        next_of("FREQ=WEEKLY", "", "20261005", "20261006", &at(2026, 11, 2, 18, 0)),
        Some(("2026-11-02T00:00".into(), "2026-11-03T00:00".into()))
    );
    let inv = parse(
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:f\r\nDTSTART:20261005T100000\r\nRRULE:FREQ=WEEKLY\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n",
    )
    .unwrap();
    assert_eq!(minutes(&next(&inv, &at(2026, 11, 1, 12, 0), &Utc).unwrap().0), "2026-11-02T10:00");
}

#[test]
fn a_rule_it_cannot_read_gives_none_not_a_wrong_date() {
    // The last is past year 9999.
    for rule in ["FREQ=WEEKLY;BYDAY=XX", "FREQ=HOURLY", "FREQ=YEARLY;INTERVAL=9000"] {
        assert_eq!(next_start(rule, "", &sun()), None, "{rule}");
    }
}

#[test]
fn bysetpos_picks_the_day_of_the_month() {
    assert_eq!(
        next_of("FREQ=MONTHLY;BYDAY=MO,TU;BYSETPOS=1", "", "20261005T100000", "20261005T103000", &sun()),
        Some(("2026-11-02T10:00+01:00".into(), "2026-11-02T10:30+01:00".into()))
    );
}

#[test]
fn a_one_off_event_is_its_own_only_occurrence() {
    // Oct 5 is over.
    assert_eq!(next_start("", "", &sun()), None);
    let on_nov_2 = next_of("", "", "20261102T100000", "20261102T103000", &sun());
    assert_eq!(on_nov_2.map(|(s, _)| s).as_deref(), Some("2026-11-02T10:00+01:00"));
}

#[test]
fn an_end_that_floats_where_its_start_doesnt_counts_by_its_wall_clock() {
    let said = [
        ("DTSTART;TZID=Europe/Berlin:20261102T100000", "DTEND;TZID=Nowhere/Land:20261102T103000"),
        ("DTSTART;TZID=Nowhere/Land:20261102T100000", "DTEND;TZID=Europe/Berlin:20261102T103000"),
        ("DTSTART:20261102T100000Z", "DTEND:20261102T103000"),
        ("DTSTART:20261102T100000", "DTEND:20261102T103000Z"),
    ];
    let after = Dt::from_parts(2026, 11, 2, 8, 0, 0, Tz::Fixed(0)).unwrap();
    for (start, end) in said {
        let text =
            ["BEGIN:VCALENDAR", "BEGIN:VEVENT", "UID:w", "SUMMARY:Odd", start, end, "END:VEVENT", "END:VCALENDAR", ""]
                .join("\r\n");
        let (s, e) = next(&parse(&text).unwrap(), &after, &Utc).unwrap();
        assert_eq!(e.minus(&s), 1800, "{start} {end}");
    }
}

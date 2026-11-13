//! Attention's rules with no database: when a snooze wakes, how a person's task reads, and what a follow-up becomes.

use mm_attention::followups::{self, FollowUp, dismissed, pinned, unpinning_ends, waiting_item};
use mm_attention::ports::WaitingRow;
use mm_attention::snoozing::{Until, in_window, wake_time};
use mm_attention::tasks::{one_line, read_date};
use mm_kernel::seams::Moment;
use mm_kernel::when::days_from_civil;
use mm_kernel::zone::{Rule, Utc};

fn at(y: i64, mo: u32, d: u32, h: i64, mi: i64) -> i64 {
    days_from_civil(y, mo, d) * 86_400 + h * 3600 + mi * 60
}

fn moment(seconds: i64) -> Moment {
    Moment::from_micros(seconds * 1_000_000)
}

fn berlin() -> Rule {
    Rule::parse("CET-1CEST,M3.5.0,M10.5.0/3").expect("a rule")
}

// ── snooze ───────────────────────────────────────────────────────────────

#[test]
fn presets_from_monday_noon() {
    let now = moment(at(2026, 11, 2, 12, 0));
    let woke = |preset| wake_time(Some(preset), None, now, &Utc);
    assert_eq!(woke("later_today"), Some(at(2026, 11, 2, 15, 0)));
    assert_eq!(woke("tomorrow"), Some(at(2026, 11, 3, 8, 0)));
    assert_eq!(woke("weekend"), Some(at(2026, 11, 7, 8, 0)));
    assert_eq!(woke("next_week"), Some(at(2026, 11, 9, 8, 0)));
    assert_eq!(woke("someday"), None);
}

#[test]
fn later_today_past_midnight_is_tomorrow_morning() {
    let now = moment(at(2026, 11, 2, 22, 30));
    assert_eq!(wake_time(Some("later_today"), None, now, &Utc), Some(at(2026, 11, 3, 8, 0)));
}

#[test]
fn on_a_saturday_the_weekend_is_the_next_one() {
    let now = moment(at(2026, 11, 7, 10, 0));
    assert_eq!(wake_time(Some("weekend"), None, now, &Utc), Some(at(2026, 11, 14, 8, 0)));
    assert_eq!(wake_time(Some("next_week"), None, now, &Utc), Some(at(2026, 11, 9, 8, 0)));
}

#[test]
fn tomorrow_at_eight_is_eight_on_the_wall_after_the_clocks_go_back() {
    // Saturday 24 October 2026, noon in Berlin (summer time); the clocks go back that night.
    let now = moment(at(2026, 10, 24, 10, 0));
    let zone = berlin();
    assert_eq!(zone.offset_at(at(2026, 10, 25, 7, 0)), 3600);
    assert_eq!(wake_time(Some("tomorrow"), None, now, &zone), Some(at(2026, 10, 25, 7, 0)));
}

#[test]
fn a_time_picked_lies_ahead_and_within_a_year() {
    let now = at(2026, 11, 2, 12, 0);
    let wake = |until: Until| wake_time(None, Some(&until), moment(now), &Utc);
    assert_eq!(wake(Until::Seconds(now + 3600)), Some(now + 3600));
    assert_eq!(wake(Until::Seconds(now)), None);
    assert_eq!(wake(Until::Seconds(now + 366 * 86_400)), Some(now + 366 * 86_400));
    assert_eq!(wake(Until::Seconds(now + 366 * 86_400 + 1)), None);
    assert!(in_window(now + 1, now as f64) && !in_window(now, now as f64));
}

#[test]
fn a_time_reads_as_python_reads_it() {
    let now = at(2026, 11, 2, 12, 0);
    let wake = |text: &str| wake_time(None, Some(&Until::Text(text.to_owned())), moment(now), &Utc);
    assert_eq!(wake("2026-11-03T09:30:00+00:00"), Some(at(2026, 11, 3, 9, 30)));
    assert_eq!(wake("2026-11-03T09:30:00+01:00"), Some(at(2026, 11, 3, 8, 30)));
    assert_eq!(wake(&format!(" {} ", now + 60)), Some(now + 60));
    assert_eq!(wake("1_793_692_800"), Some(1_793_692_800));
    assert_eq!(wake("soon"), None);
    assert_eq!(wake("2027-12-01T00:00:00+00:00"), None);
    assert_eq!(wake("99999999999999999999999"), None);
    assert_eq!(wake_time(None, None, moment(now), &Utc), None);
}

#[test]
fn a_time_with_no_zone_is_the_persons_wall_clock() {
    let now = at(2026, 11, 2, 12, 0);
    let until = Until::Text("2026-11-03T09:30:00".to_owned());
    assert_eq!(wake_time(None, Some(&until), moment(now), &berlin()), Some(at(2026, 11, 3, 8, 30)));
}

// ── tasks ────────────────────────────────────────────────────────────────

#[test]
fn a_title_is_one_line_within_its_cap() {
    assert_eq!(one_line(Some("  Send\nthe\u{2028}draft \u{7}  back "), 200), "Send the draft back");
    assert_eq!(one_line(Some("abcdef"), 3), "abc");
    assert_eq!(one_line(None, 200), "");
}

#[test]
fn a_due_date_is_a_day_within_its_window() {
    let today = days_from_civil(2026, 11, 2);
    assert_eq!(read_date(Some(" 2026-11-05 "), today).as_deref(), Some("2026-11-05"));
    assert_eq!(read_date(Some("2026-11-05T09:30+01:00"), today).as_deref(), Some("2026-11-05"));
    assert_eq!(read_date(Some("2026-11-05 09:30:15.25"), today).as_deref(), Some("2026-11-05"));
    assert_eq!(read_date(Some("2026-02-30"), today), None);
    assert_eq!(read_date(Some("2026-11-05T09:30:00.1234567"), today), None);
    assert_eq!(read_date(Some("next friday"), today), None);
    // A year and a day behind, five years and five days ahead.
    assert_eq!(read_date(Some("2025-10-31"), today), None);
    assert_eq!(read_date(Some("2025-11-01"), today).as_deref(), Some("2025-11-01"));
    assert_eq!(read_date(Some("2031-11-06"), today).as_deref(), Some("2031-11-06"));
    assert_eq!(read_date(Some("2031-11-07"), today), None);
}

// ── follow-ups ───────────────────────────────────────────────────────────

fn sent() -> FollowUp {
    FollowUp {
        kind: followups::SENT.to_owned(),
        state: followups::DUE.to_owned(),
        first_sent_ts: Some(100),
        last_msg_ts: Some(100),
        due_ts: Some(400),
        created_at: Some("then".to_owned()),
        resolved_at: None,
    }
}

#[test]
fn a_pin_supersedes_a_nudge_and_keeps_when_it_was_sent() {
    let pin = pinned(Some(sent()), Some(500), "now");
    assert_eq!((pin.kind.as_str(), pin.state.as_str()), (followups::REPLY_LATER, followups::DUE));
    assert_eq!((pin.first_sent_ts, pin.last_msg_ts, pin.due_ts), (Some(100), Some(500), Some(400)));
    assert_eq!(pin.created_at.as_deref(), Some("now"));
    assert_eq!(pinned(None, None, "now").first_sent_ts, None);
}

#[test]
fn a_dismissal_stays_and_unpinning_ends_only_a_pin() {
    let gone = dismissed(Some(sent()), "now").expect("a row");
    assert_eq!((gone.state.as_str(), gone.resolved_at.as_deref()), (followups::DISMISSED, Some("now")));
    assert_eq!(dismissed(None, "now"), None);
    assert!(!unpinning_ends(Some(&sent())));
    assert!(unpinning_ends(Some(&pinned(None, None, "now"))));
    assert!(!unpinning_ends(None));
}

#[test]
fn a_sent_thread_waits_on_its_first_recipient() {
    let now = at(2026, 11, 2, 12, 0);
    let row = WaitingRow {
        key: Some(7),
        folder: "Sent".to_owned(),
        uid: Some(3),
        from_name: Some(String::new()),
        from_addr: Some("ada@example.test".to_owned()),
        subject: None,
        date_iso: Some("2026-10-30T12:00:00+00:00".to_owned()),
        snippet: None,
        seen: true,
        flagged: false,
        has_attach: false,
        is_junk: false,
        bucket: None,
        thread_id: Some("t".to_owned()),
        ts: Some(now - 3 * 86_400),
        thread_count: 2,
        account: "ada@example.test".to_owned(),
        kind: followups::SENT.to_owned(),
        first_sent: Some(now - 3 * 86_400),
        recipients: Some(" lin@example.test , bea@example.test".to_owned()),
    };
    let item = waiting_item(&row, moment(now));
    assert_eq!(item["waiting_label"], "Sent 3d ago · no reply");
    assert_eq!(item["to"], "lin@example.test");
    assert_eq!(item["from_name"], "ada@example.test");
    assert_eq!(item["subject"], "(no subject)");
    assert_eq!(item["when"], "3d ago");
}

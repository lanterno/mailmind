//! How a time is said: `mailmind.kernel.when` at the suites' frozen time, and how long ago in the person's own days
//! on either side of their midnight, in zones east and west of UTC (the Python app's tests/unit/kernel/test_when.py).

use mm_kernel::ago::{ago, from_iso, rel_time, rel_time_future};
use mm_kernel::relative;
use mm_kernel::seams::Moment;
use mm_kernel::when::days_from_civil;
use mm_kernel::zone::{Rule, Utc, Zone};

// conftest.NOW, 2026-11-02 12:00 UTC.
const NOW: Moment = Moment::from_micros(1_793_620_800_000_000);
const SECS: i64 = 1_793_620_800;

#[test]
fn a_stored_time_is_said_as_the_page_says_it() {
    let said = |iso| rel_time(Some(iso), NOW, &Utc);
    assert_eq!(said("2026-11-02T11:59:00+00:00"), "just now");
    assert_eq!(said("2026-11-02T11:30:00+00:00"), "30m ago", "within the hour, minutes: not just now");
    assert_eq!(said("2026-11-02T03:00:00+00:00"), "9h ago");
    assert_eq!(said("2026-11-01T09:00:00+00:00"), "yesterday");
    assert_eq!(said("2026-10-27T09:14:00+00:00"), "6d ago");
    assert_eq!(said("2026-10-14T09:14:00+00:00"), "2w ago");
    // Past a month, the date as it was written, in its own offset.
    assert_eq!(said("2026-09-01T23:30:00-02:00"), "Sep 01");
    assert_eq!(said("2026-11-03T12:00:00+00:00"), "Nov 03", "a day still to come is its date");
    assert_eq!(said("not a date at all"), "not a date");
    assert_eq!(rel_time(Some(""), NOW, &Utc), "");
    assert_eq!(rel_time(None, NOW, &Utc), "");
}

/// The zones the midnight cases run in, each with the offset it has in early November 2026, as an ISO time writes
/// it: Auckland in its summer, Kolkata, Paris, UTC and Los Angeles back on standard time.
fn zones() -> Vec<(&'static str, Box<dyn Zone>, &'static str)> {
    let rule = |text| Box::new(Rule::parse(text).expect("a rule")) as Box<dyn Zone>;
    vec![
        ("Auckland", rule("NZST-12NZDT,M9.5.0,M4.1.0/3"), "+13:00"),
        ("Kolkata", rule("<+0530>-5:30"), "+05:30"),
        ("Paris", rule("CET-1CEST,M3.5.0,M10.5.0/3"), "+01:00"),
        ("UTC", Box::new(Utc), "+00:00"),
        ("Los Angeles", rule("PST8PDT,M3.2.0,M11.1.0"), "-08:00"),
    ]
}

/// Twenty past midnight on Tuesday 3 November 2026 in a zone of `offset` seconds east of UTC.
fn twenty_past_midnight(offset: i64) -> Moment {
    Moment::from_micros((days_from_civil(2026, 11, 3) * 86_400 + 20 * 60 - offset) * 1_000_000)
}

#[test]
fn just_after_midnight_the_words_agree_with_the_days_the_list_heads() {
    for (name, zone, written) in zones() {
        let now = twenty_past_midnight(zone.offset_at(1_793_620_800));
        let at = |wall: &str| format!("{wall}{written}");
        for (wall, said, head) in [
            ("2026-11-03T00:19:30", "just now", "Today"),
            ("2026-11-03T00:05:00", "15m ago", "Today"),
            ("2026-11-02T23:50:00", "yesterday", "Yesterday: not just now, half an hour back"),
            ("2026-11-02T00:30:00", "yesterday", "Yesterday: not 23h ago"),
            ("2026-11-01T18:00:00", "2d ago", "Past week: not yesterday, 30 hours back"),
            ("2026-10-27T22:00:00", "1w ago", "Earlier: not 6d ago"),
        ] {
            assert_eq!(rel_time(Some(&at(wall)), now, &*zone), said, "{name}, {wall}, under {head}");
            assert_eq!(relative::rel_time(Some(&at(wall)), now, &*zone), said, "{name}, {wall}, under {head}");
        }
    }
}

#[test]
fn a_time_written_in_another_zone_falls_on_the_persons_day() {
    // 05:00 in New York on the 2nd is 10:00 UTC, and 23:00 in Auckland; it is 23:30 UTC, 12:30 on the 3rd there.
    let auckland = Rule::parse("NZST-12NZDT,M9.5.0,M4.1.0/3").expect("a rule");
    let now = Moment::from_micros((days_from_civil(2026, 11, 2) * 86_400 + 23 * 3600 + 30 * 60) * 1_000_000);
    let sent = Some("2026-11-02T05:00:00-05:00");
    assert_eq!(rel_time(sent, now, &auckland), "yesterday");
    assert_eq!(relative::rel_time(sent, now, &auckland), "yesterday");
    assert_eq!(rel_time(sent, now, &Utc), "13h ago");
    assert_eq!(relative::rel_time(sent, now, &Utc), "13h ago");
}

#[test]
fn iso_times_read_as_fromisoformat_reads_them() {
    let at = |s| from_iso(s).map(|w| (w.at.whole_seconds(), w.offset));
    assert_eq!(at("2026-11-02T13:00:00+01:00"), Some((SECS, 3600)));
    assert_eq!(at("2026-11-02T12:00:00Z"), Some((SECS, 0)));
    assert_eq!(at("2026-11-02 12:00"), Some((SECS, 0)));
    assert_eq!(at("20261102T120000"), Some((SECS, 0)));
    assert_eq!(at("2026-11-02T10:00:00-0200"), Some((SECS, -7200)));
    assert_eq!(at("2026-11-02"), Some((SECS - 12 * 3600, 0)));
    assert!(from_iso("2026-02-30").is_none());
    assert!(from_iso("2026-10-31T25:00").is_none());
    assert!(from_iso("2026-10-31T08:45").is_some());
    assert_eq!(from_iso("2026-10-31T08:45:00.5").map(|w| w.at.micros().rem_euclid(1_000_000)), Some(500_000));
}

#[test]
fn how_long_ago_and_how_soon() {
    assert_eq!(ago(None, NOW), "a while");
    assert_eq!(ago(Some(0), NOW), "a while");
    assert_eq!(ago(Some(SECS - 300), NOW), "5m");
    assert_eq!(ago(Some(SECS - 7200), NOW), "2h");
    assert_eq!(ago(Some(SECS - 3 * 86_400), NOW), "3d");
    assert_eq!(ago(Some(SECS + 50), NOW), "0m");
    assert_eq!(rel_time_future(SECS - 1, NOW), "soon");
    assert_eq!(rel_time_future(SECS + 600, NOW), "in 10m");
    assert_eq!(rel_time_future(SECS + 3 * 3600 + 300, NOW), "today 3:05 PM");
    assert_eq!(rel_time_future(SECS + 21 * 3600, NOW), "tomorrow 9:00 AM");
    assert_eq!(rel_time_future(SECS + 4 * 86_400, NOW), "Fri 12:00 PM");
    assert_eq!(rel_time_future(SECS + 30 * 86_400, NOW), "Dec 2");
}

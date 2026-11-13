//! How a time is said: `mailmind.kernel.when` at the suites' frozen time.

use mm_kernel::ago::{ago, from_iso, rel_time, rel_time_future};
use mm_kernel::seams::Moment;

// conftest.NOW, 2026-11-02 12:00 UTC.
const NOW: Moment = Moment::from_micros(1_793_620_800_000_000);
const SECS: i64 = 1_793_620_800;

#[test]
fn a_stored_time_is_said_as_the_page_says_it() {
    assert_eq!(rel_time(Some("2026-11-02T11:30:00+00:00"), NOW), "just now");
    assert_eq!(rel_time(Some("2026-11-02T03:00:00+00:00"), NOW), "9h ago");
    assert_eq!(rel_time(Some("2026-11-01T09:00:00+00:00"), NOW), "yesterday");
    assert_eq!(rel_time(Some("2026-10-27T09:14:00+00:00"), NOW), "6d ago");
    assert_eq!(rel_time(Some("2026-10-14T09:14:00+00:00"), NOW), "2w ago");
    // Past a month, the date as it was written, in its own offset.
    assert_eq!(rel_time(Some("2026-09-01T23:30:00-02:00"), NOW), "Sep 01");
    assert_eq!(rel_time(Some("2026-11-03T12:00:00+00:00"), NOW), "-1d ago");
    assert_eq!(rel_time(Some("not a date at all"), NOW), "not a date");
    assert_eq!(rel_time(Some(""), NOW), "");
    assert_eq!(rel_time(None, NOW), "");
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

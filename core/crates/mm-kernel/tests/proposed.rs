//! What a model proposes for a calendar, read with care (`tests/unit/kernel/test_proposed.py`'s): the values here are
//! what the Python app's `kernel.proposed` gives for the same text.

use mm_kernel::proposed::{ends_after, is_day, one_line, read_date, read_moment};
use serde_json::{Value, json};

const TODAY: (i64, u32, u32) = (2026, 11, 2);

fn moment(text: &str) -> Option<String> {
    read_moment(Some(&json!(text)), TODAY)
}

#[test]
fn a_day_or_a_time_reads_to_the_minute_with_its_offset() {
    assert_eq!(moment(" 2026-11-03T10:00:30.5+0530 ").as_deref(), Some("2026-11-03T10:00+05:30"));
    assert_eq!(moment("2026-11-03 09:15Z").as_deref(), Some("2026-11-03T09:15+00:00"));
    assert_eq!(moment("2026-11-03T09:15").as_deref(), Some("2026-11-03T09:15"));
    assert_eq!(moment("2031-11-03").as_deref(), Some("2031-11-03"));
    assert_eq!(read_date(Some(&json!("2026-11-04T23:00")), TODAY).as_deref(), Some("2026-11-04"));
    assert!(is_day("2026-11-03") && !is_day("2026-11-03T10:00"));
}

#[test]
fn what_wont_read_or_falls_outside_the_window_is_none() {
    for text in
        ["2026-02-30", "2026-13-01", "2026-11-03T24:00", "next Tuesday", "2024-11-01", "2033-01-01", "2026-11-3"]
    {
        assert_eq!(moment(text), None, "{text}");
    }
    assert_eq!(read_moment(Some(&json!(20261103)), TODAY), None);
    assert_eq!(read_moment(None, TODAY), None);
}

#[test]
fn an_end_ends_an_event_only_after_it_and_of_its_kind() {
    assert!(ends_after("2026-11-03T10:00+01:00", "2026-11-03T09:30Z"));
    assert!(ends_after("2026-11-03", "2026-11-03"));
    assert!(!ends_after("2026-11-03T10:00", "2026-11-03T10:00"));
    assert!(!ends_after("2026-11-03", "2026-11-03T11:00"));
    assert!(!ends_after("2026-11-03T10:00", "2026-11-03T11:00Z"));
    assert!(!ends_after("2026-11-03", "2026-12-05"));
}

#[test]
fn a_title_is_one_line_within_its_cap() {
    assert_eq!(one_line(Some(&json!("  Lunch\n\twith\u{2028}Lin\u{85} ")), 200), "Lunch with Lin");
    assert_eq!(one_line(Some(&json!("abcdef")), 3), "abc");
    assert_eq!(one_line(Some(&Value::Null), 3), "");
}

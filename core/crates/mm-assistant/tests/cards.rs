//! A brief, a delta and a review with no I/O (`tests/unit/assistant/test_brief.py` and `test_review.py`'s): the values
//! here are what the Python app's `domain.brief` and `domain.review` give for the same input.

use std::collections::HashSet;

use mm_assistant::brief::{Group, clean, clean_delta, delta_fallback, delta_since, fallback, trim_narrative};
use mm_assistant::ports::Glance;
use mm_assistant::review::{self, meeting_lines, meetings_of, week_so_far};
use serde_json::{Map, Value, json};

fn glance(
    pk: i64,
    category: Option<&str>,
    importance: i64,
    action: Option<&str>,
    from: (Option<&str>, Option<&str>),
) -> Glance {
    Glance {
        pk,
        category: category.map(str::to_owned),
        importance: Some(importance),
        action: action.map(str::to_owned),
        from_name: from.0.map(str::to_owned),
        from_addr: from.1.map(str::to_owned),
        subject: [None, Some("Lunch"), None, Some("Design"), Some("Venue")][pk as usize].map(str::to_owned),
        ..Glance::default()
    }
}

fn rows() -> Vec<Glance> {
    vec![
        glance(1, Some("info"), 2, Some(""), (Some(""), Some("lin@example.test"))),
        glance(2, None, 5, Some("Reply"), (Some("Ivo"), Some("ivo@example.test"))),
        glance(3, Some("action-needed"), 3, None, (None, None)),
        glance(4, Some("info"), 2, Some(" "), (Some("Mae"), Some("mae@example.test"))),
    ]
}

fn object(value: Value) -> Map<String, Value> {
    value.as_object().cloned().expect("an object")
}

fn group(label: &str, pks: &[i64]) -> Group {
    Group { label: label.into(), count: pks.len(), pks: pks.to_vec() }
}

#[test]
fn a_brief_that_wont_do_is_made_from_the_rows() {
    let (narrative, groups) = fallback(&rows());
    assert_eq!(
        narrative,
        "4 items need a glance \u{2014} 2 look like they need a reply. Grouped into 4 buckets below."
    );
    assert_eq!(
        groups,
        [group("Needs a reply", &[2, 3]), group("info", &[1, 4]), group("other", &[2]), group("action-needed", &[3])]
    );
}

#[test]
fn a_runaway_brief_ends_at_a_sentence_or_a_word_in_its_back_half() {
    let words = trim_narrative(&format!("  {}  ", "word ".repeat(400)), 1500);
    assert_eq!((words.chars().count(), words.ends_with("word word\u{2026}")), (1500, true));
    let sentences = trim_narrative(&format!("{}tail", "A sentence here. ".repeat(100)), 1500);
    assert_eq!((sentences.chars().count(), sentences.ends_with("re. A sentence here.")), (1495, true));
    let one_word = trim_narrative(&"x".repeat(1600), 1500);
    assert_eq!((one_word.chars().count(), one_word.ends_with("x\u{2026}")), (1501, true));
    assert_eq!(trim_narrative(" Short. ", 1500), "Short.");
}

#[test]
fn a_models_brief_and_delta_are_held_to_their_caps_and_to_what_it_was_shown() {
    let answer = object(json!({"narrative": " Busy day. ", "groups": [
        {"label": "L".repeat(50), "pks": [3, 1, 99, true, 2.0, "3"]}, {"label": null, "pks": [99]}, "x",
        {"label": 7, "pks": [2]}]}));
    let (narrative, groups) = clean(&answer, &HashSet::from([1, 2, 3]));
    assert_eq!((narrative.as_str(), groups), ("Busy day.", vec![group(&"L".repeat(40), &[3, 1, 1]), group("7", &[2])]));
    let delta = object(json!({"headline": "h".repeat(310), "items": [
        {"who": "W", "gist": 5, "importance": "4"}, {"who": null, "importance": 0}, {"importance": "x"},
        {"importance": 3.9}, 7]}));
    let (headline, items) = clean_delta(&delta);
    assert_eq!(headline.len(), 300);
    assert_eq!(
        items,
        [
            json!({"who": "W", "gist": "5", "importance": 4}),
            json!({"who": "", "gist": "", "importance": 2}),
            json!({"who": "", "gist": "", "importance": 2}),
            json!({"who": "", "gist": "", "importance": 3}),
        ]
    );
    let (headline, items) = delta_fallback(&rows()[..2]);
    assert_eq!(headline, "2 new since you last looked.");
    assert_eq!(
        items,
        [
            json!({"who": "lin@example.test", "gist": "Lunch", "importance": 2}),
            json!({"who": "Ivo", "gist": "(no subject)", "importance": 5}),
        ]
    );
}

#[test]
fn a_delta_looks_back_a_day_at_most_in_ten_minute_steps() {
    const NOW: i64 = 1_793_620_800;
    assert_eq!(
        (delta_since(0, NOW), delta_since(NOW - 3600, NOW), delta_since(NOW + 100_000_000, NOW)),
        (1_793_534_400, NOW - 3600, NOW)
    );
}

#[test]
fn a_review_written_on_monday_is_read_all_week() {
    assert_eq!(week_so_far((2026, 11, 4)), ["2026-11-04", "2026-11-03", "2026-11-02"]);
    assert_eq!(week_so_far((2026, 11, 2)), ["2026-11-02"]);
    assert_eq!(week_so_far((2026, 11, 8)).len(), 7);
}

fn event(summary: Value, start: &str, all_day: bool, key: i64) -> Value {
    json!({"key": key, "folder": "INBOX", "uid": 1, "account": "a", "thread_id": "t", "summary": summary,
           "start": start, "all_day": all_day})
}

#[test]
fn the_weeks_meetings_are_the_latest_gone_and_the_soonest_coming_each_once() {
    let gone = [
        event(json!("Old"), "2026-10-20T10:00", false, 1),
        event(json!("Standup"), "2026-10-27T09:00", false, 1),
        event(json!("Standup"), "2026-10-28T09:00", false, 1),
    ];
    let coming = [
        event(json!("Standup"), "2026-10-28T09:00", false, 2),
        event(Value::Null, "2026-11-03", true, 1),
        event(json!("Lunch"), "2026-11-05T12:30", false, 1),
        event(json!("Far"), "2026-11-20T10:00", false, 1),
    ];
    let meetings = meetings_of(&gone, &coming, "2026-10-26T12:00", "2026-11-02T12:00", "2026-11-09T12:00");
    let said: Vec<(&str, &str, i64, bool)> = meetings
        .iter()
        .map(|m| {
            (
                m["summary"].as_str().unwrap(),
                m["start"].as_str().unwrap(),
                m["key"].as_i64().unwrap(),
                m["past"] == true,
            )
        })
        .collect();
    assert_eq!(
        said,
        [
            ("Standup", "2026-10-27T09:00", 1, true),
            ("Standup", "2026-10-28T09:00", 1, true),
            ("(no title)", "2026-11-03", 1, false),
            ("Lunch", "2026-11-05T12:30", 1, false),
        ]
    );
    assert_eq!(
        meeting_lines(&meetings),
        "- 2026-10-27 09:00 (already happened): Standup\n- 2026-10-28 09:00 (already happened): Standup\n\
         - 2026-11-03 (all day): (no title)\n- 2026-11-05 12:30: Lunch"
    );
    assert_eq!(meeting_lines(&[]), "(none)");
}

#[test]
fn a_reviews_fingerprint_fallback_and_answer_read_as_the_python_apps() {
    let stats = object(json!({"sent": 1, "received": 4, "by_bucket": {}, "needs_you": 2, "waiting_on": 0,
                              "reply_later": 0, "cleaned": 0, "handled": 3}));
    let needs = [
        json!({"account": "a", "thread_id": null, "who": "Ivo", "subject": "Contract"}),
        json!({"account": "a", "thread_id": "t2", "who": "Noor", "subject": "Design"}),
    ];
    let waiting = [json!({"account": "b", "thread_id": "t9"})];
    assert_eq!(review::fingerprint_of(&stats, &needs, &waiting), "1:4:2:0:3|t2:2:0:9dc6aa50|t2:1:0:f9d6455a");
    let (narrative, next) = review::fallback(&stats, &needs);
    assert_eq!(
        narrative,
        "In the last 7 days you sent 1 message and 4 came in. You filed away 3. 2 still need a reply from you."
    );
    assert_eq!(next, ["Reply to Ivo \u{2014} Contract", "Reply to Noor \u{2014} Design"]);
    let mut clear = stats.clone();
    clear.extend([("sent".into(), json!(2)), ("needs_you".into(), json!(0)), ("handled".into(), json!(0))]);
    assert_eq!(
        review::fallback(&clear, &[]).0,
        "In the last 7 days you sent 2 messages and 4 came in. Your inbox is clear \u{2014} nothing is waiting on you."
    );
    let mut waits = stats.clone();
    waits.insert("waiting_on".into(), json!(1));
    assert!(
        review::fallback(&waits, &needs)
            .0
            .ends_with("2 still need a reply from you and you're waiting to hear back on 1.")
    );
    let (narrative, next) =
        review::clean(&object(json!({"narrative": "n".repeat(1500), "next": ["a".repeat(200), 5, "b", "c", "d"]})));
    assert_eq!((narrative.len(), next), (1400, vec!["a".repeat(160), "b".into(), "c".into()]));
    assert_eq!(
        review::clean(&object(json!({"next": "xyz"}))),
        (String::new(), vec!["x".into(), "y".into(), "z".into()])
    );
    assert!(review::degenerate("short") && review::degenerate(&"x".repeat(50)));
    assert!(!review::degenerate("one two three four five six seven eight nine ten"));
}

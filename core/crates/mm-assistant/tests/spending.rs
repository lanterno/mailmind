//! What AI cost over the last week and this month, by what a person calls each feature: the Spending card's
//! (`tests/unit/assistant/test_spending.py`).

use mm_assistant::spending::{OTHER, label, period, report};
use serde_json::{Value, json};

/// A day of the usage ledger's report: each feature's (calls, cost in micro-dollars), summed.
fn day(on: &str, features: &[(&str, i64, i64)]) -> Value {
    let by: serde_json::Map<String, Value> =
        features.iter().map(|(f, c, m)| ((*f).to_owned(), json!({"calls": c, "cost_micros": m}))).collect();
    json!({"day": on, "calls": features.iter().map(|f| f.1).sum::<i64>(),
           "cost_micros": features.iter().map(|f| f.2).sum::<i64>(), "features": by})
}

#[test]
fn a_feature_made_two_ways_is_one_line_and_one_not_known_is_other() {
    let days = [
        day("2026-11-02", &[("triage", 3, 300), ("triage-batch", 10, 500), ("brief-ahead", 1, 900)]),
        day("2026-11-01", &[("brief", 1, 800), ("future-feature", 2, 20)]),
    ];
    assert_eq!(
        period(&days),
        json!({"calls": 17, "cost_micros": 2520, "features": [
            {"label": "Today's brief", "calls": 2, "cost_micros": 1700},
            {"label": "Summaries", "calls": 13, "cost_micros": 800},
            {"label": "Other", "calls": 2, "cost_micros": 20}]})
    );
}

#[test]
fn a_line_with_no_call_is_left_out_and_lines_that_cost_the_same_go_by_calls_then_name() {
    let days = [day("2026-11-02", &[("ask", 1, 100), ("rewrite", 2, 100), ("extract", 1, 100), ("thread", 0, 0)])];
    let labels: Vec<Value> =
        period(&days)["features"].as_array().unwrap_or(&Vec::new()).iter().map(|f| f["label"].clone()).collect();
    assert_eq!(labels, [json!("Rewrite"), json!("Ask"), json!("Dates and to-dos")]);
}

#[test]
fn the_week_is_the_last_seven_days_and_the_month_its_days_so_far() {
    let mut days: Vec<Value> = [3, 2, 1].iter().map(|d| day(&format!("2026-11-{d:02}"), &[("ask", 1, 10)])).collect();
    days.extend((4..=31).rev().map(|d| day(&format!("2026-10-{d:02}"), &[("ask", 1, 10)])));
    let said = report(&days);
    assert_eq!((&said["week"]["calls"], &said["month"]["calls"], &said["ok"]), (&json!(7), &json!(3), &json!(true)));
    let none = json!({"calls": 0, "cost_micros": 0, "features": []});
    assert_eq!(report(&[]), json!({"ok": true, "week": none, "month": none}));
}

#[test]
fn a_sum_is_held_at_the_most_the_ledger_holds() {
    let most = period(&[day("2026-11-02", &[("ask", 1, i64::MAX)]), day("2026-11-01", &[("ask", 1, i64::MAX)])]);
    assert_eq!((&most["cost_micros"], &most["features"][0]["cost_micros"]), (&json!(i64::MAX), &json!(i64::MAX)));
}

#[test]
fn every_feature_a_call_names_has_its_own_words() {
    for feature in [
        "triage",
        "triage-batch",
        "thread",
        "brief",
        "brief-ahead",
        "delta",
        "review",
        "review-ahead",
        "draft",
        "draft-ahead",
        "rewrite",
        "extract",
        "screen",
        "rationale",
        "sender",
        "ask",
        "plan",
    ] {
        assert_ne!(label(feature), OTHER, "{feature}");
    }
}

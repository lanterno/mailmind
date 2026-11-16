//! What AI cost over the last week and this month, by what a person would call each feature
//! (`mailmind.assistant.domain.spending`): what the Spending card in Settings shows. The provider's own console holds the
//! bill; this is what MailMind counted from each call it made.
//!
//! A feature's calls are counted under the name the call gave the usage ledger; the ones a person sees as one thing (a
//! summary made one at a time or in a batch, a brief asked for or written ahead) are summed under one label. A name not
//! listed here is "Other".

use serde_json::{Value, json};

/// The days the week holds, today's among them.
pub const WEEK_DAYS: usize = 7;
/// The most days a month has, so the usage asked for always holds this month.
pub const MONTH_DAYS: i64 = 31;

pub const OTHER: &str = "Other";

/// What a person would call the feature the usage ledger names `feature`.
pub fn label(feature: &str) -> &'static str {
    match feature {
        "triage" | "triage-batch" => "Summaries",
        "thread" => "Conversation summaries",
        "brief" | "brief-ahead" => "Today's brief",
        "delta" => "What came in since you looked",
        "review" | "review-ahead" => "Your week",
        "draft" => "Drafted replies",
        "draft-ahead" => "Replies drafted ahead",
        "rewrite" => "Rewrite",
        "extract" => "Dates and to-dos",
        "screen" => "The Screener",
        "rationale" => "Cleanup suggestions",
        "sender" => "About a sender",
        "ask" => "Ask",
        "plan" => "Commands in words",
        _ => OTHER,
    }
}

fn count(value: &Value, name: &str) -> i64 {
    value.get(name).and_then(Value::as_i64).unwrap_or(0)
}

/// `days` (the usage ledger's report, `UsageReport::days`) summed: the calls and their cost in micro-dollars, in all and
/// by label, the dearest first (then the most calls, then by label), with no label that made no call. Each sum is held
/// at the most the ledger holds.
pub fn period<'a>(days: impl IntoIterator<Item = &'a Value>) -> Value {
    let (mut calls, mut cost) = (0_i64, 0_i64);
    let mut by_label: Vec<(&'static str, i64, i64)> = Vec::new();
    for day in days {
        calls = calls.saturating_add(count(day, "calls"));
        cost = cost.saturating_add(count(day, "cost_micros"));
        for (feature, spent) in day.get("features").and_then(Value::as_object).into_iter().flatten() {
            let name = label(feature);
            let at = match by_label.iter().position(|(n, _, _)| *n == name) {
                Some(at) => at,
                None => {
                    by_label.push((name, 0, 0));
                    by_label.len() - 1
                }
            };
            let summed = &mut by_label[at];
            summed.1 = summed.1.saturating_add(count(spent, "calls"));
            summed.2 = summed.2.saturating_add(count(spent, "cost_micros"));
        }
    }
    by_label.retain(|(_, c, m)| *c > 0 || *m > 0);
    by_label.sort_by(|a, b| b.2.cmp(&a.2).then(b.1.cmp(&a.1)).then(a.0.cmp(b.0)));
    let features: Vec<Value> =
        by_label.into_iter().map(|(name, c, m)| json!({"label": name, "calls": c, "cost_micros": m})).collect();
    json!({"calls": calls, "cost_micros": cost, "features": features})
}

/// The last week and this month (`Spending`), from `days`, the usage ledger's report of at least `MONTH_DAYS` local
/// days, newest first: the week is the last `WEEK_DAYS` days, today among them, and the month the days of today's month
/// so far.
pub fn report(days: &[Value]) -> Value {
    let month_of = |day: &Value| day.get("day").and_then(Value::as_str).and_then(|d| d.get(..7)).map(str::to_owned);
    let month = days.first().and_then(month_of);
    let week = period(days.iter().take(WEEK_DAYS));
    let this_month = period(days.iter().filter(|day| month.is_some() && month_of(day) == month));
    json!({"ok": true, "week": week, "month": this_month})
}

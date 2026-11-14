//! A brief and a delta, with no I/O (`domain.brief`): what is taken from a model's answer, what stands in when the
//! answer won't do, and how far back a delta may look. A model's answer is its own words, so each is held to its caps
//! here, and a brief's ids to what it was shown.

use std::collections::HashSet;

use mm_kernel::text::is_space;
use serde_json::{Map, Value, json};

use crate::ports::Glance;
use crate::text::{cut, length, python_int, python_str, strip, truthy};

pub const GROUPS_MAX: usize = 6;
/// Messages one of a brief's groups may hold.
pub const GROUP_MAX: usize = 80;
pub const DELTA_TOP_N: i64 = 25;
/// How far back a delta looks at most.
pub const DELTA_FLOOR: i64 = 24 * 3600;
/// The floor moves in these steps.
pub const DELTA_FLOOR_STEP: i64 = 600;

/// A brief's group: the messages it names, by pk.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Group {
    pub label: String,
    pub count: usize,
    pub pks: Vec<i64>,
}

/// Who a message is from, as a brief says it.
pub fn who(r: &Glance) -> &str {
    [&r.from_name, &r.from_addr].into_iter().flatten().map(String::as_str).find(|s| !s.is_empty()).unwrap_or("someone")
}

/// A text that says something, else `instead`: Python's `or`.
pub fn or<'a>(text: &'a Option<String>, instead: &'a str) -> &'a str {
    text.as_deref().filter(|t| !t.is_empty()).unwrap_or(instead)
}

/// A number as Python's f-string writes it, None as "None".
pub fn number(n: Option<i64>) -> String {
    n.map_or_else(|| "None".to_owned(), |n| n.to_string())
}

/// A card made straight from the rows, for when Claude's reply won't do: a bad reply is kept once rather than asked
/// again and again.
pub fn fallback(rows: &[Glance]) -> (String, Vec<Group>) {
    let mut by_category: Vec<(String, Vec<i64>)> = Vec::new();
    for r in rows {
        let category = or(&r.category, "other");
        match by_category.iter_mut().find(|(c, _)| c == category) {
            Some((_, pks)) => pks.push(r.pk),
            None => by_category.push((category.to_owned(), vec![r.pk])),
        }
    }
    let need: Vec<i64> = rows
        .iter()
        .filter(|r| mm_kernel::verdict::needs_you(r.importance, r.category.as_deref(), r.action.as_deref()))
        .map(|r| r.pk)
        .collect();
    let mut groups = Vec::new();
    if !need.is_empty() {
        groups.push(Group {
            label: "Needs a reply".into(),
            count: need.len(),
            pks: need.iter().take(60).copied().collect(),
        });
    }
    // Most first; a tie keeps the order the categories came in.
    by_category.sort_by_key(|(_, pks)| std::cmp::Reverse(pks.len()));
    for (category, pks) in by_category.into_iter().take(5) {
        groups.push(Group { label: category, count: pks.len(), pks: pks.into_iter().take(60).collect() });
    }
    let replies =
        if need.is_empty() { String::new() } else { format!(" \u{2014} {} look like they need a reply", need.len()) };
    let narrative =
        format!("{} items need a glance{replies}. Grouped into {} buckets below.", rows.len(), groups.len());
    (narrative, groups)
}

/// A brief's prose whole under a generous cap. Only a runaway reply over it is cut: at its last whole sentence in the
/// back half, else at a word there, and an ellipsis says so.
pub fn trim_narrative(text: &str, cap: usize) -> String {
    let text = strip(text);
    if length(text) <= cap {
        return text.to_owned();
    }
    let cut: Vec<char> = text.chars().take(cap).collect();
    let find_last = |pattern: &[char], from: usize| -> Option<usize> {
        (from..=cut.len().saturating_sub(pattern.len())).rev().find(|&i| cut[i..].starts_with(pattern))
    };
    let end = [['.', ' '], ['!', ' '], ['?', ' ']].iter().filter_map(|p| find_last(p, 0)).max();
    if let Some(end) = end.filter(|&e| e as f64 > cap as f64 * 0.5) {
        return cut[..=end].iter().collect::<String>().trim_end_matches(is_space).to_owned();
    }
    let space = find_last(&[' '], cap / 2).filter(|&s| s > 0);
    let kept: String = cut[..space.unwrap_or(cut.len())].iter().collect();
    format!("{}\u{2026}", kept.trim_end_matches(is_space))
}

/// A list a model's JSON gave, or nothing: Python's `x or []` where only a list is iterated.
fn listed(value: Option<&Value>) -> &[Value] {
    match value {
        Some(Value::Array(items)) => items,
        _ => &[],
    }
}

/// One group of a brief: the integers it named that it was shown, up to GROUP_MAX.
pub fn in_group(named: &[Value], shown: &HashSet<i64>) -> Vec<i64> {
    named
        .iter()
        .filter_map(|p| match p {
            Value::Bool(b) => Some(i64::from(*b)),
            Value::Number(n) => n.as_i64(),
            _ => None,
        })
        .filter(|p| shown.contains(p))
        .take(GROUP_MAX)
        .collect()
}

/// What the model said `name` was, as Python's `str(obj.get(name) or "")`.
fn said(answer: &Map<String, Value>, name: &str) -> String {
    answer.get(name).filter(|v| truthy(v)).map(python_str).unwrap_or_default()
}

/// The model's brief held to its caps, its ids to those it was shown.
pub fn clean(answer: &Map<String, Value>, valid: &HashSet<i64>) -> (String, Vec<Group>) {
    let narrative = trim_narrative(&said(answer, "narrative"), 1500);
    let mut groups = Vec::new();
    for g in listed(answer.get("groups")).iter().take(GROUPS_MAX) {
        let Value::Object(g) = g else { continue };
        let pks = in_group(listed(g.get("pks")), valid);
        if pks.is_empty() {
            continue;
        }
        groups.push(Group { label: cut(&said(g, "label"), 40).to_owned(), count: pks.len(), pks });
    }
    (narrative, groups)
}

/// The model's delta held to its caps.
pub fn clean_delta(answer: &Map<String, Value>) -> (String, Vec<Value>) {
    let headline = cut(&said(answer, "headline"), 300).to_owned();
    let mut items = Vec::new();
    for it in listed(answer.get("items")).iter().take(DELTA_TOP_N as usize) {
        let Value::Object(it) = it else { continue };
        let importance = it.get("importance").filter(|v| truthy(v)).map_or(Some(2), python_int).unwrap_or(2);
        items.push(json!({"who": cut(&said(it, "who"), 60), "gist": cut(&said(it, "gist"), 120),
                          "importance": importance}));
    }
    (headline, items)
}

/// A delta made straight from the rows, for when Claude's reply won't do.
pub fn delta_fallback(rows: &[Glance]) -> (String, Vec<Value>) {
    let items = rows
        .iter()
        .take(DELTA_TOP_N as usize)
        .map(|r| {
            json!({"who": cut(who(r), 60), "gist": cut(or(&r.subject, "(no subject)"), 120),
                   "importance": r.importance})
        })
        .collect();
    (format!("{} new since you last looked.", rows.len()), items)
}

/// What a delta looks back to: the watermark a tab sent, no further back than a day and no later than now (a tab's
/// clock may be ahead). The day's floor moves in 10-minute steps, so a stale watermark gives a stable key and not a
/// fresh call every second.
pub fn delta_since(watermark: i64, now: i64) -> i64 {
    let floor = now - DELTA_FLOOR;
    let floor = floor - floor.rem_euclid(DELTA_FLOOR_STEP);
    watermark.max(floor).min(now)
}

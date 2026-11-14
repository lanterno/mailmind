//! A weekly review, with no I/O (`domain.review`): what is taken from a model's answer, what stands in when the answer
//! won't do, what makes one stale, the week's meetings, and when one is written ahead and read.

use serde_json::{Map, Value, json};

use crate::digest::fingerprint;
use crate::text::{cut, length, python_str, truthy};

/// Of each list asked of the calendar.
pub const EVENTS_READ: usize = 40;
/// How many just gone, and how many to come.
pub const EVENTS_EACH_WAY: usize = 6;
pub const EVENT_SUMMARY_MAX: usize = 120;
pub const AHEAD_DAYS: i64 = 7;
/// Monday: the week just ended is reviewed on its morning, and read until the next.
pub const WRITTEN_AHEAD_ON: i64 = 0;

/// The day of the week of a day counted from the epoch, Monday 0.
pub fn weekday(days: i64) -> i64 {
    (days + 3).rem_euclid(7)
}

/// The days of today's week so far, newest first, from today back to its first: those a review written ahead is read
/// on.
pub fn week_so_far(today: (i64, u32, u32)) -> Vec<String> {
    let days = mm_kernel::when::days_from_civil(today.0, today.1, today.2);
    (0..=(weekday(days) - WRITTEN_AHEAD_ON).rem_euclid(7))
        .map(|n| {
            let (y, m, d) = mm_kernel::when::civil(days - n);
            format!("{y:04}-{m:02}-{d:02}")
        })
        .collect()
}

/// A local wall-clock time, in seconds as though it were UTC, to the minute: `YYYY-MM-DDTHH:MM`.
pub fn minutes_iso(local: i64) -> String {
    let (y, m, d) = mm_kernel::when::civil(local.div_euclid(86_400));
    let second = local.rem_euclid(86_400);
    format!("{y:04}-{m:02}-{d:02}T{:02}:{:02}", second / 3600, second % 3600 / 60)
}

fn text<'a>(event: &'a Value, name: &str) -> &'a str {
    event.get(name).and_then(Value::as_str).unwrap_or_default()
}

/// The week's events, from the calendar's answer as of a week ago and its answer as of now, since each lists what
/// hadn't ended by then: the latest of what has started, then the soonest of what hasn't, each event once. The times are
/// local wall-clock ones, as the calendar writes them.
pub fn meetings_of(gone: &[Value], coming: &[Value], since: &str, now: &str, until: &str) -> Vec<Value> {
    let mut each: Vec<&Value> = Vec::new();
    for event in gone.iter().chain(coming) {
        let same = |e: &&Value| e.get("summary") == event.get("summary") && text(e, "start") == text(event, "start");
        if !each.iter().any(same) {
            each.push(event);
        }
    }
    let mut within: Vec<&Value> =
        each.into_iter().filter(|e| since <= text(e, "start") && text(e, "start") < until).collect();
    within.sort_by(|a, b| text(a, "start").cmp(text(b, "start")));
    let past: Vec<&Value> = within.iter().copied().filter(|e| text(e, "start") < now).collect();
    let next = within.iter().copied().filter(|e| text(e, "start") >= now).take(EVENTS_EACH_WAY);
    let chosen = past[past.len().saturating_sub(EVENTS_EACH_WAY)..].iter().copied().chain(next);
    chosen
        .map(|e| {
            let summary = e.get("summary").filter(|s| truthy(s)).map_or_else(|| "(no title)".to_owned(), python_str);
            json!({"key": e.get("key"), "folder": e.get("folder"), "uid": e.get("uid"), "account": e.get("account"),
                   "thread_id": e.get("thread_id"), "summary": cut(&summary, EVENT_SUMMARY_MAX),
                   "start": text(e, "start"), "all_day": e.get("all_day").is_some_and(truthy),
                   "past": text(e, "start") < now})
        })
        .collect()
}

/// The week's events as the model is told them.
pub fn meeting_lines(meetings: &[Value]) -> String {
    let lines: Vec<String> = meetings
        .iter()
        .map(|m| {
            let all_day = m.get("all_day").is_some_and(truthy);
            let start = text(m, "start");
            let when = if all_day { cut(start, 10).to_owned() } else { start.replace('T', " ") };
            let day = if all_day { " (all day)" } else { "" };
            let past = if m.get("past").is_some_and(truthy) { " (already happened)" } else { "" };
            format!("- {when}{day}{past}: {}", text(m, "summary"))
        })
        .collect();
    if lines.is_empty() { "(none)".to_owned() } else { lines.join("\n") }
}

fn count(stats: &Map<String, Value>, name: &str) -> i64 {
    stats.get(name).and_then(Value::as_i64).unwrap_or(0)
}

/// An item's thread, as a fingerprint names it: `account|thread_id`.
fn thread_of(item: &Value) -> String {
    let part = |name| item.get(name).map_or_else(|| "None".to_owned(), python_str);
    format!("{}|{}", part("account"), part("thread_id"))
}

/// The week as reviewed: its counts, and the threads that need the person or wait on others, named by account and
/// thread so a renumbered folder changes nothing.
pub fn fingerprint_of(stats: &Map<String, Value>, needs: &[Value], waiting: &[Value]) -> String {
    let threads = |items: &[Value]| {
        let named: Vec<String> = items.iter().map(thread_of).collect();
        fingerprint(named.iter().map(|n| Some(n.as_str())), 0)
    };
    let counts = ["sent", "received", "needs_you", "waiting_on", "handled"].map(|n| count(stats, n).to_string());
    format!("{}|{}|{}", counts.join(":"), threads(needs), threads(waiting))
}

/// The review's prose and picks, from the numbers alone.
pub fn fallback(stats: &Map<String, Value>, needs: &[Value]) -> (String, Vec<String>) {
    let (sent, received, handled) = (count(stats, "sent"), count(stats, "received"), count(stats, "handled"));
    let (needs_you, waiting_on) = (count(stats, "needs_you"), count(stats, "waiting_on"));
    let mut n = format!(
        "In the last 7 days you sent {sent} message{} and {received} came in. ",
        if sent != 1 { "s" } else { "" }
    );
    if handled != 0 {
        n += &format!("You filed away {handled}. ");
    }
    if needs_you != 0 || waiting_on != 0 {
        n += &format!("{needs_you} still need a reply from you");
        n += &if waiting_on != 0 {
            format!(" and you're waiting to hear back on {waiting_on}.")
        } else {
            ".".to_owned()
        };
    } else {
        n += "Your inbox is clear \u{2014} nothing is waiting on you.";
    }
    let next =
        needs.iter().take(3).map(|x| format!("Reply to {} \u{2014} {}", text(x, "who"), text(x, "subject"))).collect();
    (n, next)
}

/// The model's review held to its caps: its prose, and at most three picks.
pub fn clean(answer: &Map<String, Value>) -> (String, Vec<String>) {
    let narrative = answer.get("narrative").filter(|v| truthy(v)).map(python_str).unwrap_or_default();
    let next: Vec<String> = match answer.get("next").filter(|v| truthy(v)) {
        Some(Value::Array(told)) => {
            told.iter().filter_map(Value::as_str).map(|x| cut(x, 160).to_owned()).take(3).collect()
        }
        // Python iterated a string's characters.
        Some(Value::String(told)) => told.chars().take(3).map(String::from).collect(),
        _ => Vec::new(),
    };
    (cut(&narrative, 1400).to_owned(), next)
}

/// An empty reply, or a stub like "test": a real retrospective is a few sentences.
pub fn degenerate(narrative: &str) -> bool {
    length(narrative) < 40 || narrative.split(mm_kernel::text::is_space).filter(|w| !w.is_empty()).count() < 7
}

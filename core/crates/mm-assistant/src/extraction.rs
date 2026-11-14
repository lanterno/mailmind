//! What the model's reading of a message comes to (`domain.extraction`): the meetings and the dated tasks its text
//! proposes, as suggestions a person looks over. Nothing here creates anything. The answer is read with care, within
//! the caps, and an invalid date is dropped, as `mm_kernel::proposed` says. An event needs a day; a task can stand
//! without one. Nothing here fails.

use mm_kernel::proposed::{self, Day};
use serde_json::{Map, Value, json};

use crate::context::quoted;
use crate::text::{cut, lines, strip};

pub const MAX_EVENTS: usize = 5;
pub const MAX_TASKS: usize = 10;
/// How much of a message is read: a day named near the end of a long one is still found.
pub const TEXT_MAX: usize = 8000;

/// A message's text as the model is shown it: quoted lines left out, as they are what was said before this message.
/// A forward's text stays, as that is often all a forward says.
pub fn readable(body: &str) -> String {
    let kept: Vec<&str> = lines(body).into_iter().filter(|l| !quoted(l)).collect();
    cut(strip(&kept.join("\n")), TEXT_MAX).to_owned()
}

fn objects(held: Option<&Value>) -> Vec<&Map<String, Value>> {
    match held {
        Some(Value::Array(items)) => items.iter().filter_map(Value::as_object).collect(),
        _ => Vec::new(),
    }
}

/// A meeting the model named, or None for one with no title or no day we can read. An end that can't end it is left
/// off.
fn event_of(raw: &Map<String, Value>, today: Day) -> Option<(String, String, Value)> {
    let title = proposed::one_line(raw.get("title"), proposed::TITLE_MAX);
    let start = proposed::read_moment(raw.get("start"), today)?;
    if title.is_empty() {
        return None;
    }
    let end = proposed::read_moment(raw.get("end"), today).filter(|end| proposed::ends_after(&start, end));
    let event = json!({"title": title, "start": start, "end": end,
                       "place": proposed::one_line(raw.get("place"), proposed::PLACE_MAX),
                       "all_day": proposed::is_day(&start)});
    Some((title.to_lowercase(), start, event))
}

/// A task the model named, or None for one with no title. A date that won't read leaves it without one.
fn task_of(raw: &Map<String, Value>, today: Day) -> Option<(String, Option<String>, Value)> {
    let title = proposed::one_line(raw.get("title"), proposed::TITLE_MAX);
    if title.is_empty() {
        return None;
    }
    let due = proposed::read_date(raw.get("due"), today);
    let task = json!({"title": title, "due": due});
    Some((title.to_lowercase(), due, task))
}

fn distinct<K: PartialEq>(found: impl Iterator<Item = (K, Value)>, cap: usize) -> Vec<Value> {
    let mut seen: Vec<K> = Vec::new();
    let mut out = Vec::new();
    for (key, one) in found {
        if seen.contains(&key) {
            continue;
        }
        seen.push(key);
        out.push(one);
        if out.len() == cap {
            break;
        }
    }
    out
}

/// What is taken from the model's answer: each event and task once, within the caps, as `events` and `tasks`.
pub fn suggestions(answer: &Map<String, Value>, today: Day) -> (Vec<Value>, Vec<Value>) {
    let events = objects(answer.get("events"))
        .into_iter()
        .filter_map(|raw| event_of(raw, today))
        .map(|(title, start, event)| ((title, start), event));
    let tasks = objects(answer.get("tasks"))
        .into_iter()
        .filter_map(|raw| task_of(raw, today))
        .map(|(title, due, task)| ((title, due), task));
    (distinct(events, MAX_EVENTS), distinct(tasks, MAX_TASKS))
}

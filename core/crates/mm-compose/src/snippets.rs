//! Snippets: your own words kept under a short name, which `;name` in the composer's text brings in
//! (`mailmind.compose.domain.snippets` and `application.snippets`). What makes one, what is read back of those saved,
//! and the use cases /api/snippets lists and changes them through. {{A placeholder}} in the text is only words here:
//! going through them is the page's.

use std::sync::Arc;

use mm_kernel::errors::{Failure, Problem};
use mm_kernel::text;
use serde_json::{Map, Value, json};

use crate::ports::SnippetsStore;

pub const MAX_SNIPPETS: usize = 200;
pub const MAX_TEXT: usize = 20_000;

/// One snippet: what names it, once, with no `;` and in lowercase, and its words.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Snippet {
    pub trigger: String,
    pub text: String,
}

impl Snippet {
    pub fn to_json(&self) -> Value {
        json!({"trigger": self.trigger, "text": self.text})
    }
}

/// Text with LF line ends only (`compose.domain.resume.lf`).
fn lf(text: &str) -> String {
    text.replace("\r\n", "\n").replace('\r', "\n")
}

/// The first `n` characters of `text`.
fn first_chars(text: &str, n: usize) -> String {
    text.chars().take(n).collect()
}

/// The trigger this names, however it was typed, or None for what can't be one: up to 32 letters, digits, `-` or `_`,
/// starting with a letter or a digit.
pub fn trigger_of(raw: Option<&Value>) -> Option<String> {
    let raw = raw?.as_str()?;
    let stripped = text::strip(raw);
    let trigger = stripped.strip_prefix(';').unwrap_or(stripped).to_lowercase();
    let b = trigger.as_bytes();
    let fits = (1..=32).contains(&b.len())
        && (b[0].is_ascii_lowercase() || b[0].is_ascii_digit())
        && b.iter().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || *c == b'_' || *c == b'-');
    fits.then_some(trigger)
}

fn in_order(mut snippets: Vec<Snippet>) -> Vec<Snippet> {
    snippets.sort_by(|a, b| a.trigger.cmp(&b.trigger));
    snippets
}

/// The snippets out of whatever was saved. It never fails: it drops what isn't one, and of two with a trigger keeps
/// the first.
pub fn clean(raw: Option<&Value>) -> Vec<Snippet> {
    let mut found: Vec<Snippet> = Vec::new();
    for item in raw.and_then(Value::as_array).into_iter().flatten() {
        let Some(item) = item.as_object() else { continue };
        let (Some(trigger), Some(Value::String(words))) = (trigger_of(item.get("trigger")), item.get("text")) else {
            continue;
        };
        if text::strip(words).is_empty() || found.iter().any(|s| s.trigger == trigger) {
            continue;
        }
        found.push(Snippet { trigger, text: first_chars(&lf(words), MAX_TEXT) });
    }
    let mut kept = in_order(found);
    kept.truncate(MAX_SNIPPETS);
    kept
}

/// The snippets with this one added, or put in the place of the one its trigger names. Refused for what can't be
/// kept, and never cut short: those are someone's words.
pub fn saved(current: &[Snippet], trigger: Option<&Value>, words: Option<&str>) -> Result<Vec<Snippet>, Problem> {
    let name = trigger_of(trigger).ok_or_else(|| Problem::refused("a trigger is up to 32 letters, digits, - or _"))?;
    let words = lf(words.unwrap_or_default());
    let words = words.trim_matches('\n');
    if text::strip(words).is_empty() {
        return Err(Problem::refused("a snippet needs some text"));
    }
    if words.chars().count() > MAX_TEXT {
        return Err(Problem::refused("a snippet can be up to 20,000 characters"));
    }
    let mut others: Vec<Snippet> = current.iter().filter(|s| s.trigger != name).cloned().collect();
    if others.len() >= MAX_SNIPPETS {
        return Err(Problem::refused(format!("there can be {MAX_SNIPPETS} snippets: delete one first")));
    }
    others.push(Snippet { trigger: name, text: words.to_owned() });
    Ok(in_order(others))
}

/// The snippets with this one gone, or None if that changes nothing.
pub fn without(current: &[Snippet], trigger: Option<&Value>) -> Option<Vec<Snippet>> {
    let name = trigger_of(trigger);
    let kept: Vec<Snippet> = current.iter().filter(|s| Some(&s.trigger) != name.as_ref()).cloned().collect();
    (kept.len() != current.len()).then_some(kept)
}

fn listed(snippets: &[Snippet]) -> Value {
    json!({"ok": true, "snippets": snippets.iter().map(Snippet::to_json).collect::<Vec<_>>()})
}

/// The snippets, as /api/snippets lists and changes them.
#[derive(Debug)]
pub struct Snippets {
    store: Arc<dyn SnippetsStore>,
}

impl Snippets {
    pub fn new(store: Arc<dyn SnippetsStore>) -> Snippets {
        Snippets { store }
    }

    /// Every snippet kept, in the order of their triggers.
    pub fn listed(&self) -> Result<Value, Failure> {
        Ok(listed(&clean(self.store.read()?.as_ref())))
    }

    /// A change /api/snippets asked for (`verb` save or delete, `trigger`, `text`), and the snippets after it.
    pub fn post(&self, asked: &Map<String, Value>) -> Result<Value, Failure> {
        let saving = match asked.get("verb").and_then(Value::as_str) {
            Some("save") => true,
            Some("delete") => false,
            _ => return Err(Problem::refused("verb must be save|delete").into()),
        };
        let trigger = asked.get("trigger");
        let words = asked.get("text").and_then(Value::as_str);
        let mut answer: Result<Vec<Snippet>, Problem> = Ok(Vec::new());
        self.store.change(&mut |raw| {
            let now = clean(raw.as_ref());
            let after = if saving {
                match saved(&now, trigger, words) {
                    Ok(after) => Some(after),
                    Err(refused) => {
                        answer = Err(refused);
                        return None;
                    }
                }
            } else {
                without(&now, trigger)
            };
            let kept = after.as_ref().map(|after| Value::Array(after.iter().map(Snippet::to_json).collect()));
            answer = Ok(after.unwrap_or(now));
            kept
        })?;
        Ok(listed(&answer?))
    }
}

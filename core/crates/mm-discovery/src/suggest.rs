//! What the search box offers as you type (`mailmind.discovery.domain.suggest` and `application.suggest`): the word
//! being typed, finished from the grammar and from who has written to you. Local SQL; the model is never asked.

use std::sync::Arc;

use mm_kernel::errors::Failure;
use serde_json::{Value, json};

use crate::ports::ContactMail;
use crate::query::{HAS_OFFERED, IS_OFFERED, MAX_CHARS, OPERATORS, is_space};
use crate::search::cut;
use crate::text::lower;

const PEOPLE: [&str; 3] = ["from", "to", "cc"];
/// Characters of a bare word before who has written is worth reading through.
const NOT_YET: usize = 2;
const NEEDLE: usize = 64;
/// How many of each a popover has room for.
const SENDERS: usize = 5;
const DOMAINS: usize = 3;

/// The word still being typed.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Typing {
    /// The box before the word, as it was typed.
    pub settled: String,
    /// "-" where the word negates.
    pub minus: &'static str,
    /// The operator it has got as far as naming.
    pub name: Option<String>,
    /// What follows the operator's colon, else the whole word.
    pub value: String,
}

/// The word still being typed, or None with nothing to finish: an empty box, a space just typed, a question or a
/// command, an open quote, or a word with a colon the grammar doesn't read.
pub fn typing(q: Option<&str>) -> Option<Typing> {
    let text = q.unwrap_or_default().trim_start_matches(is_space);
    let chars: Vec<char> = text.chars().collect();
    let (first, last) = (*chars.first()?, *chars.last()?);
    if chars.len() > MAX_CHARS || first == '?' || first == '!' || is_space(last) {
        return None;
    }
    let mut start = 0;
    let mut quoted = false;
    for (i, &c) in chars.iter().enumerate() {
        if c == '"' {
            quoted = !quoted;
        } else if is_space(c) && !quoted {
            start = i + 1;
        }
    }
    let word: String = chars[start..].iter().collect();
    if word.contains('"') || word.contains('\'') {
        return None;
    }
    let minus = if word.starts_with('-') && word.chars().count() > 1 { "-" } else { "" };
    let rest = &word[minus.len()..];
    let settled: String = chars[..start].iter().collect();
    match rest.split_once(':') {
        Some((name, value)) => {
            let name = lower(name);
            OPERATORS.contains(&name.as_str()).then(|| Typing {
                settled,
                minus,
                name: Some(name),
                value: value.to_owned(),
            })
        }
        None => Some(Typing { settled, minus, name: None, value: rest.to_owned() }),
    }
}

/// What to read through who has written for, or None where they have no part to play.
pub fn needle(t: &Typing) -> Option<String> {
    let person = t.name.as_deref().is_some_and(|n| PEOPLE.contains(&n));
    (person || (t.name.is_none() && t.value.chars().count() >= NOT_YET))
        .then(|| cut(&lower(mm_kernel::text::strip(&t.value)), NEEDLE))
}

fn offer(t: &Typing, finished: &str, label: &str, detail: &str, complete: bool) -> Value {
    let value = format!("{}{}{}{}", t.settled, t.minus, finished, if complete { " " } else { "" });
    json!({"value": value, "label": label, "detail": detail, "complete": complete})
}

fn values(t: &Typing, name: &str, values: &[&str]) -> Vec<Value> {
    let typed = lower(&t.value);
    values
        .iter()
        .filter(|v| v.starts_with(typed.as_str()))
        .map(|v| {
            let said = format!("{name}:{v}");
            offer(t, &said, &said, "", true)
        })
        .collect()
}

/// What a query can hold without quoting it: a sender's address is a stranger's text.
fn unquoted(text: &str) -> bool {
    !text.is_empty() && !text.chars().any(|c| is_space(c) || c == '"' || c == '\'')
}

/// The suggestions for this word, each with `value`, the whole box as it reads with this one taken, and `complete`,
/// whether that can be searched as it stands or an operator is waiting for its value.
pub fn offers(t: &Typing, senders: &[(Option<String>, Option<String>)], domains: &[Option<String>]) -> Vec<Value> {
    match t.name.as_deref() {
        Some("is") => return values(t, "is", &IS_OFFERED),
        Some("has") => return values(t, "has", &HAS_OFFERED),
        _ => {}
    }
    let mut out = Vec::new();
    if t.name.is_none() {
        let typed = lower(&t.value);
        out.extend(OPERATORS.iter().filter(|n| n.starts_with(typed.as_str())).map(|n| {
            let said = format!("{n}:");
            offer(t, &said, &said, "", false)
        }));
    }
    let field = t.name.as_deref().unwrap_or("from");
    for (addr, name) in senders.iter().take(SENDERS) {
        let addr = addr.as_deref().unwrap_or_default();
        if unquoted(addr) {
            let name = name.as_deref().filter(|n| !n.is_empty());
            out.push(offer(
                t,
                &format!("{field}:{addr}"),
                name.unwrap_or(addr),
                if name.is_some() { addr } else { "" },
                true,
            ));
        }
    }
    for domain in domains.iter().take(DOMAINS) {
        let domain = domain.as_deref().unwrap_or_default();
        if unquoted(domain) {
            out.push(offer(t, &format!("{field}:{domain}"), domain, "everyone at this domain", true));
        }
    }
    out
}

#[derive(Debug)]
pub struct Suggesting {
    mail: Arc<dyn ContactMail>,
}

impl Suggesting {
    pub fn new(mail: Arc<dyn ContactMail>) -> Suggesting {
        Suggesting { mail }
    }

    /// The suggestions for the box as it reads now.
    pub fn answer(&self, q: Option<&str>) -> Result<Value, Failure> {
        let Some(t) = typing(q) else {
            return Ok(json!({"ok": true, "items": []}));
        };
        let (senders, domains) = match needle(&t) {
            Some(needle) => (
                self.mail.senders_like(&needle)?.into_iter().map(|s| (s.addr, s.name)).collect(),
                self.mail.sender_domains_like(&needle)?.into_iter().map(|(d, _)| d).collect(),
            ),
            None => (Vec::new(), Vec::new()),
        };
        Ok(json!({"ok": true, "items": offers(&t, &senders, &domains)}))
    }
}

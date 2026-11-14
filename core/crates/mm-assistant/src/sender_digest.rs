//! What one sender has been writing about (`domain.sender_digest` and `application.sender_digest`): a call over their
//! recent threads, on a person's click, from what is stored of them. No body is fetched and nothing is kept.

use std::sync::Arc;

use mm_kernel::errors::Problem;
use serde_json::{Map, Value, json};

use crate::calls::Calls;
use crate::ports::{SenderRecent, Senders};
use crate::prompts;
use crate::refusals::{AiFailure, answered};
use crate::text::{cut, length, python_str, strip, truthy};

/// How many of a sender's newest messages are read.
pub const RECENT: i64 = 24;
/// How many of their threads, the newest.
pub const THREADS: usize = 8;
/// Of everything the model is told.
pub const CAP_TOTAL: usize = 6000;
pub const SUMMARY_MAX: usize = 700;
pub const TOPICS_MAX: usize = 4;
pub const TOPIC_MAX: usize = 60;
pub const OPEN_MAX: usize = 200;
/// Of one thread's line: a subject is the sender's to make long.
const LINE_MAX: usize = 400;
const ADDR_MAX: usize = 320;

pub fn schema() -> Value {
    json!({"type": "object", "properties": {"summary": {"type": "string"},
           "topics": {"type": "array", "items": {"type": "string"}}, "open": {"type": "string"}},
           "required": ["summary", "topics", "open"]})
}

/// One of a sender's threads as the model is told of it.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Thread {
    pub subject: String,
    pub messages: usize,
    pub last: String,
    pub gist: String,
}

/// What is taken from an answer.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Said {
    pub summary: String,
    pub topics: Vec<String>,
    pub open: String,
}

/// The sender a request names, as rows go by them, or None if that's no address.
pub fn address(raw: Option<&str>) -> Option<String> {
    let addr = strip(raw?).to_lowercase();
    (addr.contains('@') && length(&addr) <= ADDR_MAX).then_some(addr)
}

/// A sender's mail, newest first, as its threads, the newest first: one with no thread is its own, by its subject.
pub fn threads_of(rows: &[SenderRecent]) -> Vec<Thread> {
    let mut grouped: Vec<((&str, String), Thread)> = Vec::new();
    for r in rows {
        let thread = match r.thread_id.as_deref().filter(|t| !t.is_empty()) {
            Some(id) => id.to_owned(),
            None => format!("lone:{}", r.subject.as_deref().unwrap_or("None")),
        };
        let key = (r.account.as_str(), thread);
        match grouped.iter_mut().find(|(k, _)| *k == key) {
            Some((_, held)) => held.messages += 1,
            None => {
                let said = |s: &Option<String>| s.clone().filter(|s| !s.is_empty());
                let held = Thread {
                    subject: said(&r.subject).unwrap_or_else(|| "(no subject)".into()),
                    messages: 1,
                    last: cut(r.date_iso.as_deref().unwrap_or_default(), 10).to_owned(),
                    gist: said(&r.summary).or_else(|| said(&r.snippet)).unwrap_or_default(),
                };
                grouped.push((key, held));
            }
        }
    }
    grouped.into_iter().take(THREADS).map(|(_, t)| t).collect()
}

/// The threads as the model is told them, within what it may be told.
pub fn prompt_lines(threads: &[Thread]) -> String {
    let mut out = Vec::new();
    let mut total = 0;
    for t in threads {
        let s = if t.messages == 1 { "" } else { "s" };
        let mut line = format!("- {} ({} message{s}, latest {})", t.subject, t.messages, t.last);
        if !t.gist.is_empty() {
            line.push_str(": ");
            line.push_str(&t.gist);
        }
        let line = cut(&line, LINE_MAX);
        if total + length(line) > CAP_TOTAL {
            break;
        }
        total += length(line) + 1;
        out.push(line.to_owned());
    }
    out.join("\n")
}

/// `str(obj.get(name) or "").strip()[:most]`.
fn text_of(answer: &Map<String, Value>, name: &str, most: usize) -> String {
    let said = answer.get(name).filter(|v| truthy(v)).map(python_str).unwrap_or_default();
    cut(strip(&said), most).to_owned()
}

pub fn clean(answer: &Map<String, Value>) -> Said {
    let told = match answer.get("topics") {
        Some(Value::Array(items)) => items.as_slice(),
        _ => &[],
    };
    let topics = told
        .iter()
        .filter_map(Value::as_str)
        .filter(|t| !strip(t).is_empty())
        .map(|t| cut(strip(t), TOPIC_MAX).to_owned())
        .take(TOPICS_MAX)
        .collect();
    Said { summary: text_of(answer, "summary", SUMMARY_MAX), topics, open: text_of(answer, "open", OPEN_MAX) }
}

/// For an answer with nothing in it: what the mail itself says.
pub fn fallback(threads: &[Thread]) -> Said {
    let n = threads.len();
    let s = if n == 1 { "" } else { "s" };
    let latest = threads.first().map_or("", |t| t.subject.as_str());
    Said { summary: format!("{n} recent thread{s}, the latest about {latest}."), ..Said::default() }
}

#[derive(Debug)]
pub struct SenderDigest {
    calls: Arc<Calls>,
    senders: Arc<dyn Senders>,
}

impl SenderDigest {
    pub fn new(calls: Arc<Calls>, senders: Arc<dyn Senders>) -> SenderDigest {
        SenderDigest { calls, senders }
    }

    /// `POST /api/contact/summary`: a sender's recent threads, in a few sentences. `bad address` for an address that
    /// won't read, and not found for a sender with no mail stored.
    pub fn summarize(&self, raw: Option<&str>) -> Result<Value, AiFailure> {
        let addr = address(raw).ok_or_else(|| Problem::refused("bad address"))?;
        let rows = self.senders.senders_recent(&[addr.as_str()], RECENT)?.into_iter().next().unwrap_or_default();
        let Some(first) = rows.first() else { return Err(Problem::not_found("no mail from this sender").into()) };
        let threads = threads_of(&rows);
        let name = first.from_name.as_deref().filter(|n| !n.is_empty()).unwrap_or(&addr);
        let who = format!("{name} <{addr}>");
        let n = threads.len().to_string();
        let prompt = prompts::SENDER.fill(&[("who", &who), ("n", &n), ("threads", &prompt_lines(&threads))]);
        let made = self.calls.call_claude_json(&prompt, &schema(), None, "sender");
        let answer = answered(made, "sender summary failed", "summary unavailable")?;
        let mut said = clean(&answer);
        if said.summary.is_empty() {
            said = fallback(&threads);
        }
        Ok(json!({"ok": true, "addr": addr, "threads": threads.len(), "summary": said.summary, "topics": said.topics,
                  "open": said.open}))
    }
}

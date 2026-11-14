//! Summarizing and scoring mail that isn't junk (`application.assessment`): a few calls in parallel for what's new,
//! each its message's triage under a schema, and what it said kept under the message's key.
//!
//! The Python app sends a backlog as a batch at half price. The Rust server keeps no batches yet: it triages every
//! message as Python's does with MAILMIND_NO_BATCHES on, one call each, and leaves alone what a batch the Python app
//! sent still holds.

use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex, PoisonError};

use mm_kernel::errors::Fault;
use mm_kernel::log::{self, Field};
use mm_kernel::seams::Clock;
use serde_json::{Map, Value, json};

use crate::background_ports::{Assessment, Outcome, Pending, Triage};
use crate::calls::Calls;
use crate::ports::Reply;
use crate::prompts;
use crate::schema::object_in;
use crate::text::{cut, python_int, python_str, strip, truthy};

const TAG: &str = "assess";
/// Calls at a time.
pub const WORKERS: usize = 4;
/// Messages triaged in one round.
pub const ASSESS_BATCH: usize = 300;
/// Seconds a round waits while the person is using AI.
pub const ASSESS_YIELD: i64 = 45;
/// Triage runs on every new message and is a short classify-and-summarize.
pub const EFFORT: &str = "low";
/// What a summary, a category and a next step are cut to.
const SUMMARY_MAX: usize = 600;
const CATEGORY_MAX: usize = 32;
const ACTION_MAX: usize = 200;
/// Of a snippet, what the prompt shows.
const BODY_MAX: usize = 1800;
/// An importance that won't read as a number.
pub const IMPORTANCE_UNREADABLE: i64 = 2;

/// Structured output returns an object that matches it, so a triage reply needs no scraping.
pub fn triage_schema() -> Value {
    json!({
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "importance": {"type": "integer", "minimum": 1, "maximum": 5},
            "category": {"type": "string",
                         "enum": ["action-needed", "personal", "work-job", "finance", "calendar", "info", "other"]},
            "action": {"type": "string"},
        },
        "required": ["summary", "importance", "category"],
    })
}

/// An importance as 1 to 5, whatever it was told as (`kernel.assessment.importance_of`): `int()` of it held to that
/// range, and 2 where `int()` would raise.
pub fn importance_of(told: Option<&Value>) -> i64 {
    let Some(told) = told else { return IMPORTANCE_UNREADABLE };
    if let Some(n) = python_int(told) {
        return n.clamp(1, 5);
    }
    // A whole number too long for 64 bits is still one to Python.
    if let Value::String(s) = told {
        let s = strip(s);
        let digits = s.strip_prefix(['+', '-']).unwrap_or(s);
        if !digits.is_empty() && digits.bytes().all(|b| b.is_ascii_digit()) {
            return if s.starts_with('-') { 1 } else { 5 };
        }
    }
    IMPORTANCE_UNREADABLE
}

/// What a message is shown to the model as.
pub fn triage_prompt(row: &Pending) -> String {
    let addr = row.from_addr.as_deref().unwrap_or("None");
    let frm = match row.from_name.as_deref().filter(|n| !n.is_empty()) {
        Some(name) => format!("{name} <{addr}>"),
        None => addr.to_owned(),
    };
    let subject = row.subject.as_deref().filter(|s| !s.is_empty()).unwrap_or("(no subject)");
    let body = cut(row.snippet.as_deref().unwrap_or_default(), BODY_MAX);
    prompts::TRIAGE.fill(&[
        ("frm", &frm),
        ("subject", subject),
        ("date", row.date_iso.as_deref().unwrap_or_default()),
        ("body", body),
    ])
}

/// `str(value)` of a member of the reply's object, or `default` where it has none.
fn member(obj: &Map<String, Value>, name: &str, default: &str) -> String {
    obj.get(name).map_or_else(|| default.to_owned(), python_str)
}

/// What to keep for a message from what triage answered. A reply with no usable summary keeps a stand-in, so the
/// message doesn't go round for ever.
pub fn assessment_of(row: &Pending, reply: &Reply, model_label: &str, prompt_version: u32) -> Assessment {
    let obj = object_in(&reply.text);
    if obj.get("summary").is_some_and(truthy) {
        return Assessment {
            summary: cut(&member(&obj, "summary", ""), SUMMARY_MAX).to_owned(),
            importance: importance_of(Some(obj.get("importance").unwrap_or(&json!(2)))),
            category: cut(&member(&obj, "category", "other"), CATEGORY_MAX).to_owned(),
            action: cut(&member(&obj, "action", ""), ACTION_MAX).to_owned(),
            model: model_label.to_owned(),
            served_by: Some(reply.model.clone()),
            prompt_version: Some(prompt_version),
            outcome: Some(Outcome::Assessed),
        };
    }
    Assessment {
        summary: row.subject.clone().filter(|s| !s.is_empty()).unwrap_or_else(|| "(no subject)".to_owned()),
        importance: IMPORTANCE_UNREADABLE,
        category: "other".to_owned(),
        action: String::new(),
        model: "parse-error".to_owned(),
        served_by: Some(reply.model.clone()),
        prompt_version: Some(prompt_version),
        outcome: Some(if reply.refused() { Outcome::Refused } else { Outcome::Unusable }),
    }
}

#[derive(Debug)]
pub struct Assessments {
    calls: Arc<Calls>,
    triage: Arc<dyn Triage>,
    clock: Arc<dyn Clock>,
}

impl Assessments {
    pub fn new(calls: Arc<Calls>, triage: Arc<dyn Triage>, clock: Arc<dyn Clock>) -> Assessments {
        Assessments { calls, triage, clock }
    }

    /// Why triage would be held back now, or None when it would go.
    fn held_back(&self) -> Option<&'static str> {
        self.calls.gateway().held_back("triage", Some(&self.calls.model_label()))
    }

    /// One message triaged, or None when no reply came: it waits for the next round.
    pub fn summarize_one(&self, row: &Pending) -> Option<Assessment> {
        let reply = self.calls.reply_to(&triage_prompt(row), &triage_schema(), Some(EFFORT), "triage").ok()?;
        Some(assessment_of(row, &reply, &self.calls.model_label(), prompts::TRIAGE.version))
    }

    /// Triage up to `limit` messages that have no summary yet, `workers` at a time, and say how many were summarized.
    /// With AI off, or while the cost governor holds triage back, not one message is tried. A message whose call
    /// failed waits for the next round; one that can't be kept stops the rest, and the round fails.
    pub fn summarize_pending(&self, limit: usize, workers: usize) -> Result<usize, Fault> {
        if self.calls.engine() == "none" || self.held_back().is_some() {
            return Ok(0);
        }
        let newer_than = self.triage.batch_cutoff()?;
        let rows = self.triage.needs_summary(i64::try_from(limit).unwrap_or(i64::MAX), newer_than)?;
        if rows.is_empty() {
            return Ok(0);
        }
        let next = AtomicUsize::new(0);
        let done = AtomicUsize::new(0);
        let stopped = AtomicBool::new(false);
        let failed: Mutex<Option<Fault>> = Mutex::new(None);
        std::thread::scope(|scope| {
            for _ in 0..workers.clamp(1, rows.len()) {
                scope.spawn(|| {
                    while !stopped.load(Ordering::SeqCst) {
                        let Some(row) = rows.get(next.fetch_add(1, Ordering::SeqCst)) else { break };
                        let Some(said) = self.summarize_one(row) else { continue };
                        let when = mm_kernel::when::iso(self.clock.now());
                        match self.triage.save_summary(row.pk, &said, &when) {
                            Ok(()) => {
                                done.fetch_add(1, Ordering::SeqCst);
                            }
                            Err(fault) => {
                                stopped.store(true, Ordering::SeqCst);
                                failed.lock().unwrap_or_else(PoisonError::into_inner).get_or_insert(fault);
                            }
                        }
                    }
                });
            }
        });
        match failed.into_inner().unwrap_or_else(PoisonError::into_inner) {
            Some(fault) => Err(fault),
            None => Ok(done.into_inner()),
        }
    }

    /// The assess job's round: triage up to ASSESS_BATCH messages that have no summary yet. A full round means more
    /// are waiting, so the next follows at once (Some(0)). While the person is using AI it waits until it has yielded
    /// long enough; held back otherwise (the limit, the breaker), it waits its interval (None).
    pub fn round(&self) -> Result<Option<i64>, Fault> {
        if let Some(why) = self.held_back() {
            return Ok((why == "yielding").then_some(ASSESS_YIELD));
        }
        let done = self.summarize_pending(ASSESS_BATCH, WORKERS)?;
        if done > 0 {
            log::info(TAG, "%(count)s summarized", &[(Field::Count, &done.to_string())]);
        }
        Ok((done >= ASSESS_BATCH).then_some(0))
    }
}

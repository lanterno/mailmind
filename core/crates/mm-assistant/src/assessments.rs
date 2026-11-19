//! Summarizing and scoring mail that isn't junk (`application.assessment`): a few calls in parallel for what's new,
//! each its message's triage under a schema, and what it said kept under the message's key.
//!
//! A backlog goes as a batch at half price (`batches`), from the same round: what a batch holds, and while one is out
//! everything older than the newest it took, is left to it.
//!
//! Only mail from the last `RECENT_DAYS` is summarized in the background: older mail is summarized only when a person
//! asks for a summary, so turning AI on over a mailbox of years spends nothing on its past. Background summaries ask
//! the provider's small model (`Calls::background_label`), and each is stored under it.

use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex, PoisonError};

use mm_kernel::errors::Fault;
use mm_kernel::events::{Events, Topic};
use mm_kernel::log::{self, Field};
use mm_kernel::seams::Clock;
use serde_json::{Map, Value, json};

use crate::background_ports::{Assessment, Outcome, Pending, Triage};
use crate::batches::{self, Batching};
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
/// How far back the background summarizes, in days.
pub const RECENT_DAYS: i64 = 30;

/// The moment the background's reach starts, `RECENT_DAYS` before `now` in whole seconds: mail from before it waits for
/// a person to ask for its summary.
pub fn recent(now: i64) -> i64 {
    now - RECENT_DAYS * 86_400
}

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
    assessment_with(row, reply, model_label, Some(prompt_version))
}

/// The same, for a reply to a prompt whose version may not be known: a batch sent before migration 43 kept none.
pub fn assessment_with(row: &Pending, reply: &Reply, model_label: &str, prompt_version: Option<u32>) -> Assessment {
    let obj = object_in(&reply.text);
    if obj.get("summary").is_some_and(truthy) {
        return Assessment {
            summary: cut(&member(&obj, "summary", ""), SUMMARY_MAX).to_owned(),
            importance: importance_of(Some(obj.get("importance").unwrap_or(&json!(2)))),
            category: cut(&member(&obj, "category", "other"), CATEGORY_MAX).to_owned(),
            action: cut(&member(&obj, "action", ""), ACTION_MAX).to_owned(),
            model: model_label.to_owned(),
            served_by: Some(reply.model.clone()),
            prompt_version,
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
        prompt_version,
        outcome: Some(if reply.refused() { Outcome::Refused } else { Outcome::Unusable }),
    }
}

#[derive(Debug)]
pub struct Assessments {
    calls: Arc<Calls>,
    triage: Arc<dyn Triage>,
    clock: Arc<dyn Clock>,
    /// The backlog's batches, and whether the round sends one (MAILMIND_NO_BATCHES off).
    batches: Option<(Arc<Batching>, bool)>,
    /// The open tabs, told when a round's summaries land.
    events: Option<Arc<dyn Events>>,
}

impl Assessments {
    pub fn new(calls: Arc<Calls>, triage: Arc<dyn Triage>, clock: Arc<dyn Clock>) -> Assessments {
        Assessments { calls, triage, clock, batches: None, events: None }
    }

    /// The same, with a round that brings in a batch's answers and, when `send`, sends a backlog as a batch.
    pub fn with_batches(mut self, batching: Arc<Batching>, send: bool) -> Assessments {
        self.batches = Some((batching, send));
        self
    }

    /// The same, with a round that tells the open tabs how many messages it summarized, whenever it summarized any.
    pub fn telling(mut self, events: Arc<dyn Events>) -> Assessments {
        self.events = Some(events);
        self
    }

    /// What is out in a batch, with no call made (`batch_status`): nothing, for triage that sends none.
    pub fn batch_status(&self) -> Result<batches::BatchStatus, Fault> {
        match &self.batches {
            Some((batching, _)) => batching.status(),
            None => Ok(batches::BatchStatus { waiting: 0, state: None, estimate_micros: 0 }),
        }
    }

    /// Why triage would be held back now, or None when it would go.
    fn held_back(&self) -> Option<&'static str> {
        self.calls.gateway().held_back("triage", Some(&self.calls.background_label()))
    }

    /// One message triaged by the background's model, or None when no reply came: it waits for the next round.
    pub fn summarize_one(&self, row: &Pending) -> Option<Assessment> {
        self.summarize_with(row, &self.calls.background_label())
    }

    /// One message triaged by `model`, and kept under its name.
    fn summarize_with(&self, row: &Pending, model: &str) -> Option<Assessment> {
        let reply = self.calls.reply_from(&triage_prompt(row), &triage_schema(), Some(EFFORT), "triage", model).ok()?;
        Some(assessment_of(row, &reply, model, prompts::TRIAGE.version))
    }

    /// Triage up to `limit` messages from the last `RECENT_DAYS` that have no summary yet, `workers` at a time, and say
    /// how many were summarized. With AI off, or while the cost governor holds triage back, not one message is tried.
    /// A message whose call failed waits for the next round; one that can't be kept stops the rest, and the round
    /// fails.
    pub fn summarize_pending(&self, limit: usize, workers: usize) -> Result<usize, Fault> {
        if self.calls.engine() == "none" || self.held_back().is_some() {
            return Ok(0);
        }
        let model = self.calls.background_label();
        let recent = recent(self.clock.now().whole_seconds());
        let newer_than = self.triage.batch_cutoff()?.map_or(recent, |cutoff| cutoff.max(recent));
        let rows = self.triage.needs_summary(i64::try_from(limit).unwrap_or(i64::MAX), Some(newer_than))?;
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
                        let Some(said) = self.summarize_with(row, &model) else { continue };
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
    /// long enough; held back otherwise (the limit, the breaker), it waits its interval (None). A batch's answers are
    /// brought in first, whatever holds the rest back, and a backlog goes out in the next batch rather than through
    /// this round's calls.
    ///
    /// Summaries change what needs you and what can wait with no mail changing, so a round that kept any tells the
    /// open tabs how many (`summarized`), a batch's answers among them, even when what follows them holds the round
    /// back or fails: the lists, the Inbox's tabs, the rail and the brief read them again.
    pub fn round(&self) -> Result<Option<i64>, Fault> {
        let mut landed = 0;
        let next = self.round_counting(&mut landed);
        if let (Some(events), true) = (&self.events, landed > 0) {
            events.publish(Topic::Summarized, json!({"count": landed}));
        }
        next
    }

    /// The round, adding to `landed` each summary it keeps as it keeps it.
    fn round_counting(&self, landed: &mut usize) -> Result<Option<i64>, Fault> {
        if let Some((batching, _)) = &self.batches {
            *landed += batching.collect()?;
        }
        if let Some(why) = self.held_back() {
            return Ok((why == "yielding").then_some(ASSESS_YIELD));
        }
        if let Some((batching, true)) = &self.batches {
            batching.submit(batches::MIN, batches::KEEP_NEWEST)?;
        }
        let done = self.summarize_pending(ASSESS_BATCH, WORKERS)?;
        *landed += done;
        if done > 0 {
            log::info(TAG, "%(count)s summarized", &[(Field::Count, &done.to_string())]);
        }
        Ok((done >= ASSESS_BATCH).then_some(0))
    }
}

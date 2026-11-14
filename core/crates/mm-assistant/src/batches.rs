//! A backlog of triage through the Claude API's Message Batches, at half the price (`application.assessment`'s
//! `batch_submit`, `batch_collect` and `batch_status`), and the Assistant's part of the forget round
//! (`answers.batch_forget_finished`).
//!
//! A backlog (a first import, or after an outage) goes as one batch, one at a time, and new mail stays synchronous: the
//! newest of a backlog are left to triage now, so the brief has something to say meanwhile. A batch takes what fits in
//! its share of what today's limit leaves, by an estimate that errs high, and books that against background work while
//! it is out. Its row is committed as `submitting` before it is sent, and sending is never retried. Its answers are
//! brought in once it has ended, whatever holds the rest back, since they are paid for; then the API is asked to let
//! it go, as it holds mail. A batch the Python app sent is in the same rows, and is brought in the same way.

use std::sync::Arc;

use mm_kernel::errors::Fault;
use mm_kernel::log::{self, Field};
use mm_kernel::seams::Clock;

use crate::assessments::{EFFORT, assessment_with, triage_prompt, triage_schema};
use crate::background_ports::{BatchBook, Begun, FinishedBatches, Pending, Triage};
use crate::calls::Calls;
use crate::ports::{BatchResult, Reply};
use crate::pricing;
use crate::prompts::{SYSTEM, TRIAGE};
use crate::refusals::CallError;

const TAG: &str = "assess";
/// The feature a batch's requests are counted under.
pub const FEATURE: &str = "triage-batch";
/// This many waiting is a backlog.
pub const MIN: usize = 100;
/// Requests in one batch.
pub const MAX: usize = 2000;
/// Of a backlog, triaged now.
pub const KEEP_NEWEST: usize = 20;
/// Seconds between asking whether a batch has ended.
pub const POLL: i64 = 300;
/// A batch books at most 1/this of what today's limit leaves, the rest for whoever asks.
pub const SHARE: i64 = 2;
/// Seconds: a batch still being sent after this was cut off by a crash.
pub const SEND_GIVE_UP: i64 = 900;
/// How long a finished batch is kept: thirty days.
pub const KEPT_FOR: i64 = 30 * 86_400;

/// Let go of the batches that finished more than `KEPT_FOR` ago, timed in the clock's whole seconds as Python's
/// `int(time.time())` is: how many went.
pub fn forget_finished(store: &dyn FinishedBatches, clock: &dyn Clock) -> Result<usize, Fault> {
    store.forget_finished(clock.now().whole_seconds() - KEPT_FOR)
}

/// What is out in a batch, with no call made (`BatchStatus`).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct BatchStatus {
    /// How many messages.
    pub waiting: i64,
    /// `submitting` or `running`; None with none out.
    pub state: Option<String>,
    /// What it may cost, in micro-dollars.
    pub estimate_micros: i64,
}

/// The message key a request's custom id names: its digits, as `str.isdigit` and `int` read them for ASCII.
fn key_of(custom_id: &str) -> Option<i64> {
    if custom_id.is_empty() || !custom_id.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    custom_id.parse().ok()
}

/// Characters, as Python's `len` counts a string.
fn chars(text: &str) -> i64 {
    i64::try_from(text.chars().count()).unwrap_or(i64::MAX)
}

#[derive(Debug)]
pub struct Batching {
    calls: Arc<Calls>,
    triage: Arc<dyn Triage>,
    book: Arc<dyn BatchBook>,
    clock: Arc<dyn Clock>,
}

impl Batching {
    pub fn new(
        calls: Arc<Calls>,
        triage: Arc<dyn Triage>,
        book: Arc<dyn BatchBook>,
        clock: Arc<dyn Clock>,
    ) -> Batching {
        Batching { calls, triage, book, clock }
    }

    fn now(&self) -> i64 {
        self.clock.now().whole_seconds()
    }

    /// When a backlog of `at_least` is waiting and no batch is out, send it as one, leaving the `keep` newest to go
    /// now, and say how many it took: what fits in its share of today's limit, by estimate. With AI off, or while
    /// the cost governor holds a batch back, nothing goes. A batch that wasn't sent gives its messages back, and the
    /// next round tries again.
    pub fn submit(&self, at_least: usize, keep: usize) -> Result<usize, Fault> {
        let label = self.calls.model_label();
        let gateway = self.calls.gateway();
        if self.calls.engine() == "none" || gateway.held_back(FEATURE, Some(&label)).is_some() {
            return Ok(0);
        }
        if self.book.open()?.is_some() {
            return Ok(0);
        }
        let wanted = i64::try_from(keep.saturating_add(MAX)).unwrap_or(i64::MAX);
        let rows: Vec<Pending> =
            self.triage.needs_summary(wanted, None)?.into_iter().filter(|r| r.message_key.is_some()).collect();
        if rows.len() < at_least {
            return Ok(0);
        }
        // What background work may spend, so a batch keeps within the limit.
        let share = gateway.room().div_euclid(SHARE);
        let (mut requests, mut keys) = (Vec::new(), Vec::new());
        let (mut estimate, mut cutoff) = (0, 0);
        for row in rows.iter().skip(keep) {
            let Some(key) = row.message_key else { continue };
            let text = triage_prompt(row);
            let cost = pricing::batch_estimate(&label, chars(SYSTEM) + chars(&text));
            if estimate + cost > share {
                break;
            }
            requests.push((key.to_string(), text));
            keys.push(key);
            estimate += cost;
            cutoff = cutoff.max(row.ts.unwrap_or(0));
        }
        if requests.is_empty() {
            return Ok(0);
        }
        let begun = Begun {
            feature: FEATURE,
            model: &label,
            keys: &keys,
            estimate_micros: estimate,
            cutoff_ts: cutoff,
            now: self.now(),
            prompt_version: TRIAGE.version,
        };
        // Committed before the network: sending it is a call.
        let Some(id) = self.book.begin(&begun)? else { return Ok(0) };
        let count = requests.len().to_string();
        match self.calls.submit_batch(&requests, &triage_schema(), Some(EFFORT), FEATURE) {
            Ok(remote_id) => {
                self.book.sent(id, &remote_id, self.now())?;
                log::info(TAG, "sent a batch of %(count)s", &[(Field::Count, &count)]);
                Ok(requests.len())
            }
            Err(error) => {
                // It didn't go, so they wait; the next round tries again.
                self.book.drop_batch(id)?;
                let class = match error {
                    CallError::NotSent(_) => "NotSent",
                    CallError::Model(_) => "ModelError",
                };
                log::info(
                    TAG,
                    "a batch of %(count)s wasn't sent: %(why)s",
                    &[(Field::Count, &count), (Field::Why, class)],
                );
                Ok(0)
            }
        }
    }

    /// Bring in the answers of a batch that has ended, asking at most every `POLL` seconds, and say how many it
    /// stored. Nothing here sends a message, so neither the limit nor someone using AI holds it back: these answers
    /// are paid for. A batch cut off while it was being sent gives its messages back, as one the API no longer has
    /// does; one that couldn't be read is asked about again later. A batch whose answers are in is removed from the
    /// API, tried again each round until it goes.
    pub fn collect(&self) -> Result<usize, Fault> {
        if self.calls.engine() == "none" {
            return Ok(0);
        }
        let gateway = self.calls.gateway();
        let now = self.now();
        let mut stored = 0;
        for cut_off in self.book.in_state("submitting")?.into_iter().filter(|b| b.created_at < now - SEND_GIVE_UP) {
            self.book.drop_batch(cut_off.id)?;
            log::info(TAG, "a batch was cut off while it was being sent; its messages are waiting again", &[]);
        }
        if let Some(out) = self.book.open()?
            && out.state == "running"
            && out.polled_at + POLL <= now
        {
            let remote_id = out.remote_id.clone().unwrap_or_default();
            let read = gateway.batch_ended(&remote_id).and_then(|ended| match ended {
                true => gateway.batch_results(&remote_id, &out.feature, Some(&out.model)).map(Some),
                false => Ok(None),
            });
            match read {
                Err(CallError::NotSent(_)) => return Ok(stored),
                Err(CallError::Model(error)) if error.status == Some(404) => {
                    // Another key's, or removed: nothing more will come of it.
                    self.book.drop_batch(out.id)?;
                    log::info(TAG, "the API no longer has a batch; its messages are waiting again", &[]);
                }
                Err(CallError::Model(error)) => {
                    self.book.polled(out.id, now)?;
                    let status = error.status.map(|s| s.to_string()).unwrap_or_else(|| "None".to_owned());
                    log::info(TAG, "couldn't read a batch: ModelError %(why)s", &[(Field::Why, &status)]);
                }
                Ok(None) => self.book.polled(out.id, now)?,
                Ok(Some(results)) => {
                    stored = self.store(out.id, out.prompt_version, results, now)?;
                    log::info(TAG, "a batch brought %(count)s answers", &[(Field::Count, &stored.to_string())]);
                }
            }
        }
        for done in self.book.in_state("collected")? {
            // What it holds is mail: it stays at the API no longer than needed.
            match gateway.discard_batch(done.remote_id.as_deref().unwrap_or_default()) {
                Err(CallError::NotSent(_)) => return Ok(stored),
                Err(CallError::Model(error)) if error.status != Some(404) => {
                    let status = error.status.map(|s| s.to_string()).unwrap_or_else(|| "None".to_owned());
                    log::info(TAG, "couldn't remove a batch: ModelError %(why)s", &[(Field::Why, &status)]);
                    continue;
                }
                Ok(()) | Err(CallError::Model(_)) => self.book.discarded(done.id)?,
            }
        }
        Ok(stored)
    }

    /// Each answer a batch brought, under its message's key, and the batch collected: how many were its own.
    fn store(&self, id: i64, version: Option<i64>, results: Vec<BatchResult>, now: i64) -> Result<usize, Fault> {
        // A request with no answer is waiting again once the batch is collected.
        let answers: Vec<(i64, Reply)> =
            results.into_iter().filter_map(|r| Some((key_of(&r.custom_id)?, r.reply?))).collect();
        let label = self.calls.model_label();
        let version = version.and_then(|v| u32::try_from(v).ok());
        let judge = |row: Option<&Pending>, reply: &Reply| {
            assessment_with(row.unwrap_or(&Pending::default()), reply, &label, version)
        };
        let when = mm_kernel::when::iso(self.clock.now());
        self.book.collected(id, now, &when, &answers, &judge)
    }

    /// What is out in a batch, with no call made: how many messages, and what it may cost.
    pub fn status(&self) -> Result<BatchStatus, Fault> {
        let out = self.book.open()?;
        Ok(BatchStatus {
            waiting: self.book.waiting()?,
            state: out.as_ref().map(|b| b.state.clone()),
            estimate_micros: out.map_or(0, |b| b.estimate_micros),
        })
    }
}

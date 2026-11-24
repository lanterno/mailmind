//! How AI stands, with no call made (`application.status`): what the page says of it, and how the most recent call
//! went. With AI off (turned off in Settings, or no key, URL or model) the engine is "none", which is no failure, and
//! `off` says why. A call that was never made (AI off, or held back by the cost governor) isn't one either.

use std::sync::{Arc, Mutex};

use mm_kernel::errors::Fault;
use mm_kernel::log;
use mm_kernel::seams::Clock;
use serde_json::{Map, Value, json};

use crate::assessments::recent;
use crate::background_ports::Backlog;
use crate::gateway::Gateway;
use crate::refusals::CallError;

/// How the most recent call went: `ok` None until one has run.
#[derive(Clone, Debug, Default)]
struct Last {
    ok: Option<bool>,
    ts: i64,
    error: String,
}

#[derive(Debug)]
pub struct Status {
    gateway: Arc<Gateway>,
    clock: Arc<dyn Clock>,
    last: Mutex<Last>,
    /// Where it counts what waits for a summary: nowhere, for a status that says nothing of it.
    backlog: Option<Arc<dyn Backlog>>,
}

impl Status {
    pub fn new(gateway: Arc<Gateway>, clock: Arc<dyn Clock>) -> Status {
        Status { gateway, clock, last: Mutex::new(Last::default()), backlog: None }
    }

    /// The same, saying while AI is on how many messages from the last 30 days wait for a summary, as `backlog`
    /// counts them.
    pub fn with_backlog(mut self, backlog: Arc<dyn Backlog>) -> Status {
        self.backlog = Some(backlog);
        self
    }

    fn note(&self, ok: bool, error: String) {
        let ts = self.clock.now().whole_seconds();
        *self.last.lock().unwrap_or_else(std::sync::PoisonError::into_inner) = Last { ok: Some(ok), ts, error };
    }

    /// `make`, its outcome recorded for `report`, and its result passed through unchanged. A call that was never made
    /// isn't recorded.
    pub fn noting<T>(&self, make: impl FnOnce() -> Result<T, CallError>) -> Result<T, CallError> {
        let made = make();
        match &made {
            Ok(_) => self.note(true, String::new()),
            Err(CallError::Model(error)) => self.note(false, error.plain()),
            Err(CallError::NotSent(_)) => {}
        }
        made
    }

    /// Whether the background reads what waits for a summary, now or at its next round, which a kick brings when mail
    /// comes: neither today's limit nor the breaker holds its summaries back. Someone asking the AI something only
    /// has them wait a moment.
    fn reading(&self) -> bool {
        self.gateway.stopped("triage", Some(&self.gateway.background_model())).is_none()
    }

    /// What the page says of AI, with no call made: the engine ("api", or "none" while AI is off), the provider
    /// chosen and why AI is off (null while it is on), the model in force, whether the model chosen for a server on this
    /// Mac runs in a cloud (`providers::cloud_model`, said on or off), where the key came from, how the most
    /// recent call went, what today's calls cost so far in micro-dollars (null when the ledger can't be read) beside
    /// the daily limit, how many messages a batch out holds, when one does, and while AI is on how many messages from
    /// the last 30 days wait for a summary, and whether they are being read (`summarizing`).
    pub fn report(&self) -> Result<Value, Fault> {
        let in_force = self.gateway.in_force();
        let on = in_force.on();
        let off = in_force.lm.as_ref().err().map(|why| why.id());
        let last = self.last.lock().unwrap_or_else(std::sync::PoisonError::into_inner).clone();
        let spent = match self.gateway.spent_today() {
            Ok(spent) => json!(spent),
            Err(fault) => {
                log::warning("ai", "the usage ledger can't be read", &[], Some(fault.class));
                Value::Null
            }
        };
        let mut report = Map::new();
        report.insert("engine".into(), json!(if on { "api" } else { "none" }));
        report.insert("provider".into(), json!(in_force.provider.id()));
        report.insert("off".into(), json!(off));
        report.insert("model".into(), json!(if on { in_force.model.as_str() } else { "" }));
        report
            .insert("background_model".into(), json!(if on { self.gateway.background_model() } else { String::new() }));
        report.insert(
            "cloud_model".into(),
            json!(crate::providers::cloud_model(in_force.provider, in_force.local, &in_force.model)),
        );
        report.insert("key_from".into(), json!(if on { in_force.key_from } else { "" }));
        report.insert("last_ok".into(), json!(last.ok));
        report.insert("last_ts".into(), json!(last.ts));
        report.insert("last_error".into(), json!(last.error));
        report.insert("spent_today_micros".into(), spent);
        report.insert("limit_micros".into(), json!(self.gateway.governor().limit_micros()));
        if on
            && let Ok(waiting) = self.gateway.in_batches()
            && waiting > 0
        {
            report.insert("batch_waiting".into(), json!(waiting));
        }
        if on && let Some(backlog) = &self.backlog {
            match backlog.summaries_waiting(recent(self.clock.now().whole_seconds())) {
                Ok(waiting) => {
                    report.insert("summaries_waiting".into(), json!(waiting));
                    report.insert("summarizing".into(), json!(waiting > 0 && self.reading()));
                }
                Err(fault) => log::warning("ai", "what waits for a summary can't be read", &[], Some(fault.class)),
            }
        }
        Ok(Value::Object(report))
    }
}

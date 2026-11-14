//! How AI stands, with no call made (`application.status`): what the page says of it, and how the most recent call
//! went. With no API key the engine is "none", which is no failure. A call that was never made (AI off, or held back
//! by the cost governor) isn't one either.

use std::sync::{Arc, Mutex};

use mm_kernel::errors::Fault;
use mm_kernel::log;
use mm_kernel::seams::Clock;
use serde_json::{Map, Value, json};

use crate::gateway::Gateway;
use crate::ports::InForce;
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
    model_label: String,
    last: Mutex<Last>,
}

impl Status {
    pub fn new(gateway: Arc<Gateway>, clock: Arc<dyn Clock>, model_label: String) -> Status {
        Status { gateway, clock, model_label, last: Mutex::new(Last::default()) }
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

    /// What the page says of AI, with no call made: the engine ("api", or "none" with no key), its model, where the
    /// key came from, how the most recent call went, what today's calls cost so far in micro-dollars (null when the
    /// ledger can't be read) beside the daily limit, and how many messages a batch out holds, when one does.
    pub fn report(&self) -> Result<Value, Fault> {
        let in_force = self.gateway.in_force();
        let (on, key_from) = match &in_force {
            InForce::On { key_from, .. } => (true, *key_from),
            InForce::Off { .. } => (false, ""),
        };
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
        report.insert("model".into(), json!(if on { self.model_label.as_str() } else { "" }));
        report.insert("key_from".into(), json!(key_from));
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
        Ok(Value::Object(report))
    }
}

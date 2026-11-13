//! How AI stands, with no call made (`mailmind.assistant.application.status`): what the page says of it. With no
//! API key the engine is "none", which is no failure.
//!
//! The Rust server makes no AI call yet, so it says how AI stands only where no key is named: with one, the report
//! is `NOT_YET`.

use std::sync::Arc;

use mm_kernel::errors::{Fault, NOT_YET};
use mm_kernel::log;
use mm_kernel::seams::Clock;
use mm_kernel::when::civil;
use serde_json::{Value, json};

use crate::limits::limit_micros;
use crate::ports::{AiSettings, UsageLedger};

#[derive(Debug)]
pub struct Status {
    settings: Arc<dyn AiSettings>,
    ledger: Arc<dyn UsageLedger>,
    clock: Arc<dyn Clock>,
}

impl Status {
    pub fn new(settings: Arc<dyn AiSettings>, ledger: Arc<dyn UsageLedger>, clock: Arc<dyn Clock>) -> Status {
        Status { settings, ledger, clock }
    }

    /// The engine ("none" with no key) and its model, where the key came from, how the most recent call went
    /// (`last_ok` null until one has run), and what today's calls cost so far in micro-dollars, null when the
    /// ledger can't be read, beside the daily limit.
    pub fn report(&self) -> Result<Value, Fault> {
        if self.settings.key_named()? {
            return Err(NOT_YET);
        }
        let (year, month, day) = civil(self.clock.now().whole_seconds().div_euclid(86_400));
        let spent = match self.ledger.spent(&format!("{year:04}-{month:02}-{day:02}")) {
            Ok(spent) => json!(spent),
            Err(fault) => {
                log::warning("ai", "the usage ledger can't be read", &[], Some(fault.class));
                Value::Null
            }
        };
        Ok(json!({
            "engine": "none", "model": "", "key_from": "", "last_ok": null, "last_ts": 0, "last_error": "",
            "spent_today_micros": spent, "limit_micros": limit_micros(self.settings.daily_limit()?.as_ref()),
        }))
    }
}

//! The gateway every AI call goes through (`application.gateway`): it builds the request for the model in use, asks
//! the cost governor whether it may go now, sends it through the language model for the key in force, and adds each
//! call to the usage ledger: what it cost, under the feature that made it.

use std::fmt;
use std::sync::Arc;

use mm_kernel::errors::Fault;
use mm_kernel::log;
use mm_kernel::seams::Clock;
use mm_kernel::zone::Zone;
use serde_json::Value;

use crate::features;
use crate::governor::{CostGovernor, Spending};
use crate::limits::limit_micros;
use crate::models;
use crate::ports::{AiSettings, Engine, InForce, LanguageModel, ModelError, Prompt, Reply, Spend, Usage, UsageLedger};
use crate::pricing;
use crate::prompts::SYSTEM;
use crate::refusals::{CallError, NotSent};
use crate::schema::structured;

/// A ceiling, not a target; adaptive thinking counts against it.
pub const MAX_TOKENS: u32 = 16000;

/// The local date a moment counts against, `YYYY-MM-DD`: spending rolls at local midnight.
pub fn local_day(zone: &dyn Zone, seconds: i64) -> String {
    let (year, month, day) = mm_kernel::zone::date(zone, seconds);
    format!("{year:04}-{month:02}-{day:02}")
}

/// What the day has cost, against what it may: the ledger's, and the config's limit read at each ask.
#[derive(Debug)]
pub struct Budget {
    pub ledger: Arc<dyn UsageLedger>,
    pub settings: Arc<dyn AiSettings>,
    pub clock: Arc<dyn Clock>,
    pub zone: Arc<dyn Zone>,
}

impl Spending for Budget {
    fn spent_today(&self) -> Result<i64, Fault> {
        self.ledger.spent(&local_day(&*self.zone, self.clock.now().whole_seconds()))
    }

    /// A config file that can't be read leaves it at the default.
    fn limit_micros(&self) -> i64 {
        limit_micros(self.settings.daily_limit().ok().flatten().as_ref())
    }

    fn reserved(&self) -> Result<i64, Fault> {
        self.ledger.reserved()
    }
}

pub struct Gateway {
    engine: Arc<dyn Engine>,
    budget: Arc<Budget>,
    governor: Arc<CostGovernor>,
}

impl fmt::Debug for Gateway {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("Gateway(..)")
    }
}

impl Gateway {
    pub fn new(engine: Arc<dyn Engine>, budget: Arc<Budget>, governor: Arc<CostGovernor>) -> Gateway {
        Gateway { engine, budget, governor }
    }

    /// The language model for the key in force, or that AI is off.
    pub fn in_force(&self) -> InForce {
        self.engine.in_force()
    }

    /// False when AI is off: there is no key, and every call is `AiOff`, unsent.
    pub fn on(&self) -> bool {
        matches!(self.in_force(), InForce::On { .. })
    }

    pub fn governor(&self) -> &Arc<CostGovernor> {
        &self.governor
    }

    /// `text` as one user turn under the system prompt to `model` (the default one unless named), with structured
    /// output when `schema` is given, at `effort` (medium unless named) where the model takes one, and the refusal
    /// fallback where it takes that. The call is counted under `feature` whatever its outcome, if it was made.
    pub fn ask(
        &self,
        text: &str,
        schema: Option<&Value>,
        feature: &str,
        model: Option<&str>,
        effort: Option<&str>,
    ) -> Result<Reply, CallError> {
        let name = model.filter(|m| !m.is_empty()).unwrap_or(models::DEFAULT);
        let prompt = Prompt {
            text: text.to_owned(),
            model: name.to_owned(),
            max_tokens: MAX_TOKENS,
            schema: schema.map(structured),
            effort: models::takes_effort(name).then(|| effort.unwrap_or(models::EFFORT).to_owned()),
            fallback: models::takes_fallback(name),
            system: Some(SYSTEM),
        };
        let InForce::On { model: lm, .. } = self.in_force() else {
            return Err(NotSent::AiOff.into());
        };
        let made = self.governor.call(features::kind(feature), feature, name, &lm, || lm.complete(&prompt))?;
        match made {
            Ok(reply) => {
                self.heard(feature, name, &lm, None);
                let outcome = if reply.refused() { "refused" } else { "ok" };
                self.count(feature, name, &reply.model, outcome, reply.usage);
                Ok(reply)
            }
            Err(error) => {
                self.heard(feature, name, &lm, Some(&error));
                let outcome = if error.permanent() { "rejected" } else { "failed" };
                self.count(feature, name, "", outcome, Usage::default());
                Err(CallError::Model(error))
            }
        }
    }

    /// Why a call for `feature` would be held back now ("yielding", "away", "limit", "breaker"), or None when it
    /// would go. Background work asks before it starts.
    pub fn held_back(&self, feature: &str, model: Option<&str>) -> Option<&'static str> {
        let name = model.filter(|m| !m.is_empty()).unwrap_or(models::DEFAULT);
        match self.in_force() {
            InForce::On { model: lm, .. } => self.governor.held_back(features::kind(feature), feature, name, &lm),
            InForce::Off { .. } => None,
        }
    }

    fn heard(&self, feature: &str, name: &str, lm: &Arc<dyn LanguageModel>, error: Option<&ModelError>) {
        self.governor.heard(feature, name, lm, error);
    }

    fn count(&self, feature: &str, asked: &str, served: &str, outcome: &'static str, usage: Usage) {
        let cost = pricing::cost_micros(
            asked,
            served,
            usage.input_tokens,
            usage.output_tokens,
            usage.cache_write_tokens,
            usage.cache_read_tokens,
        );
        let now = self.budget.clock.now().whole_seconds();
        let spend = Spend {
            day: local_day(&*self.budget.zone, now),
            feature: feature.to_owned(),
            model: asked.to_owned(),
            served: served.to_owned(),
            outcome,
            usage,
            cost_micros: cost,
            ts: now,
            calls: 1,
        };
        // The call itself went fine; a ledger write never undoes it.
        if let Err(fault) = self.budget.ledger.record(&spend) {
            log::warning("ai", "the usage ledger failed", &[], Some(fault.class));
        }
    }

    /// What today's calls cost so far, in micro-dollars.
    pub fn spent_today(&self) -> Result<i64, Fault> {
        self.budget.spent_today()
    }

    /// What batches still out hold, in messages.
    pub fn in_batches(&self) -> Result<i64, Fault> {
        self.budget.ledger.in_batches()
    }
}

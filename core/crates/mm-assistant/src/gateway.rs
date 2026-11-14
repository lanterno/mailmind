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
use crate::ports::{
    AiSettings, BatchResult, Engine, InForce, LanguageModel, ModelError, Prompt, Reply, Spend, Usage, UsageLedger,
};
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

/// One user turn under the system prompt to model `name`, with structured output when `schema` is given, at `effort`
/// (medium unless named) where the model takes one, and the refusal fallback when `fallback`.
fn prompt(text: &str, name: &str, schema: Option<&Value>, effort: Option<&str>, fallback: bool) -> Prompt {
    Prompt {
        text: text.to_owned(),
        model: name.to_owned(),
        max_tokens: MAX_TOKENS,
        schema: schema.map(structured),
        effort: models::takes_effort(name).then(|| effort.unwrap_or(models::EFFORT).to_owned()),
        fallback,
        system: Some(SYSTEM),
    }
}

/// What `usage` cost, at the dearer of the model asked and the one that served the reply.
fn cost(asked: &str, served: &str, usage: Usage) -> i64 {
    pricing::cost_micros(
        asked,
        served,
        usage.input_tokens,
        usage.output_tokens,
        usage.cache_write_tokens,
        usage.cache_read_tokens,
    )
}

/// What calls of one model and outcome came to, as the ledger adds them up.
#[derive(Clone, Copy, Debug, Default)]
struct Counted {
    calls: i64,
    usage: Usage,
    cost_micros: i64,
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
        let prompt = prompt(text, name, schema, effort, models::takes_fallback(name));
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

    /// The language model for the key in force, or `AiOff` with none: nothing is sent.
    fn model(&self) -> Result<Arc<dyn LanguageModel>, CallError> {
        match self.in_force() {
            InForce::On { model, .. } => Ok(model),
            InForce::Off { .. } => Err(NotSent::AiOff.into()),
        }
    }

    /// Each (custom id, text) as one request of one batch to `model` (the default one unless named), and the batch's
    /// id. Never with the refusal fallback, which batches reject, and never sent again. Its cost is counted when its
    /// answers are in; a failure to send it is counted as a call that failed.
    pub fn submit_batch(
        &self,
        requests: &[(String, String)],
        schema: Option<&Value>,
        feature: &str,
        model: Option<&str>,
        effort: Option<&str>,
    ) -> Result<String, CallError> {
        let name = model.filter(|m| !m.is_empty()).unwrap_or(models::DEFAULT);
        let prompts: Vec<(String, Prompt)> =
            requests.iter().map(|(id, text)| (id.clone(), prompt(text, name, schema, effort, false))).collect();
        let lm = self.model()?;
        match lm.submit_batch(&prompts) {
            Ok(batch_id) => {
                self.heard(feature, name, &lm, None);
                Ok(batch_id)
            }
            Err(error) => {
                self.heard(feature, name, &lm, Some(&error));
                let outcome = if error.permanent() { "rejected" } else { "failed" };
                self.count(feature, name, "", outcome, Usage::default());
                Err(CallError::Model(error))
            }
        }
    }

    /// Whether a batch has ended.
    pub fn batch_ended(&self, batch_id: &str) -> Result<bool, CallError> {
        self.model()?.batch_ended(batch_id).map_err(CallError::Model)
    }

    /// What a batch that has ended said, each answer counted under `feature` at half price, those of one model and
    /// outcome together, and how each went told to the cost governor, as a call's is.
    pub fn batch_results(
        &self,
        batch_id: &str,
        feature: &str,
        model: Option<&str>,
    ) -> Result<Vec<BatchResult>, CallError> {
        let name = model.filter(|m| !m.is_empty()).unwrap_or(models::DEFAULT);
        let lm = self.model()?;
        let results = lm.batch_results(batch_id, name).map_err(CallError::Model)?;
        // Each (served, outcome) and what its calls came to, in the order each first came.
        let mut tally: Vec<(String, &'static str, Counted)> = Vec::new();
        for result in &results {
            let (served, outcome, usage) = match &result.reply {
                None => {
                    let status = if result.permanent { Some(400) } else { None };
                    self.heard(feature, name, &lm, Some(&ModelError::new("a request in a batch was rejected", status)));
                    (String::new(), if result.permanent { "rejected" } else { "failed" }, Usage::default())
                }
                Some(reply) => {
                    self.heard(feature, name, &lm, None);
                    (reply.model.clone(), if reply.refused() { "refused" } else { "ok" }, reply.usage)
                }
            };
            let spent = pricing::batch_micros(cost(name, &served, usage));
            let at = match tally.iter().position(|t| t.0 == served && t.1 == outcome) {
                Some(at) => at,
                None => {
                    tally.push((served, outcome, Counted::default()));
                    tally.len() - 1
                }
            };
            let sum = &mut tally[at].2;
            sum.calls += 1;
            sum.usage.input_tokens += usage.input_tokens;
            sum.usage.output_tokens += usage.output_tokens;
            sum.usage.cache_write_tokens += usage.cache_write_tokens;
            sum.usage.cache_read_tokens += usage.cache_read_tokens;
            sum.cost_micros += spent;
        }
        for (served, outcome, counted) in tally {
            self.record(feature, name, &served, outcome, counted);
        }
        Ok(results)
    }

    /// Remove what the API keeps of a batch.
    pub fn discard_batch(&self, batch_id: &str) -> Result<(), CallError> {
        self.model()?.discard_batch(batch_id).map_err(CallError::Model)
    }

    /// What today's limit leaves for background work, in micro-dollars.
    pub fn room(&self) -> i64 {
        self.governor.room()
    }

    fn heard(&self, feature: &str, name: &str, lm: &Arc<dyn LanguageModel>, error: Option<&ModelError>) {
        self.governor.heard(feature, name, lm, error);
    }

    fn count(&self, feature: &str, asked: &str, served: &str, outcome: &'static str, usage: Usage) {
        self.record(
            feature,
            asked,
            served,
            outcome,
            Counted { calls: 1, usage, cost_micros: cost(asked, served, usage) },
        );
    }

    fn record(&self, feature: &str, asked: &str, served: &str, outcome: &'static str, counted: Counted) {
        let Counted { calls, usage, cost_micros } = counted;
        let now = self.budget.clock.now().whole_seconds();
        let spend = Spend {
            day: local_day(&*self.budget.zone, now),
            feature: feature.to_owned(),
            model: asked.to_owned(),
            served: served.to_owned(),
            outcome,
            usage,
            cost_micros,
            ts: now,
            calls,
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

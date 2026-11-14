//! A bulk command in words: plan, preview, confirm (`application.bulk` and `application.plans`).
//!
//! An untrusted command maps to a constrained {verb, filter} through Claude. The filter is read again through the
//! search's own grammar, and what it reaches is frozen, capped, as a plan. Planning changes nothing: the page shows a
//! confirm gate, and only a click sends the plan's id back to be carried out.
//!
//! A plan can be confirmed once, within `action_plan::TTL` seconds. Nothing of it is durable: a restart ends every plan,
//! and the person plans again.

use std::fmt;
use std::sync::{Arc, Mutex, PoisonError};

use mm_kernel::errors::{Fault, Problem};
use mm_kernel::seams::Clock;
use mm_kernel::zone::Zone;
use serde_json::{Map, Value, json};

use crate::action_plan::{
    self, ActionPlan, FILTER_MAX, KEPT, SAMPLE, SNOOZE_PRESETS, TTL, VERBS, cap_of, filter_told, out_of_reach,
    preset_told, verb_told,
};
use crate::calls::Calls;
use crate::ports::PlanSearch;
use crate::prompts;
use crate::refusals::{AiFailure, answered};

pub fn schema() -> Value {
    json!({
        "type": "object",
        "additionalProperties": false,
        "required": ["intent"],
        "properties": {
            "intent": {"type": "string", "enum": ["act", "search"]},
            "filter": {"type": "string", "maxLength": FILTER_MAX},
            "verb": {"type": "string", "enum": VERBS},
            "snooze_preset": {"type": "string", "enum": SNOOZE_PRESETS},
        },
    })
}

/// A plan's id, drawn where the program can draw one.
pub type Draw = Arc<dyn Fn() -> Result<String, Fault> + Send + Sync>;

/// The plans open, by id, the newest last.
#[derive(Default)]
struct PlanBook {
    plans: Vec<ActionPlan>,
}

pub struct BulkCommands {
    calls: Arc<Calls>,
    search: Arc<dyn PlanSearch>,
    clock: Arc<dyn Clock>,
    zone: Arc<dyn Zone>,
    draw: Draw,
    book: Mutex<PlanBook>,
}

impl fmt::Debug for BulkCommands {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("BulkCommands(..)")
    }
}

fn degraded(filter: &str) -> Value {
    json!({"ok": true, "intent": "search", "filter": filter})
}

impl BulkCommands {
    pub fn new(
        calls: Arc<Calls>,
        search: Arc<dyn PlanSearch>,
        clock: Arc<dyn Clock>,
        zone: Arc<dyn Zone>,
        draw: Draw,
    ) -> BulkCommands {
        BulkCommands { calls, search, clock, zone, draw, book: Mutex::new(PlanBook::default()) }
    }

    /// `POST /api/ai/do-plan`: a command in words as a validated, capped preview with the id of the plan it froze.
    /// Changes nothing. It is a search, acting on nothing, whenever it isn't a clean, allowlisted, scoped action.
    pub fn plan(&self, cmd: Option<&Value>) -> Result<Value, AiFailure> {
        let cmd = action_plan::command_of(cmd);
        if cmd.is_empty() {
            return Err(Problem::refused("empty command").into());
        }
        let (y, m, d) = mm_kernel::zone::date(&*self.zone, self.clock.now().whole_seconds());
        let today = format!("{y:04}-{m:02}-{d:02}");
        let prompt = prompts::PLAN.fill(&[("today", &today), ("cmd", &cmd)]);
        let made = self.calls.call_claude_json(&prompt, &schema(), None, "plan");
        let plan: Map<String, Value> = answered(made, "plan failed", "planner unavailable")?;
        let filter = filter_told(&plan);
        let Some(verb) = verb_told(&plan) else { return Ok(degraded(&filter)) };
        // The model's filter is read again through the real grammar: what it wrote is never trusted raw.
        let (narrows, names_folder) = self.search.narrows(&filter);
        if !narrows {
            return Ok(degraded(&filter));
        }
        let cap = cap_of(verb);
        let apart: Vec<&str> = out_of_reach(verb, names_folder).into_iter().collect();
        let Some(reached) = self.search.reach(&filter, cap, &apart, SAMPLE)? else { return Ok(degraded(&filter)) };
        if reached.messages.is_empty() {
            return Ok(json!({"ok": true, "intent": "search", "filter": filter, "empty": true}));
        }
        // What is still most of the mailbox is refused, whatever the filter says.
        if self.search.sweeps_whole_mailbox(reached.matched)? {
            return Ok(degraded(&filter));
        }
        let preset = preset_told(&plan, verb);
        let id = self.open(verb, preset, reached.messages)?;
        Ok(json!({"ok": true, "intent": "act", "verb": verb, "filter": filter, "snooze_preset": preset,
                  "count": reached.count, "cap": cap, "capped": reached.count > cap, "plan": id,
                  "sample": reached.sample}))
    }

    fn open(&self, verb: &str, preset: &str, messages: Vec<(i64, String)>) -> Result<String, Fault> {
        let id = (self.draw)()?;
        let expires = self.clock.now().micros() + TTL * 1_000_000;
        let plan =
            ActionPlan { id: id.clone(), verb: verb.to_owned(), preset: Some(preset.to_owned()), messages, expires };
        let mut book = self.book.lock().unwrap_or_else(PoisonError::into_inner);
        book.plans.retain(|p| p.id != id);
        book.plans.push(plan);
        let over = book.plans.len().saturating_sub(KEPT);
        book.plans.drain(..over);
        Ok(id)
    }

    /// What a preview froze, once, or None when its plan has expired or no one made it. The Assistant only says what
    /// it was: the mail's own contexts carry it out.
    pub fn take(&self, id: &str) -> Option<ActionPlan> {
        let plan = {
            let mut book = self.book.lock().unwrap_or_else(PoisonError::into_inner);
            let at = book.plans.iter().position(|p| p.id == id)?;
            book.plans.remove(at)
        };
        (self.clock.now().micros() < plan.expires).then_some(plan)
    }
}

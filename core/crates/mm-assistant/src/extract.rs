//! What a message proposes, read by Claude on a person's click (`application.extract`): the meetings and dated tasks in
//! its text, as suggestions they confirm or edit. One schema-constrained call, and nothing is created by it.

use std::sync::Arc;

use mm_kernel::errors::Problem;
use mm_kernel::seams::Clock;
use mm_kernel::zone::Zone;
use serde_json::{Value, json};

use crate::calls::Calls;
use crate::context::Context;
use crate::extraction;
use crate::prompts;
use crate::refusals::{AiFailure, answered};

pub fn schema() -> Value {
    let text = json!({"type": "string"});
    json!({
        "type": "object",
        "properties": {
            "events": {"type": "array", "items": {"type": "object",
                "properties": {"title": text, "start": text, "end": text, "place": text},
                "required": ["title", "start", "end", "place"]}},
            "tasks": {"type": "array", "items": {"type": "object", "properties": {"title": text, "due": text},
                "required": ["title", "due"]}},
        },
        "required": ["events", "tasks"],
    })
}

const WEEKDAYS: [&str; 7] = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

#[derive(Debug)]
pub struct Extractor {
    calls: Arc<Calls>,
    context: Context,
    clock: Arc<dyn Clock>,
    zone: Arc<dyn Zone>,
}

impl Extractor {
    pub fn new(calls: Arc<Calls>, context: Context, clock: Arc<dyn Clock>, zone: Arc<dyn Zone>) -> Extractor {
        Extractor { calls, context, clock, zone }
    }

    /// What the message `pk` proposes.
    pub fn read(&self, pk: Option<i64>) -> Result<Value, AiFailure> {
        let pk = pk.ok_or_else(|| Problem::refused("missing message"))?;
        let rows = self.context.mail().to_reply_to(pk)?;
        let row = rows.first().ok_or_else(|| Problem::not_found("not found"))?;
        let today = mm_kernel::zone::date(&*self.zone, self.clock.now().whole_seconds());
        let days = mm_kernel::when::days_from_civil(today.0, today.1, today.2);
        // 1970-01-01 was a Thursday.
        let weekday = WEEKDAYS[usize::try_from((days + 3).rem_euclid(7)).unwrap_or(0)];
        let today_text = format!("{weekday}, {:04}-{:02}-{:02}", today.0, today.1, today.2);
        let some = |v: &Option<String>| v.clone().filter(|s| !s.is_empty());
        let body = extraction::readable(&self.context.body_of(row)?);
        let prompt = prompts::EXTRACT.fill(&[
            ("today", &today_text),
            ("sent", &some(&row.date_iso).unwrap_or_else(|| "an unknown day".into())),
            ("frm", &some(&row.from_name).or_else(|| some(&row.from_addr)).unwrap_or_else(|| "someone".into())),
            ("subject", &some(&row.subject).unwrap_or_else(|| "(no subject)".into())),
            ("body", &body),
        ]);
        let answer = answered(
            self.calls.call_claude_json(&prompt, &schema(), None, "extract"),
            "extract failed",
            "couldn\u{2019}t read this message for dates",
        )?;
        let (events, tasks) = extraction::suggestions(&answer, today);
        Ok(json!({"ok": true, "events": events, "tasks": tasks, "model": self.calls.model_label()}))
    }
}

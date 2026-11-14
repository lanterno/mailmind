//! The AI's suggestions for the Screener (`domain.screening` and `application.screening`): a pass over the senders
//! waiting there, on a person's click, which says Allow or Screen out only of those it is sure of. The answer is its own
//! words on a stranger's mail, so a verdict names a sender only by a number it was shown, and only one said surely
//! enough stands. It suggests and nothing else: a person's click on a row writes a rule, through Curation.

use std::collections::HashSet;
use std::sync::Arc;

use serde_json::{Map, Value, json};

use crate::calls::Calls;
use crate::ports::{SenderRecent, Senders};
use crate::prompts;
use crate::refusals::{AiFailure, answered};
use crate::text::{cut, python_str, truthy};

/// How many senders one pass reads: the Screener's newest.
pub const MAX_SENDERS: usize = 40;
/// How many of a sender's newest messages it is told of.
pub const SUBJECTS: i64 = 3;
/// Below this, nothing is said: a hunch on a row someone will act on is worse than none.
pub const MIN_CONFIDENCE: f64 = 0.8;
pub const WHY_MAX: usize = 120;
/// Of one sender's line: a subject is the sender's to make long.
const LINE_MAX: usize = 400;
/// Of what the AI said of a message.
const GIST_MAX: usize = 80;
const VERDICTS: [&str; 2] = ["keep", "block"];

pub fn schema() -> Value {
    json!({"type": "object", "properties": {"verdicts": {"type": "array", "items": {"type": "object", "properties": {
           "id": {"type": "integer"}, "verdict": {"type": "string", "enum": ["keep", "block"]},
           "confidence": {"type": "number"}, "why": {"type": "string"}},
           "required": ["id", "verdict", "confidence", "why"]}}}, "required": ["verdicts"]})
}

/// A field of a Screener row as Python's f-string writes it.
fn shown(sender: &Value, name: &str) -> String {
    python_str(sender.get(name).unwrap_or(&Value::Null))
}

/// One sender as the model is told of them, under number `n`: `sender` is a row of the Screener's queue.
pub fn line(n: usize, sender: &Value, recent: &[SenderRecent]) -> String {
    let seen: Vec<&str> =
        recent.iter().map(|r| r.subject.as_deref().filter(|s| !s.is_empty()).unwrap_or("(no subject)")).collect();
    let gist = recent.iter().find_map(|r| r.summary.as_deref().filter(|s| !s.is_empty())).map(|s| cut(s, GIST_MAX));
    let said = gist.map(|g| format!(" AI on the latest: {g}")).unwrap_or_default();
    let line = format!(
        "[{n}] {} <{}>, {} in the inbox. Subjects: {}.{said}",
        shown(sender, "from_name"),
        shown(sender, "from_addr"),
        shown(sender, "count"),
        seen.join("; ")
    );
    cut(&line, LINE_MAX).to_owned()
}

/// How sure the model said it was, from 0 to 1. What can't be read as that isn't sure at all.
pub fn confidence(told: Option<&Value>) -> f64 {
    match told.and_then(Value::as_f64) {
        Some(sure) if !sure.is_nan() => sure.clamp(0.0, 1.0),
        _ => 0.0,
    }
}

/// `round(x, 2)`.
fn round2(x: f64) -> f64 {
    format!("{x:.2}").parse().unwrap_or(x)
}

/// What stands of an answer about the senders in `shown`, the first of which was told as number 1. One verdict a
/// sender: the first it gave. A number it wasn't shown, a verdict that is no rule's and anything said less surely than
/// MIN_CONFIDENCE all go.
pub fn clean(answer: &Map<String, Value>, shown: &[String]) -> Vec<Value> {
    let told = match answer.get("verdicts") {
        Some(Value::Array(items)) => items.as_slice(),
        _ => &[],
    };
    let mut out = Vec::new();
    let mut taken = HashSet::new();
    for item in told.iter().filter_map(Value::as_object) {
        let (verdict, sure) = (item.get("verdict").and_then(Value::as_str), confidence(item.get("confidence")));
        let Some(n) = item.get("id").and_then(Value::as_i64) else { continue };
        let at = usize::try_from(n).ok().filter(|n| (1..=shown.len()).contains(n));
        let Some(addr) = at.map(|n| &shown[n - 1]) else { continue };
        if taken.contains(&n) {
            continue;
        }
        let Some(verdict) = verdict.filter(|v| VERDICTS.contains(v)) else { continue };
        if sure < MIN_CONFIDENCE {
            continue;
        }
        taken.insert(n);
        let why = item.get("why").filter(|w| truthy(w)).map(python_str).unwrap_or_default();
        out.push(json!({"from_addr": addr, "verdict": verdict, "confidence": round2(sure),
                        "why": cut(&why, WHY_MAX)}));
    }
    out
}

#[derive(Debug)]
pub struct Screening {
    calls: Arc<Calls>,
    senders: Arc<dyn Senders>,
}

impl Screening {
    pub fn new(calls: Arc<Calls>, senders: Arc<dyn Senders>) -> Screening {
        Screening { calls, senders }
    }

    /// `POST /api/screener/suggest`: who of `waiting`, the senders in this account's Screener as Curation lists them,
    /// the AI would allow or screen out, and why, where it is sure.
    pub fn suggest(&self, account: Option<&str>, waiting: &[Value]) -> Result<Value, AiFailure> {
        let waiting = &waiting[..waiting.len().min(MAX_SENDERS)];
        if waiting.is_empty() {
            return Ok(json!({"ok": true, "account": account, "asked": 0, "suggestions": []}));
        }
        let addrs: Vec<String> = waiting.iter().map(|s| shown(s, "from_addr")).collect();
        let recent = self.senders.senders_recent(&addrs.iter().map(String::as_str).collect::<Vec<_>>(), SUBJECTS)?;
        let lines: Vec<String> = (1..).zip(waiting.iter().zip(&recent)).map(|(n, (s, r))| line(n, s, r)).collect();
        let prompt = prompts::SCREEN.fill(&[("senders", &lines.join("\n"))]);
        let made = self.calls.call_claude_json(&prompt, &schema(), None, "screen");
        let answer = answered(made, "screener suggestions failed", "suggestions unavailable")?;
        Ok(json!({"ok": true, "account": account, "asked": waiting.len(), "suggestions": clean(&answer, &addrs)}))
    }
}

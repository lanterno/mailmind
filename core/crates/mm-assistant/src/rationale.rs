//! Why a sender is worth cleaning up (`domain.rationale` and `application.rationale`): one call over the stored numbers
//! of the senders a person marked, for the preview a batch cleanup shows. The numbers are the stored mail's, so the
//! model puts them in words and invents none, and a sender it says nothing of gets the same numbers in plain words. It
//! says why and does nothing: what a person confirms goes through Curation.

use std::collections::HashMap;
use std::sync::Arc;

use mm_kernel::errors::{Fault, Problem};
use mm_kernel::seams::Clock;
use serde_json::{Map, Value, json};

use crate::calls::Calls;
use crate::ports::{SenderStats, Senders};
use crate::prompts;
use crate::refusals::{AiFailure, answered};
use crate::text::{cut, length, strip};

/// How many one pass reads: what the list of noisy senders shows.
pub const MAX_SENDERS: usize = 40;
/// Of one rationale.
pub const TEXT_MAX: usize = 200;
const ADDR_MAX: usize = 320;

pub fn schema() -> Value {
    json!({"type": "object", "properties": {"rationales": {"type": "array", "items": {"type": "object",
           "properties": {"id": {"type": "integer"}, "text": {"type": "string"}}, "required": ["id", "text"]}}},
           "required": ["rationales"]})
}

/// The distinct senders a request names, lower-cased as rows go by them, or None if it names none, too many, or anything
/// that isn't an address.
pub fn named(raw: &[String]) -> Option<Vec<String>> {
    if raw.is_empty() || raw.len() > MAX_SENDERS {
        return None;
    }
    let mut out: Vec<String> = Vec::new();
    for one in raw {
        if !one.contains('@') || length(one) > ADDR_MAX {
            return None;
        }
        let addr = strip(one).to_lowercase();
        if !out.contains(&addr) {
            out.push(addr);
        }
    }
    Some(out)
}

/// How long `seconds` is, in the words a sentence would use.
pub fn span(seconds: f64) -> String {
    let days = (seconds / 86400.0).floor().max(0.0) as i64;
    match days {
        0 => "under a day".into(),
        1 => "1 day".into(),
        2..60 => format!("{days} days"),
        60..730 => format!("{} months", days / 30),
        _ => format!("{} years", days / 365),
    }
}

/// A count as Python writes it, `None` where the store had none.
fn count(n: Option<i64>) -> String {
    n.map_or_else(|| "None".into(), |n| n.to_string())
}

/// How long ago `ts` was at `now`; a sender with no time stored can't be put in words.
fn since(ts: Option<i64>, now: f64) -> Result<String, Fault> {
    ts.map(|ts| span(now - ts as f64)).ok_or_else(|| Fault::new("TypeError"))
}

/// One sender's numbers as the model is told them, under number `n`.
pub fn line(n: usize, row: &SenderStats, now: f64) -> Result<String, Fault> {
    let name = row.name.as_deref().filter(|s| !s.is_empty()).unwrap_or(&row.addr);
    Ok(format!(
        "[{n}] {name} <{}>: {} messages over {}, {} of them never opened, {} replied to, the latest {} ago.",
        row.addr,
        row.n,
        since(row.first_ts, now)?,
        count(row.unseen),
        count(row.replied),
        since(row.last_ts, now)?,
    ))
}

/// The same numbers in plain words, for a sender the model said nothing about.
pub fn fallback(row: &SenderStats, now: f64) -> Result<String, Fault> {
    let said = format!(
        "{} of {} unopened over {}, {} replied to.",
        count(row.unseen),
        row.n,
        since(row.first_ts, now)?,
        count(row.replied)
    );
    Ok(cut(&said, TEXT_MAX).to_owned())
}

/// One rationale for each sender in `rows`, the first of which was told as number 1: what the model said of it, or
/// else `fallback`. A number it wasn't shown says nothing of anyone, and the first word on a sender stands.
pub fn clean(answer: &Map<String, Value>, rows: &[SenderStats], now: f64) -> Result<Map<String, Value>, Fault> {
    let mut said: HashMap<i64, String> = HashMap::new();
    let told = match answer.get("rationales") {
        Some(Value::Array(items)) => items.as_slice(),
        _ => &[],
    };
    for item in told.iter().filter_map(Value::as_object) {
        let Some(n) = item.get("id").and_then(Value::as_i64) else { continue };
        if said.contains_key(&n) {
            continue;
        }
        if let Some(Value::String(text)) = item.get("text")
            && !strip(text).is_empty()
        {
            said.insert(n, cut(strip(text), TEXT_MAX).to_owned());
        }
    }
    let mut out = Map::new();
    for (n, row) in (1..).zip(rows) {
        let text = match said.remove(&n) {
            Some(text) => text,
            None => fallback(row, now)?,
        };
        out.insert(row.addr.clone(), Value::String(text));
    }
    Ok(out)
}

#[derive(Debug)]
pub struct Rationales {
    calls: Arc<Calls>,
    senders: Arc<dyn Senders>,
    clock: Arc<dyn Clock>,
}

impl Rationales {
    pub fn new(calls: Arc<Calls>, senders: Arc<dyn Senders>, clock: Arc<dyn Clock>) -> Rationales {
        Rationales { calls, senders, clock }
    }

    /// `POST /api/cleanup/rationale`: a sentence for each of `senders` that has mail stored. `bad senders` for a list
    /// that won't read.
    pub fn explain(&self, senders: Option<&[String]>) -> Result<Value, AiFailure> {
        let addrs = named(senders.unwrap_or_default()).ok_or_else(|| Problem::refused("bad senders"))?;
        let rows = self.senders.sender_stats(&addrs)?;
        if rows.is_empty() {
            return Ok(json!({"ok": true, "rationales": {}}));
        }
        let now = self.clock.now().seconds();
        let lines = (1..).zip(&rows).map(|(n, r)| line(n, r, now)).collect::<Result<Vec<_>, _>>()?;
        let prompt = prompts::RATIONALE.fill(&[("senders", &lines.join("\n"))]);
        let made = self.calls.call_claude_json(&prompt, &schema(), None, "rationale");
        let answer = answered(made, "cleanup rationale failed", "rationale unavailable")?;
        Ok(json!({"ok": true, "rationales": clean(&answer, &rows, now)?}))
    }
}

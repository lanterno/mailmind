//! The composer's text rewritten, its tone changed or translated (`application.rewrite`): one schema-constrained call
//! answering `{"text": ...}`. The text is untrusted data, and what comes back is plain text.

use mm_kernel::errors::{Fault, Problem};
use serde_json::{Value, json};

use crate::calls::Calls;
use crate::prompts;
use crate::refusals::{AiFailure, answered, unreachable};
use crate::text::{cut, strip};

pub fn schema() -> Value {
    json!({"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]})
}

/// What a JSON object says for `name` as text, `(obj.get(name) or "")`: "" for what Python counts false, and a fault
/// for anything else that isn't text, as `.strip()` on it would raise.
pub fn text_of(answer: &serde_json::Map<String, Value>, name: &str) -> Result<String, Fault> {
    match answer.get(name) {
        Some(Value::String(s)) => Ok(s.clone()),
        Some(v) if crate::text::truthy(v) => Err(Fault::new("AttributeError")),
        _ => Ok(String::new()),
    }
}

/// `text` rewritten as `op` says, in `lang` for a translation.
pub fn rewrite(calls: &Calls, text: &str, op: &str, lang: &str) -> Result<Value, AiFailure> {
    let text = strip(text);
    if text.is_empty() {
        return Err(Problem::refused("nothing to rewrite").into());
    }
    let Some(instr) = prompts::look_up(&prompts::REWRITE_OPS, op) else {
        return Err(Problem::refused("unknown op").into());
    };
    let lang = if lang.is_empty() { "English" } else { lang };
    let instr = instr.replace("{lang}", cut(lang, 40));
    let prompt = prompts::REWRITE.fill(&[("instr", &instr), ("text", cut(text, 8000))]);
    let answer =
        answered(calls.call_claude_json(&prompt, &schema(), None, "rewrite"), "rewrite failed", "rewrite unavailable")?;
    let out = text_of(&answer, "text")?;
    let out = strip(&out);
    if out.is_empty() {
        return Err(unreachable("no rewrite produced").into());
    }
    Ok(json!({"ok": true, "text": cut(out, 12000), "op": op}))
}

//! The Messages API's wire format (`assistant.adapters.wire`): the request body for a `Prompt`, and the `Reply` in a
//! response.

use mm_assistant::ports::{Prompt, Reply, Usage};
use serde_json::{Map, Value, json};

/// The body of a POST /v1/messages for `prompt`. A prompt that takes the refusal fallback asks for it here, beside the
/// beta header the request carries.
pub fn body(prompt: &Prompt) -> Value {
    let mut out = Map::new();
    out.insert("model".into(), json!(prompt.model));
    out.insert("max_tokens".into(), json!(prompt.max_tokens));
    out.insert("messages".into(), json!([{"role": "user", "content": prompt.text}]));
    if let Some(system) = prompt.system.filter(|s| !s.is_empty()) {
        out.insert("system".into(), json!(system));
    }
    let mut config = Map::new();
    if let Some(schema) = &prompt.schema {
        config.insert("format".into(), json!({"type": "json_schema", "schema": schema}));
    }
    if let Some(effort) = prompt.effort.as_deref().filter(|e| !e.is_empty()) {
        config.insert("effort".into(), json!(effort));
    }
    if !config.is_empty() {
        out.insert("output_config".into(), Value::Object(config));
    }
    if prompt.fallback {
        out.insert("fallbacks".into(), json!("default"));
    }
    Value::Object(out)
}

fn count(usage: &Value, name: &str) -> i64 {
    let read = match usage.get(name) {
        Some(Value::Number(n)) => n.as_i64().or_else(|| n.as_f64().map(|f| f.trunc() as i64)),
        Some(Value::String(s)) => s.trim().parse().ok(),
        Some(Value::Bool(b)) => Some(i64::from(*b)),
        _ => None,
    };
    read.unwrap_or(0).max(0)
}

fn text_of(value: Option<&Value>) -> String {
    match value {
        None | Some(Value::Null) => String::new(),
        Some(Value::String(s)) => s.clone(),
        Some(other) => other.to_string(),
    }
}

/// The model that served the reply. After a server-side fallback that is the fallback's, named by its
/// `fallback_message` entry in `usage.iterations`, or by the last fallback block.
fn served(response: &Value, blocks: &[&Map<String, Value>], asked: &str) -> String {
    let iterations = response.get("usage").and_then(|u| u.get("iterations")).and_then(Value::as_array);
    for it in iterations.into_iter().flatten().rev() {
        if it.get("type").and_then(Value::as_str) == Some("fallback_message")
            && let Some(model) = it.get("model").filter(|m| truthy(m))
        {
            return text_of(Some(model));
        }
    }
    for b in blocks.iter().rev() {
        if b.get("type").and_then(Value::as_str) == Some("fallback")
            && let Some(to) = b.get("to").and_then(|to| to.get("model")).filter(|m| truthy(m))
        {
            return text_of(Some(to));
        }
    }
    match response.get("model").filter(|m| truthy(m)) {
        Some(model) => text_of(Some(model)),
        None => asked.to_owned(),
    }
}

fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::String(s) => !s.is_empty(),
        Value::Number(n) => n.as_f64() != Some(0.0),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// The reply in a response to a request for model `asked`. A refusal reads as "": the call worked, so callers store
/// their fallback rather than retry. A model that declined mid-reply hands over at a "fallback" block, and only the
/// text after the last one is the answer.
pub fn reply(response: &Value, asked: &str) -> Reply {
    let empty = Value::Null;
    let usage = response.get("usage").filter(|u| truthy(u)).unwrap_or(&empty);
    let mut blocks: Vec<&Map<String, Value>> =
        response.get("content").and_then(Value::as_array).into_iter().flatten().filter_map(Value::as_object).collect();
    let spent = Usage {
        input_tokens: count(usage, "input_tokens"),
        output_tokens: count(usage, "output_tokens"),
        cache_write_tokens: count(usage, "cache_creation_input_tokens"),
        cache_read_tokens: count(usage, "cache_read_input_tokens"),
    };
    let stop = response.get("stop_reason").filter(|s| truthy(s)).map(|s| text_of(Some(s))).unwrap_or_default();
    let model = served(response, &blocks, asked);
    if stop == "refusal" {
        return Reply { text: String::new(), model, usage: spent, stop_reason: stop };
    }
    if let Some(last) = blocks.iter().rposition(|b| b.get("type").and_then(Value::as_str) == Some("fallback")) {
        blocks.drain(..=last);
    }
    let text: String = blocks
        .iter()
        .filter(|b| b.get("type").and_then(Value::as_str) == Some("text"))
        .map(|b| b.get("text").filter(|t| truthy(t)).map(|t| text_of(Some(t))).unwrap_or_default())
        .collect();
    Reply { text: mm_kernel::text::strip(&text).to_owned(), model, usage: spent, stop_reason: stop }
}

/// What an error response says, as the SDK words it: its `error.message`, else the body's, cut to 200 characters.
pub fn error_text(body: &[u8]) -> String {
    let parsed: Value = serde_json::from_slice(body).unwrap_or(Value::Null);
    let inner = parsed.get("error").filter(|e| e.is_object()).unwrap_or(&parsed);
    let said = inner.get("message").filter(|m| truthy(m)).map(|m| text_of(Some(m))).unwrap_or_default();
    said.chars().take(200).collect()
}

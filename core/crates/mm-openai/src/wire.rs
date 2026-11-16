//! OpenAI's Chat Completions wire format, which every provider but Claude is asked in (`assistant.adapters.chat_wire`,
//! #49): the request body for a `Prompt`, and the `Reply` in a response.
//!
//! The system prompt goes as a `system` message ahead of the user's. Structured output is `response_format`'s
//! `json_schema`, strict only where the schema keeps strict mode's rules (every object closed, and every property in it
//! required), since a strict schema that breaks them is refused. OpenAI takes `max_completion_tokens`, and the others
//! `max_tokens`. No effort and no server-side fallback go out: neither is theirs. A reasoning model's thinking, put
//! first as `<think>…</think>` (or `<thinking>…</thinking>`), is dropped before the reply is read.

use mm_assistant::ports::{Prompt, Reply, Usage};
use mm_kernel::ai_provider::AiProvider;
use serde_json::{Map, Value, json};

/// The name structured output's schema goes by.
pub const SCHEMA_NAME: &str = "answer";

/// Whether `schema` keeps strict mode's rules: every object closed, and every property it names required.
pub fn strict(schema: &Value) -> bool {
    match schema {
        Value::Array(items) => items.iter().all(strict),
        Value::Object(node) => strict_node(node),
        _ => true,
    }
}

fn strict_node(node: &Map<String, Value>) -> bool {
    let properties = node.get("properties").and_then(Value::as_object);
    if node.get("type").and_then(Value::as_str) == Some("object") || properties.is_some() {
        let mut names: Vec<&str> = properties.map(|p| p.keys().map(String::as_str).collect()).unwrap_or_default();
        let mut required: Vec<&str> = match node.get("required") {
            Some(Value::Array(required)) => required.iter().filter_map(Value::as_str).collect(),
            _ => Vec::new(),
        };
        names.sort_unstable();
        names.dedup();
        required.sort_unstable();
        required.dedup();
        if node.get("additionalProperties") != Some(&Value::Bool(false)) || names != required {
            return false;
        }
    }
    node.iter().all(|(key, value)| match key.as_str() {
        "properties" | "$defs" | "definitions" => value.as_object().is_none_or(|kids| kids.values().all(strict)),
        "items" | "anyOf" | "oneOf" | "allOf" | "prefixItems" => strict(value),
        _ => true,
    })
}

/// What a request without `response_format` says its schema in: the prompt, after a blank line, then this and the
/// schema as compact JSON, so a service that refused structured output is still told the shape of the answer.
pub const SCHEMA_STATED: &str = "Reply with only a JSON object that matches this JSON Schema: ";

/// The body of a `POST {base}/chat/completions` for `prompt`, with its schema as `response_format` unless
/// `with_format` is false (a service that refused it), when the schema is stated after the prompt instead.
pub fn body(prompt: &Prompt, provider: AiProvider, with_format: bool) -> Value {
    let mut messages = Vec::new();
    if let Some(system) = prompt.system.filter(|s| !s.is_empty()) {
        messages.push(json!({"role": "system", "content": system}));
    }
    let text = match prompt.schema.as_ref().filter(|_| !with_format) {
        // Compact, its keys in the schema's own order.
        Some(schema) => format!("{}\n\n{SCHEMA_STATED}{schema}", prompt.text),
        None => prompt.text.clone(),
    };
    messages.push(json!({"role": "user", "content": text}));
    let mut out = Map::new();
    out.insert("model".into(), json!(prompt.model));
    out.insert("messages".into(), Value::Array(messages));
    let most = if provider == AiProvider::OpenAi { "max_completion_tokens" } else { "max_tokens" };
    out.insert(most.into(), json!(prompt.max_tokens));
    if let Some(schema) = prompt.schema.as_ref().filter(|_| with_format) {
        out.insert(
            "response_format".into(),
            json!({"type": "json_schema",
                   "json_schema": {"name": SCHEMA_NAME, "schema": schema, "strict": strict(schema)}}),
        );
    }
    Value::Object(out)
}

/// A count of tokens as a response gives it: a number, never below nought; anything else is none.
fn count(usage: Option<&Value>, name: &str) -> i64 {
    let said = usage.and_then(|u| u.get(name));
    let n = match said {
        Some(Value::Number(n)) => n.as_i64().or_else(|| n.as_f64().map(|f| f as i64)),
        Some(Value::String(s)) => s.trim().parse::<i64>().ok(),
        _ => None,
    };
    n.unwrap_or(0).max(0)
}

/// A message's content: a string, or a list of parts whose text parts are joined.
fn text(content: Option<&Value>) -> String {
    match content {
        Some(Value::String(s)) => s.clone(),
        Some(Value::Array(parts)) => parts
            .iter()
            .filter(|p| p.get("type").and_then(Value::as_str) == Some("text"))
            .filter_map(|p| p.get("text").and_then(Value::as_str))
            .collect(),
        _ => String::new(),
    }
}

/// `text` without the thinking a reasoning model puts first: a leading `<think>…</think>` or `<thinking>…</thinking>`
/// block, after any blank. Text with none, or whose block never closes, is as it was.
pub fn without_thinking(text: &str) -> &str {
    let rest = text.trim_start();
    for (open, close) in [("<think>", "</think>"), ("<thinking>", "</thinking>")] {
        if let Some(inner) = rest.strip_prefix(open)
            && let Some(end) = inner.find(close)
        {
            return &inner[end + close.len()..];
        }
    }
    text
}

/// Whether a value says something, as Python's truth does.
fn truthy(value: Option<&Value>) -> bool {
    match value {
        None | Some(Value::Null) | Some(Value::Bool(false)) => false,
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
        Some(Value::Number(n)) => n.as_f64() != Some(0.0),
        Some(Value::Bool(true)) => true,
    }
}

/// The reply in a response to a request for model `asked`. A refusal reads as "", as Claude's does: the call worked,
/// so callers store their fallback rather than retry. Cached prompt tokens count as cache reads, and the rest of the
/// prompt as input. The output is the completion's tokens, or all but the prompt's where `total_tokens` says more:
/// Gemini leaves a thinking model's thoughts out of `completion_tokens`, and bills them as output.
pub fn reply(response: &Map<String, Value>, asked: &str) -> Reply {
    let usage = response.get("usage").filter(|u| u.is_object());
    let prompt_tokens = count(usage, "prompt_tokens");
    let cached = count(usage.and_then(|u| u.get("prompt_tokens_details")), "cached_tokens").min(prompt_tokens);
    // Without `total_tokens` this is less than nought, and the completion's count stands.
    let beyond_prompt = count(usage, "total_tokens") - prompt_tokens;
    let spent = Usage {
        input_tokens: prompt_tokens - cached,
        output_tokens: count(usage, "completion_tokens").max(beyond_prompt),
        cache_write_tokens: 0,
        cache_read_tokens: cached,
    };
    let served = match response.get("model") {
        Some(Value::String(model)) if !model.is_empty() => model.clone(),
        Some(other) if truthy(Some(other)) => other.to_string(),
        _ => asked.to_owned(),
    };
    let first = response.get("choices").and_then(Value::as_array).and_then(|c| c.first()).filter(|c| c.is_object());
    let message = first.and_then(|c| c.get("message")).filter(|m| m.is_object());
    if truthy(message.and_then(|m| m.get("refusal"))) {
        return Reply { text: String::new(), model: served, usage: spent, stop_reason: "refusal".into() };
    }
    let stop_reason = match first.and_then(|c| c.get("finish_reason")) {
        Some(Value::String(reason)) => reason.clone(),
        Some(other) if truthy(Some(other)) => other.to_string(),
        _ => String::new(),
    };
    Reply {
        text: without_thinking(&text(message.and_then(|m| m.get("content")))).trim().to_owned(),
        model: served,
        usage: spent,
        stop_reason,
    }
}

/// The ids a `GET {base}/models` answered with, none where it isn't a list of models.
pub fn model_ids(response: &Value) -> Vec<String> {
    model_list(response).unwrap_or_default()
}

/// The ids a `GET {base}/models` answered with, or None where the answer isn't a list of models (`{"data": [...]}`).
pub fn model_list(response: &Value) -> Option<Vec<String>> {
    let data = response.get("data").and_then(Value::as_array);
    data.map(|models| {
        models
            .iter()
            .filter_map(|m| m.get("id"))
            .filter(|id| truthy(Some(id)))
            .map(|id| id.as_str().map_or_else(|| id.to_string(), str::to_owned))
            .collect()
    })
}

/// The error a service answered with: the body's `error`, or the first element's where the body is a list, as
/// Gemini's is (`[{"error": {...}}]`).
fn error_of(parsed: &Value) -> Option<&Value> {
    match parsed {
        Value::Array(items) => items.first()?.get("error"),
        other => other.get("error"),
    }
}

/// Whether an error body says the key isn't valid, as Gemini answers a wrong key with a 400 rather than a 401: its
/// error has a detail whose reason is `API_KEY_INVALID`, or what the service said (`error_text`: the error's message,
/// or the error itself where it is words) starts "API key not valid".
pub fn key_invalid(body: &[u8]) -> bool {
    let parsed: Value = serde_json::from_slice(body).unwrap_or(Value::Null);
    let details = error_of(&parsed).and_then(|error| error.get("details")).and_then(Value::as_array);
    let reason = details.is_some_and(|d| d.iter().any(|e| e.get("reason") == Some(&json!("API_KEY_INVALID"))));
    reason || error_text(body).starts_with("API key not valid")
}

/// The most of what a service said that an error keeps, in characters.
pub const SAID_MOST: usize = 200;

/// What a service said of an error, as an error keeps it: `error_text` with `key` taken out of the whole of it, and
/// only then cut to `SAID_MOST` characters, so no part of a key the cut runs through is left.
pub fn said(body: &[u8], key: Option<&str>) -> String {
    let mut said = error_text(body);
    if let Some(key) = key.filter(|k| !k.is_empty()) {
        said = said.replace(key, "<key>");
    }
    said.chars().take(SAID_MOST).collect()
}

/// What a service said of an error, the whole of it: OpenAI's `{"error": {"message"}}`, Ollama's `{"error": "..."}`,
/// or Gemini's either in a list.
pub fn error_text(body: &[u8]) -> String {
    let parsed: Value = serde_json::from_slice(body).unwrap_or(Value::Null);
    let error = error_of(&parsed);
    let said = match error {
        Some(Value::Object(inner)) => inner.get("message"),
        other => other,
    };
    match said {
        Some(Value::String(s)) => s.clone(),
        Some(other) if truthy(Some(other)) => other.to_string(),
        _ => String::new(),
    }
}

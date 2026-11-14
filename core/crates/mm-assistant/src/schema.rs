//! A JSON Schema in the form structured output accepts, and the JSON object a reply holds
//! (`mailmind.assistant.domain.schema`).

use serde_json::{Map, Value};

/// JSON Schema keywords structured output doesn't support. Callers clamp what they read anyway.
const UNSUPPORTED: [&str; 7] =
    ["minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "minLength", "maxLength"];

/// `schema` with every object closed (`additionalProperties: false`) and the unsupported bounds dropped.
pub fn structured(schema: &Value) -> Value {
    match schema {
        Value::Array(items) => Value::Array(items.iter().map(structured).collect()),
        Value::Object(members) => {
            let mut out = Map::new();
            for (k, v) in members {
                if UNSUPPORTED.contains(&k.as_str()) {
                    continue;
                }
                let kept = match v {
                    // Names to schemas, not a schema.
                    Value::Object(named) if k == "properties" || k == "$defs" => {
                        Value::Object(named.iter().map(|(name, s)| (name.clone(), structured(s))).collect())
                    }
                    _ => structured(v),
                };
                out.insert(k.clone(), kept);
            }
            if out.get("type").and_then(Value::as_str) == Some("object") {
                out.insert("additionalProperties".into(), Value::Bool(false));
            }
            Value::Object(out)
        }
        other => other.clone(),
    }
}

/// What `re.search(r"\{.*\}", text, re.S)` finds: from the first `{` to the last `}` after it.
pub fn braced(text: &str) -> Option<&str> {
    let start = text.find('{')?;
    let end = text.rfind('}')?;
    (end > start).then(|| &text[start..=end])
}

/// The JSON object a reply holds, or an empty one when it holds none.
pub fn object_in(text: &str) -> Map<String, Value> {
    if let Ok(Value::Object(found)) = serde_json::from_str::<Value>(text) {
        return found;
    }
    match braced(text).map(serde_json::from_str::<Value>) {
        Some(Ok(Value::Object(found))) => found,
        _ => Map::new(),
    }
}

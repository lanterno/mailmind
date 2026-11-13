//! The cost governor's daily limit (`mailmind.assistant.domain.limits`).

use serde_json::Value;

/// What the limit is with none set, in US dollars.
pub const DEFAULT_DAILY_LIMIT_USD: f64 = 15.0;

/// The configuration's `ai_daily_limit_usd`, in US dollars: a positive, finite number, or the default when it is
/// anything else or missing. A number may come as text, as `float()` reads it.
pub fn daily_limit_usd(value: Option<&Value>) -> f64 {
    let read = match value {
        Some(Value::Number(n)) => n.as_f64(),
        Some(Value::Bool(b)) => Some(if *b { 1.0 } else { 0.0 }),
        Some(Value::String(s)) => mm_kernel::text::strip(s).replace('_', "").parse::<f64>().ok(),
        _ => None,
    };
    read.filter(|v| *v > 0.0 && v.is_finite()).unwrap_or(DEFAULT_DAILY_LIMIT_USD)
}

/// The daily limit in micro-dollars, rounded as Python's `round` does, half to even.
pub fn limit_micros(value: Option<&Value>) -> i64 {
    (daily_limit_usd(value) * 1_000_000.0).round_ties_even() as i64
}

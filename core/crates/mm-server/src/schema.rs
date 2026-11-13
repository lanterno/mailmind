//! The words a request's schema is written in (`mailmind.http.schemas.common`), as pydantic reads a JSON body: what
//! each kind of field takes, and how it hands it on. A value a field refuses is the `bad request` 400; one it takes is
//! handed to the use case as the schema makes it (`model_dump`), so `"7"` for a number goes on as 7.
//!
//! A field is `T | None` unless it is required: null is taken as null, and the use case words what it makes of it.

use mm_kernel::errors::Problem;
use serde_json::{Map, Number, Value};

use crate::query;

/// What a client's numbers hold: a sum on one still fits a row's 64 bits.
const MOST: f64 = 9_007_199_254_740_992.0;

/// `Number`: what `kernel.parsed.as_int` reads as one, as Python's `int()` reads it: true is 1, 7.9 is 7, `" 7 "` is
/// 7; nothing past 2^53 either way.
pub fn number(value: &Value) -> Option<Value> {
    let read = match value {
        Value::Bool(b) => Some(i64::from(*b)),
        Value::Number(n) => match n.as_i64() {
            Some(i) => Some(i).filter(|i| i.unsigned_abs() <= 1 << 53),
            None => n.as_f64().filter(|f| f.is_finite() && f.trunc().abs() <= MOST).map(|f| f.trunc() as i64),
        },
        Value::String(s) => query::as_int(s),
        _ => None,
    };
    read.map(Value::from)
}

/// `Flag`: true or false, and nothing that reads as one.
pub fn flag(value: &Value) -> Option<Value> {
    value.is_boolean().then(|| value.clone())
}

/// `str`: text, and no number.
pub fn text(value: &Value) -> Option<Value> {
    value.is_string().then(|| value.clone())
}

/// A `Literal` of words: one of them.
pub fn choice(value: &Value, words: &[&str]) -> Option<Value> {
    value.as_str().filter(|word| words.contains(word)).map(|_| value.clone())
}

/// `Dollars`: a number as a page's number field sends one, as a float; true is no number, and nor is `"12"`.
pub fn dollars(value: &Value) -> Option<Value> {
    match value {
        Value::Number(n) => n.as_f64().and_then(Number::from_f64).map(Value::Number),
        _ => None,
    }
}

/// An object: the JSON object it is.
pub fn object(value: &Value) -> Option<Value> {
    value.is_object().then(|| value.clone())
}

/// What a field of the kind `take` reads makes of `body`'s value under `name`, into `dumped` when the body named it:
/// null as null, else what `take` makes of it, or `bad request`.
pub fn optional(
    body: &Map<String, Value>,
    name: &str,
    take: impl Fn(&Value) -> Option<Value>,
    dumped: &mut Map<String, Value>,
) -> Result<(), Problem> {
    match body.get(name) {
        None => Ok(()),
        Some(Value::Null) => {
            dumped.insert(name.to_owned(), Value::Null);
            Ok(())
        }
        Some(given) => {
            let taken = take(given).ok_or_else(Problem::bad_request)?;
            dumped.insert(name.to_owned(), taken);
            Ok(())
        }
    }
}

/// A field the body must name, as what `take` makes of it, or `bad request`.
pub fn required(
    body: &Map<String, Value>,
    name: &str,
    take: impl Fn(&Value) -> Option<Value>,
) -> Result<Value, Problem> {
    body.get(name).and_then(take).ok_or_else(Problem::bad_request)
}

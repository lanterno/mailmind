//! A POST's JSON body, as the Python app's `Route.handle` reads it and its request schemas let it through.
//!
//! The perimeter has judged a declared length, so a body runs past its cap only when it declared none, which is a
//! 413. A body counts only if its length was declared and it isn't empty: otherwise it is `{}`, whatever it holds.
//! What isn't a JSON object, and a field a schema would refuse, is the `bad request` 400. A field nobody reads is
//! ignored, as `extra="ignore"` ignores it.

use axum::body::Body;
use axum::http::{HeaderMap, Method, Request, Response, header};
use mm_kernel::errors::Problem;
use serde_json::{Map, Value};

use crate::answer;
use crate::problem;
use crate::serve::body_cap;

/// The fields of a POST's body.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Fields(Map<String, Value>);

/// Why a body wasn't read.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Unread {
    /// It ran past its cap without declaring a length.
    TooLarge,
    /// It isn't a JSON object.
    NotAnObject,
}

impl Unread {
    /// What the request is answered.
    pub fn answer(self) -> Response<Body> {
        match self {
            Unread::TooLarge => answer::problem_document(413, &problem::blank(413, "payload too large")),
            Unread::NotAnObject => answer::problem(&Problem::bad_request()),
        }
    }
}

/// The body of `request` as its fields.
pub async fn read(request: Request<Body>) -> Result<Fields, Unread> {
    let cap = usize::try_from(body_cap(request.method(), request.uri().path())).unwrap_or(usize::MAX);
    let declared = request.headers().contains_key(header::CONTENT_LENGTH);
    taken(request.into_body(), cap, declared).await
}

/// The body of a POST to `path` as a JSON object, for a route that takes its own parts apart; or what the request is
/// answered instead.
pub async fn object(path: &str, headers: &HeaderMap, body: Body) -> Result<Map<String, Value>, Box<Response<Body>>> {
    let cap = usize::try_from(body_cap(&Method::POST, path)).unwrap_or(usize::MAX);
    match taken(body, cap, headers.contains_key(header::CONTENT_LENGTH)).await {
        Ok(Fields(fields)) => Ok(fields),
        Err(unread) => Err(Box::new(unread.answer())),
    }
}

/// A body read up to `cap` as it comes. A client that leaves before its body has come is answered as one that sent
/// too much: no one hears it.
async fn taken(body: Body, cap: usize, declared: bool) -> Result<Fields, Unread> {
    let Ok(bytes) = axum::body::to_bytes(body, cap).await else { return Err(Unread::TooLarge) };
    if !declared || bytes.is_empty() {
        return Ok(Fields::default());
    }
    match serde_json::from_slice::<Value>(&bytes) {
        Ok(Value::Object(fields)) => Ok(Fields(fields)),
        _ => Err(Unread::NotAnObject),
    }
}

/// `kernel.parsed.as_int` of a JSON value: what `int()` reads it as, and None for what doesn't read as a number or
/// is past 2^53 either way.
pub fn as_int(given: &Value) -> Option<i64> {
    match given {
        Value::Bool(b) => Some(i64::from(*b)),
        Value::Number(n) => match n.as_i64() {
            Some(whole) => (whole.abs() <= MOST).then_some(whole),
            None => n.as_f64().filter(|f| f.is_finite() && f.abs() < LARGER).map(f64::trunc).and_then(|f| {
                // Past 2^53 has been refused, so the truncated value is a whole number an i64 holds.
                #[allow(clippy::cast_possible_truncation)]
                let whole = f as i64;
                (whole.abs() <= MOST).then_some(whole)
            }),
        },
        Value::String(text) => crate::query::as_int(text),
        _ => None,
    }
}

const MOST: i64 = 1 << 53;
/// A float this large truncates to more than MOST: refused before it is cast.
const LARGER: f64 = 9_007_199_254_740_994.0;

impl Fields {
    /// What the body says for `name`, a null included; None where it says nothing.
    pub fn given(&self, name: &str) -> Option<&Value> {
        self.0.get(name)
    }

    /// A `Number | None` field: None where it is left out or null, and the `bad request` refusal where it isn't one.
    pub fn number(&self, name: &str) -> Result<Option<i64>, Problem> {
        match self.0.get(name) {
            None | Some(Value::Null) => Ok(None),
            Some(given) => as_int(given).map(Some).ok_or_else(Problem::bad_request),
        }
    }

    /// A `Number` field a request must give.
    pub fn required_number(&self, name: &str) -> Result<i64, Problem> {
        self.number(name)?.ok_or_else(Problem::bad_request)
    }

    /// A `str | None` field: a JSON string, else None where it is left out or null; anything else is refused, since
    /// pydantic takes no number for a string.
    pub fn text(&self, name: &str) -> Result<Option<&str>, Problem> {
        match self.0.get(name) {
            None | Some(Value::Null) => Ok(None),
            Some(Value::String(text)) => Ok(Some(text)),
            Some(_) => Err(Problem::bad_request()),
        }
    }

    /// A `str` field a request must give.
    pub fn required_text(&self, name: &str) -> Result<&str, Problem> {
        self.text(name)?.ok_or_else(Problem::bad_request)
    }

    /// A `Flag | None` field (`StrictBool`): true or false, else None where it is left out or null.
    pub fn flag(&self, name: &str) -> Result<Option<bool>, Problem> {
        match self.0.get(name) {
            None | Some(Value::Null) => Ok(None),
            Some(Value::Bool(on)) => Ok(Some(*on)),
            Some(_) => Err(Problem::bad_request()),
        }
    }

    /// A `Literal[...] | None` field: one of `allowed`, else None where it is left out or null.
    pub fn one_of(&self, name: &str, allowed: &[&str]) -> Result<Option<&str>, Problem> {
        match self.text(name)? {
            Some(given) if !allowed.contains(&given) => Err(Problem::bad_request()),
            given => Ok(given),
        }
    }

    /// A `list[Model] | None` field, each item read by `item`: None where it is left out or null.
    pub fn list<T>(&self, name: &str, item: impl Fn(&Fields) -> Result<T, Problem>) -> Result<Option<Vec<T>>, Problem> {
        match self.0.get(name) {
            None | Some(Value::Null) => Ok(None),
            Some(Value::Array(items)) => items
                .iter()
                .map(|one| match one {
                    Value::Object(fields) => item(&Fields(fields.clone())),
                    _ => Err(Problem::bad_request()),
                })
                .collect::<Result<Vec<T>, Problem>>()
                .map(Some),
            Some(_) => Err(Problem::bad_request()),
        }
    }
}

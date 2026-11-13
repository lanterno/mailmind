//! A POST's body as the Python app's routes read it (`mailmind.http.route.Route.handle` and the request schemas of
//! `mailmind.http.schemas`): read with a cap as it comes, whatever Content-Length said, since the perimeter judged
//! only the header; with no Content-Length, or nothing in it, it is `{}`; and it must be a JSON object.
//!
//! A field is read as its schema read it, and a value the schema refused is the `bad request` refusal: text is text
//! or nothing, never a number; a number is `kernel.parsed.as_int`'s; a flag is true or false and nothing else.

use axum::body::Body;
use axum::extract::Request;
use axum::http::{Method, Response};
use mm_kernel::errors::Problem;
use serde_json::{Map, Value};

use crate::{answer, problem, query, serve};

/// A POST's JSON object.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Posted(pub Map<String, Value>);

/// The body of `request`, or the answer that refuses it: a 413 once it runs past the cap, and the `bad request` 400
/// for what isn't a JSON object.
pub async fn read(request: Request) -> Result<Posted, Box<Response<Body>>> {
    let declared = request.headers().contains_key("content-length");
    let cap = serve::body_cap(&Method::POST, request.uri().path());
    let Ok(body) = axum::body::to_bytes(request.into_body(), usize::try_from(cap).unwrap_or(usize::MAX)).await else {
        return Err(Box::new(answer::problem_document(413, &problem::blank(413, "payload too large"))));
    };
    if !declared || body.is_empty() {
        return Ok(Posted::default());
    }
    // json.loads takes a UTF-8 byte order mark before the text.
    let text = body.strip_prefix(b"\xef\xbb\xbf").unwrap_or(&body);
    match serde_json::from_slice::<Value>(text) {
        Ok(Value::Object(fields)) => Ok(Posted(fields)),
        _ => Err(Box::new(answer::problem(&Problem::bad_request()))),
    }
}

/// `kernel.parsed.as_int` of what JSON parsed: `int()`'s reading, and None for what doesn't read as a number or is
/// past 2^53 either way.
pub fn as_int(value: &Value) -> Option<i64> {
    const MOST: i64 = 1 << 53;
    let n = match value {
        Value::Bool(b) => i64::from(*b),
        Value::Number(n) => match (n.as_i64(), n.as_f64()) {
            (Some(i), _) => i,
            (None, Some(f)) if f.is_finite() && f.trunc().abs() <= MOST as f64 => f.trunc() as i64,
            _ => return None,
        },
        Value::String(s) => return query::as_int(s),
        _ => return None,
    };
    (n.abs() <= MOST).then_some(n)
}

impl Posted {
    /// Whether the request said `name` at all, null included (`model_dump(exclude_unset=True)`).
    pub fn has(&self, name: &str) -> bool {
        self.0.contains_key(name)
    }

    pub fn get(&self, name: &str) -> Option<&Value> {
        self.0.get(name).filter(|v| !v.is_null())
    }

    /// `str | None`.
    pub fn text(&self, name: &str) -> Result<Option<String>, Problem> {
        match self.get(name) {
            None => Ok(None),
            Some(Value::String(s)) => Ok(Some(s.clone())),
            Some(_) => Err(Problem::bad_request()),
        }
    }

    /// `Number | None`.
    pub fn number(&self, name: &str) -> Result<Option<i64>, Problem> {
        self.get(name).map(|v| as_int(v).ok_or_else(Problem::bad_request)).transpose()
    }

    /// A `Number` the request must give.
    pub fn required_number(&self, name: &str) -> Result<i64, Problem> {
        self.number(name)?.ok_or_else(Problem::bad_request)
    }

    /// `Flag | None`: true or false, nothing else.
    pub fn flag(&self, name: &str) -> Result<Option<bool>, Problem> {
        match self.get(name) {
            None => Ok(None),
            Some(Value::Bool(b)) => Ok(Some(*b)),
            Some(_) => Err(Problem::bad_request()),
        }
    }

    /// `Literal[...] | None`: one of `allowed`, or nothing.
    pub fn one_of(&self, name: &str, allowed: &[&str]) -> Result<Option<String>, Problem> {
        match self.text(name)? {
            Some(s) if !allowed.contains(&s.as_str()) => Err(Problem::bad_request()),
            given => Ok(given),
        }
    }

    /// `list[Any] | None`.
    pub fn list(&self, name: &str) -> Result<Option<&[Value]>, Problem> {
        match self.get(name) {
            None => Ok(None),
            Some(Value::Array(items)) => Ok(Some(items)),
            Some(_) => Err(Problem::bad_request()),
        }
    }
}

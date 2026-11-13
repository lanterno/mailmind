//! What a failure answers: a problem document (RFC 9457), as `mailmind.http.shared.problem` writes one.
//!
//! Its `type` is `/problems/<kind>` for one of the kernel's kinds, or `about:blank` where the status says all there
//! is, the perimeter's refusals among them, with the status's own phrase as its title. `detail` is what a person is
//! told, left out where the page words it; what else the page acts on follows as members of its own.

use mm_kernel::errors::{Kind, Problem};
use serde_json::{Map, Value};

pub const PROBLEM_JSON: &str = "application/problem+json";

/// The four members every problem document has, which `more` never overrides.
const MEMBERS: [&str; 4] = ["type", "title", "status", "detail"];

/// Each kind's status, its name in `type` and its title.
pub fn kind(kind: Kind) -> (u16, &'static str, &'static str) {
    match kind {
        Kind::NotFound => (404, "not-found", "Not found"),
        Kind::Conflict => (409, "conflict", "Conflict"),
        Kind::Locked => (423, "locked", "Locked"),
        Kind::Refused => (400, "refused", "Refused"),
        Kind::Unreachable => (502, "unreachable", "Unreachable"),
        Kind::Unreadable => (422, "unreadable", "Unreadable"),
        Kind::Busy => (409, "busy", "Busy"),
    }
}

/// A status's phrase, as Python's `http.HTTPStatus` has it.
pub fn phrase(status: u16) -> &'static str {
    match status {
        200 => "OK",
        204 => "No Content",
        302 => "Found",
        304 => "Not Modified",
        400 => "Bad Request",
        401 => "Unauthorized",
        402 => "Payment Required",
        403 => "Forbidden",
        404 => "Not Found",
        405 => "Method Not Allowed",
        409 => "Conflict",
        410 => "Gone",
        411 => "Length Required",
        413 => "Content Too Large",
        414 => "URI Too Long",
        415 => "Unsupported Media Type",
        422 => "Unprocessable Content",
        423 => "Locked",
        429 => "Too Many Requests",
        431 => "Request Header Fields Too Large",
        500 => "Internal Server Error",
        501 => "Not Implemented",
        502 => "Bad Gateway",
        503 => "Service Unavailable",
        504 => "Gateway Timeout",
        _ => "",
    }
}

fn document(kind_and_title: Option<(&str, &str)>, status: u16, detail: &str, more: &[(&str, Value)]) -> Value {
    let mut doc = Map::new();
    match kind_and_title {
        Some((name, title)) => {
            doc.insert("type".into(), Value::String(format!("/problems/{name}")));
            doc.insert("title".into(), Value::String(title.to_owned()));
        }
        None => {
            doc.insert("type".into(), Value::String("about:blank".into()));
            doc.insert("title".into(), Value::String(phrase(status).to_owned()));
        }
    }
    doc.insert("status".into(), Value::from(status));
    if !detail.is_empty() {
        doc.insert("detail".into(), Value::String(detail.to_owned()));
    }
    for (name, value) in more.iter().filter(|(name, _)| !MEMBERS.contains(name)) {
        doc.insert((*name).to_owned(), value.clone());
    }
    Value::Object(doc)
}

/// A failure that is none of the kernel's kinds: `about:blank`, titled with the status's phrase.
pub fn blank(status: u16, detail: &str) -> Value {
    document(None, status, detail, &[])
}

/// A use case's problem as its document, and the status it answers with.
pub fn of(problem: &Problem) -> (u16, Value) {
    let (status, name, title) = kind(problem.kind);
    (status, document(Some((name, title)), status, &problem.said, &problem.more))
}

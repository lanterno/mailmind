//! The answers a route gives, built the way the Python app's are: a body, its length and its type, and nothing a
//! route didn't ask for. The perimeter adds its headers to each (`perimeter::add_answer_headers`).

use axum::body::Body;
use axum::http::{HeaderValue, Response, StatusCode, header};
use mm_kernel::errors::{Failure, Problem};
use mm_kernel::log::{self, Field};
use serde_json::Value;

use crate::{json, problem};

const TAG: &str = "http";

/// An answer of `status` with `body`, its type `content_type`.
pub fn bytes(status: u16, content_type: &'static str, body: Vec<u8>) -> Response<Body> {
    let length = body.len();
    let mut answer = Response::new(Body::from(body));
    *answer.status_mut() = StatusCode::from_u16(status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
    let headers = answer.headers_mut();
    headers.insert(header::CONTENT_TYPE, HeaderValue::from_static(content_type));
    headers.insert(header::CONTENT_LENGTH, HeaderValue::from(length));
    answer
}

/// JSON, in `json.dumps`' bytes.
pub fn json(status: u16, value: &Value) -> Response<Body> {
    bytes(status, "application/json", json::dumps(value).into_bytes())
}

/// A problem document.
pub fn problem_document(status: u16, document: &Value) -> Response<Body> {
    bytes(status, problem::PROBLEM_JSON, json::dumps(document).into_bytes())
}

/// A use case's problem.
pub fn problem(problem: &Problem) -> Response<Body> {
    let (status, document) = problem::of(problem);
    problem_document(status, &document)
}

/// What a request that failed answers. A problem says what it says; anything else is the fixed 500, with the
/// method, the path and the fault's class logged and nothing else: an error's text can hold what a server or a
/// message said.
pub fn failure(failure: &Failure, method: &str, path: &str) -> Response<Body> {
    match failure {
        Failure::Problem(p) => problem(p),
        Failure::Fault(fault) => {
            log::error(
                TAG,
                "%(method)s %(path)s failed",
                &[(Field::Method, method), (Field::Path, path)],
                Some(fault.class),
            );
            problem_document(500, &problem::blank(500, "request failed"))
        }
    }
}

/// What a route the Python app answers and this server doesn't yet gets: a 501 that says so, a problem document
/// under /api/ and a line of text elsewhere.
pub fn not_yet(path: &str) -> Response<Body> {
    const SAID: &str = "not on the Rust server yet";
    if crate::perimeter::is_api(path) {
        problem_document(501, &problem::blank(501, SAID))
    } else {
        bytes(501, "text/plain", SAID.as_bytes().to_vec())
    }
}

/// What a path no route answers gets, under /api/ a problem document and elsewhere a line of text
/// (`mailmind.http.app._no_route`). GET and POST are all the app answers, so any other method is one it doesn't
/// implement.
pub fn no_route(method: &str, path: &str) -> Response<Body> {
    let (status, said) = if matches!(method, "GET" | "POST") { (404, "not found") } else { (501, "not implemented") };
    if crate::perimeter::is_api(path) {
        problem_document(status, &problem::blank(status, said))
    } else {
        bytes(status, "text/plain", said.as_bytes().to_vec())
    }
}

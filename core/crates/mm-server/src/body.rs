//! A POST's body as the Python app's routes read it (`mailmind.http.route.Route.handle`): read up to the route's cap
//! as it comes, whatever its Content-Length said, since the perimeter judged only the header; a body whose length
//! wasn't declared, or that is empty, read as `{}`; and whatever it says, read as JSON, which must be an object.
//!
//! What a route's schema then makes of each field is the route's (`schema`).

use axum::body::Body;
use axum::http::{HeaderMap, Method, Response, header};
use mm_kernel::errors::Problem;
use serde_json::{Map, Value};

use crate::{answer, problem, serve};

/// The body of a POST to `path`, as a JSON object; or what the request is answered instead: the 413 for a body that
/// ran past the cap without declaring its length, and `bad request` for one that isn't a JSON object.
pub async fn object(path: &str, headers: &HeaderMap, body: Body) -> Result<Map<String, Value>, Box<Response<Body>>> {
    let cap = usize::try_from(serve::body_cap(&Method::POST, path)).unwrap_or(usize::MAX);
    // A client that leaves before its body has come is answered as one that sent too much: no one hears it.
    let Ok(read) = axum::body::to_bytes(body, cap).await else {
        return Err(Box::new(answer::problem_document(413, &problem::blank(413, "payload too large"))));
    };
    let text: &[u8] = if headers.get(header::CONTENT_LENGTH).is_none() || read.is_empty() { b"{}" } else { &read };
    match serde_json::from_slice::<Value>(text) {
        Ok(Value::Object(object)) => Ok(object),
        _ => Err(Box::new(answer::problem(&Problem::bad_request()))),
    }
}

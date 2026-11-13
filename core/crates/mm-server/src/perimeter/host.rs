//! The Host check, against DNS rebinding.

use axum::http::HeaderMap;

use super::{Refusal, header, lowered, origin};

/// Block DNS rebinding: the Host must name this server, and an Origin, when sent, must be its own. A page on a
/// rebound name reaches 127.0.0.1 with its own Host, so without this it could read the mailbox and the page's CSRF
/// token.
pub fn refuse_host(headers: &HeaderMap, port: u16) -> Option<Refusal> {
    let host = lowered(headers, "host");
    let own_host = [format!("127.0.0.1:{port}"), format!("localhost:{port}")].iter().any(|own| own.as_bytes() == host);
    let foreign_origin =
        header(headers, "origin").is_some_and(|o| !o.is_empty() && !origin::is_own(&o.to_ascii_lowercase(), port));
    if !own_host || foreign_origin {
        return Some(Refusal { status: 403, error: "bad host/origin" });
    }
    None
}

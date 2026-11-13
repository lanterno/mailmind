//! The perimeter: what a request must pass before any route runs (`mailmind.platform.perimeter`), check for check.
//!
//! Each check reads only the request's headers, by name whatever their case, and the values passed in, and answers a
//! `Refusal`, or None when the request may go on. Every request reaches 127.0.0.1, so the danger is another page in
//! the browser:
//! - a page on a rebound name, which reaches this server with its own Host (`refuse_host`);
//! - another site's no-cors GET, an `<img>`, which carries this server's Host and no Origin (`refuse_cross_site`);
//! - another site's POST, which can't carry the page's CSRF token or its Origin (`refuse_mutation`).
//!
//! The API answers only the page's own requests, which browsers mark `Sec-Fetch-Site: same-origin`. Every answer, a
//! refusal included, carries the headers `answer_headers` names, and the page the policy `page_policy` writes.

mod csp;
mod csrf;
mod fetch_metadata;
mod headers;
mod host;
mod limits;
mod origin;

use axum::http::HeaderMap;

pub use csp::page_policy;
pub use csrf::{refuse_mutation, token_matches};
pub use fetch_metadata::{is_api, refuse_cross_site};
pub use headers::{add_answer_headers, answer_headers};
pub use host::refuse_host;
pub use limits::refuse_body;

/// What a check answers when a request may not go on: its status, and what a person is told.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Refusal {
    pub status: u16,
    pub error: &'static str,
}

/// A header as Starlette's `Headers.get` reads it: the first by that name, whatever its case, as its bytes.
fn header<'a>(headers: &'a HeaderMap, name: &str) -> Option<&'a [u8]> {
    headers.get(name).map(|v| v.as_bytes())
}

/// A header's value lowered as Python lowers one it read as Latin-1, "" when absent. Only ASCII letters change:
/// none of Latin-1's others lowers into ASCII, and every value it is compared with is ASCII.
fn lowered(headers: &HeaderMap, name: &str) -> Vec<u8> {
    header(headers, name).unwrap_or_default().to_ascii_lowercase()
}

/// A GET (or HEAD) must name this server and come from its page.
pub fn refuse_get(path: &str, headers: &HeaderMap, port: u16) -> Option<Refusal> {
    refuse_host(headers, port).or_else(|| refuse_cross_site(headers, path))
}

/// Any other method: the size cap first, then the host, where it came from, the token and the Origin.
pub fn refuse_post(path: &str, headers: &HeaderMap, port: u16, token: &str, cap: u64) -> Option<Refusal> {
    refuse_body(headers, cap).or_else(|| refuse_mutation(path, headers, port, token))
}

/// This run's CSRF token, which the page carries and every POST must: 32 bytes from the operating system's random
/// source, in URL-safe base64 without padding, as Python's `secrets.token_urlsafe(32)` writes one. A restart changes
/// it.
pub fn new_token() -> Result<String, getrandom::Error> {
    let mut drawn = [0u8; 32];
    getrandom::fill(&mut drawn)?;
    Ok(base64_url(&drawn))
}

fn base64_url(bytes: &[u8]) -> String {
    const ALPHABET: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    let mut out = String::with_capacity(bytes.len().div_ceil(3) * 4);
    for chunk in bytes.chunks(3) {
        let n = chunk.iter().enumerate().fold(0u32, |n, (i, &b)| n | (u32::from(b) << (16 - 8 * i)));
        for i in 0..=chunk.len() {
            out.push(char::from(ALPHABET[((n >> (18 - 6 * i)) & 63) as usize]));
        }
    }
    out
}

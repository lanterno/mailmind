//! What a POST must carry: the page's CSRF token and this server's Origin.

use axum::http::HeaderMap;

use super::{Refusal, header, lowered, origin, refuse_cross_site, refuse_host};

/// The request carries the page's CSRF token, compared in constant time: how long the comparison takes depends on
/// the token's length alone, never on where the two first differ. A token that isn't ASCII is a wrong one.
pub fn token_matches(headers: &HeaderMap, token: &str) -> bool {
    let sent = header(headers, "x-csrf-token").unwrap_or_default();
    let token = token.as_bytes();
    let mut differ = u8::from(sent.len() != token.len());
    for (i, &t) in token.iter().enumerate() {
        differ |= sent.get(i).copied().unwrap_or(!t) ^ t;
    }
    std::hint::black_box(differ) == 0
}

/// A POST must pass the host and cross-site checks, carry the CSRF token and carry this server's Origin: a browser
/// sends one with every POST, the page's own fetches included.
pub fn refuse_mutation(path: &str, headers: &HeaderMap, port: u16, token: &str) -> Option<Refusal> {
    if let Some(refusal) = refuse_host(headers, port).or_else(|| refuse_cross_site(headers, path)) {
        return Some(refusal);
    }
    if !token_matches(headers, token) {
        return Some(Refusal { status: 403, error: "bad or missing CSRF token" });
    }
    if !origin::is_own(&lowered(headers, "origin"), port) {
        return Some(Refusal { status: 403, error: "origin required" });
    }
    None
}

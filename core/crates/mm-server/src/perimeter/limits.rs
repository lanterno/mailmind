//! The cap on a request's body.

use axum::http::HeaderMap;

use super::{Refusal, header};

/// A body over `cap` bytes, or a length that isn't one, judged by Content-Length before the body is read. No
/// Content-Length, or an empty one, is a length of 0 here; what such a body holds is the route's to cap as it reads.
pub fn refuse_body(headers: &HeaderMap, cap: u64) -> Option<Refusal> {
    let refused = match header(headers, "content-length") {
        None | Some(b"") => false,
        Some(given) => !python_int(given).is_some_and(|length| (0..=i128::from(cap)).contains(&length)),
    };
    refused.then_some(Refusal { status: 413, error: "payload too large" })
}

/// A header's value as Python's `int()` reads the Latin-1 text of it, or None for what isn't a number. The HTTP
/// server has refused most of what int() would read and a length mustn't be (`+5`, ` 5`), so this rarely sees more
/// than digits.
fn python_int(given: &[u8]) -> Option<i128> {
    let text: String = given.iter().map(|&b| char::from(b)).collect();
    let text = mm_kernel::text::strip(&text);
    let (negative, digits) = match text.as_bytes().first() {
        Some(b'-') => (true, &text[1..]),
        Some(b'+') => (false, &text[1..]),
        _ => (false, text),
    };
    let well_formed = !digits.is_empty()
        && !digits.starts_with('_')
        && !digits.ends_with('_')
        && !digits.contains("__")
        && digits.chars().all(|c| c.is_ascii_digit() || c == '_');
    if !well_formed {
        return None;
    }
    // A number past what i128 holds is still a number, and past any cap.
    let value = digits
        .bytes()
        .filter(u8::is_ascii_digit)
        .try_fold(0i128, |n, d| n.checked_mul(10).and_then(|n| n.checked_add(i128::from(d - b'0'))))
        .unwrap_or(i128::MAX);
    Some(if negative { -value } else { value })
}

//! The headers every answer carries.

use axum::http::{HeaderMap, HeaderName, HeaderValue};

use super::is_api;

/// What every answer carries, unless its route set the header itself.
const ANSWER_HEADERS: [(&str, &str); 6] = [
    // a body is only ever what its Content-Type says
    ("x-content-type-options", "nosniff"),
    // a link out of the app names no page of it
    ("referrer-policy", "no-referrer"),
    // no other page may frame the app and steer clicks
    ("x-frame-options", "DENY"),
    // a page of another origin that opens the app, or that it opens, gets no handle on it
    ("cross-origin-opener-policy", "same-origin"),
    // no other origin may load an answer as an image, a script or the like
    ("cross-origin-resource-policy", "same-origin"),
    // the app asks for no device, sensor or payment; nothing it shows may either
    (
        "permissions-policy",
        "accelerometer=(), camera=(), display-capture=(), geolocation=(), gyroscope=(), hid=(), magnetometer=(), \
         microphone=(), midi=(), payment=(), serial=(), usb=()",
    ),
];

/// The API's answers hold the mailbox, so no cache keeps them.
const API_HEADERS: [(&str, &str); 7] = [
    ANSWER_HEADERS[0],
    ANSWER_HEADERS[1],
    ANSWER_HEADERS[2],
    ANSWER_HEADERS[3],
    ANSWER_HEADERS[4],
    ANSWER_HEADERS[5],
    ("cache-control", "no-store"),
];

/// The headers every answer to `path` carries, as (name, value) pairs.
pub fn answer_headers(path: &str) -> &'static [(&'static str, &'static str)] {
    if is_api(path) { &API_HEADERS } else { &ANSWER_HEADERS }
}

/// Add to `headers` each of those for `path` the answer didn't set itself.
pub fn add_answer_headers(headers: &mut HeaderMap, path: &str) {
    for &(name, value) in answer_headers(path) {
        let name = HeaderName::from_static(name);
        if !headers.contains_key(&name) {
            headers.insert(name, HeaderValue::from_static(value));
        }
    }
}

//! JSON as it parsed, off a file or a request, read as the Python app reads it (`mailmind.kernel.parsed`).

use serde_json::Value;

/// What a client's numbers hold: a sum on one still fits a row's 64 bits.
const MOST: i64 = 1 << 53;

/// `as_int`: a number as a request may give it, as Python's `int()` reads it (true is 1, 7.9 is 7, `" 7 "` is 7),
/// or None for what doesn't read as one or is past 2^53 either way.
pub fn as_int(value: &Value) -> Option<i64> {
    let read = match value {
        Value::Bool(b) => i64::from(*b),
        Value::Number(n) => match n.as_i64() {
            Some(whole) => whole,
            None => {
                let f = n.as_f64().filter(|f| f.is_finite() && f.trunc().abs() <= MOST as f64)?;
                // Past 2^53 has been refused, so the truncated value is a whole number an i64 holds.
                #[allow(clippy::cast_possible_truncation)]
                let whole = f.trunc() as i64;
                whole
            }
        },
        Value::String(text) => return text_as_int(text),
        _ => return None,
    };
    (read.abs() <= MOST).then_some(read)
}

/// `int()` of text: an optional sign and ASCII digits, which underscores may group, spaces around; or None.
pub fn text_as_int(given: &str) -> Option<i64> {
    let text = crate::text::strip(given);
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
    let value = digits
        .bytes()
        .filter(u8::is_ascii_digit)
        .try_fold(0i64, |n, d| n.checked_mul(10).and_then(|n| n.checked_add(i64::from(d - b'0'))))?;
    (value <= MOST).then_some(if negative { -value } else { value })
}

//! A GET's query string as the Python app's routes read it (`mailmind.http.route._first_of_each`): `parse_qs` over
//! its Latin-1 text, each name's first value, and a blank value no value at all; and a number in it as
//! `mailmind.kernel.parsed.as_int` reads one.

use mm_kernel::errors::Problem;

/// The query string's names and their first values, in the order the names first came.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Query(Vec<(String, String)>);

impl Query {
    /// `parse_qs(query.decode("latin-1"))`, keeping each name's first value.
    pub fn parse(query: &[u8]) -> Query {
        let text: String = query.iter().map(|&b| char::from(b)).collect();
        let mut first: Vec<(String, String)> = Vec::new();
        for pair in text.split('&').filter(|p| !p.is_empty()) {
            let Some((name, value)) = pair.split_once('=') else { continue };
            if value.is_empty() {
                continue;
            }
            let name = unquote(&name.replace('+', " "));
            if first.iter().all(|(seen, _)| *seen != name) {
                first.push((name, unquote(&value.replace('+', " "))));
            }
        }
        Query(first)
    }

    /// The value given for `name`, None when none was or it was blank.
    pub fn text(&self, name: &str) -> Option<&str> {
        self.0.iter().find(|(n, _)| n == name).map(|(_, v)| v.as_str())
    }

    /// A number given for `name` (the schemas' `Number`), `default` when none was: anything that isn't one is the
    /// `bad request` refusal.
    pub fn number(&self, name: &str, default: i64) -> Result<i64, Problem> {
        match self.text(name) {
            None => Ok(default),
            Some(given) => as_int(given).ok_or_else(Problem::bad_request),
        }
    }
}

/// What a client's numbers hold: a sum on one still fits a row's 64 bits.
const MOST: i64 = 1 << 53;

/// `kernel.parsed.as_int` of a string: `int()`'s reading, and None for what doesn't read as a number or is past
/// 2^53 either way. Digits other than ASCII's, which `int()` also reads, read as none here.
pub fn as_int(given: &str) -> Option<i64> {
    let text = mm_kernel::text::strip(given);
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

/// `urllib.parse.unquote` of text read as Latin-1: each run of ASCII has its `%XX` escapes made bytes and read as
/// UTF-8, a byte that isn't UTF-8 read as U+FFFD; every other character stays as it is.
fn unquote(text: &str) -> String {
    if !text.contains('%') {
        return text.to_owned();
    }
    let mut out = String::with_capacity(text.len());
    let mut ascii: Vec<u8> = Vec::new();
    for c in text.chars() {
        if c.is_ascii() {
            ascii.push(c as u8);
        } else {
            out.push_str(&unquote_ascii(&ascii));
            ascii.clear();
            out.push(c);
        }
    }
    out.push_str(&unquote_ascii(&ascii));
    out
}

fn unquote_ascii(run: &[u8]) -> String {
    let mut bytes = Vec::with_capacity(run.len());
    let mut i = 0;
    while i < run.len() {
        let escaped = (run[i] == b'%')
            .then(|| run.get(i + 1..i + 3))
            .flatten()
            .and_then(|hex| std::str::from_utf8(hex).ok())
            .and_then(|hex| u8::from_str_radix(hex, 16).ok().filter(|_| hex.bytes().all(|b| b.is_ascii_hexdigit())));
        match escaped {
            Some(byte) => {
                bytes.push(byte);
                i += 3;
            }
            None => {
                bytes.push(run[i]);
                i += 1;
            }
        }
    }
    String::from_utf8_lossy(&bytes).into_owned()
}

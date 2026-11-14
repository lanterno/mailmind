//! Text as Python's `str` cuts and shows it, where a prompt or an answer depends on it: lines, a cut by characters, and
//! `str()` of what a model's JSON held.

use serde_json::Value;

/// Whether `c` ends a line for `str.splitlines()`.
fn ends_line(c: char) -> bool {
    matches!(c, '\n' | '\r' | '\u{b}' | '\u{c}' | '\u{1c}' | '\u{1d}' | '\u{1e}' | '\u{85}' | '\u{2028}' | '\u{2029}')
}

/// `str.splitlines()`: the lines, without their ends; `\r\n` is one end.
pub fn lines(s: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let mut start = 0;
    let mut chars = s.char_indices().peekable();
    while let Some((i, c)) = chars.next() {
        if !ends_line(c) {
            continue;
        }
        out.push(&s[start..i]);
        let mut next = i + c.len_utf8();
        if c == '\r'
            && let Some((j, '\n')) = chars.peek().copied()
        {
            chars.next();
            next = j + 1;
        }
        start = next;
    }
    if start < s.len() {
        out.push(&s[start..]);
    }
    out
}

/// `s[:n]`: the first `n` characters.
pub fn cut(s: &str, n: usize) -> &str {
    match s.char_indices().nth(n) {
        Some((at, _)) => &s[..at],
        None => s,
    }
}

/// How many characters `s` holds: `len(s)`.
pub fn length(s: &str) -> usize {
    s.chars().count()
}

/// `str.strip()`.
pub fn strip(s: &str) -> &str {
    mm_kernel::text::strip(s)
}

/// Whether Python counts `value` true.
pub fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(on) => *on,
        Value::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(items) => !items.is_empty(),
        Value::Object(members) => !members.is_empty(),
    }
}

/// `str(value)` of what JSON parsed: a string as it is, anything else as Python writes it.
pub fn python_str(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        other => repr(other),
    }
}

/// `int(value)` of what JSON parsed, None where Python's would raise.
pub fn python_int(value: &Value) -> Option<i64> {
    match value {
        Value::Bool(b) => Some(i64::from(*b)),
        Value::Number(n) => n.as_i64().or_else(|| n.as_f64().filter(|f| f.is_finite()).map(|f| f.trunc() as i64)),
        Value::String(s) => strip(s).replace('_', "").parse().ok(),
        _ => None,
    }
}

/// `repr(value)` of what JSON parsed.
pub fn repr(value: &Value) -> String {
    match value {
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::Number(n) => match (n.as_i64(), n.as_u64(), n.as_f64()) {
            (Some(i), _, _) => i.to_string(),
            (None, Some(u), _) => u.to_string(),
            (_, _, Some(f)) => float_repr(f),
            _ => n.to_string(),
        },
        Value::String(s) => string_repr(s),
        Value::Array(items) => format!("[{}]", items.iter().map(repr).collect::<Vec<_>>().join(", ")),
        Value::Object(members) => format!(
            "{{{}}}",
            members.iter().map(|(k, v)| format!("{}: {}", string_repr(k), repr(v))).collect::<Vec<_>>().join(", ")
        ),
    }
}

fn float_repr(f: f64) -> String {
    if f.is_infinite() {
        return if f > 0.0 { "inf".into() } else { "-inf".into() };
    }
    let text = format!("{f:?}");
    match text.split_once('e') {
        Some((mantissa, exp)) => {
            let exp: i32 = exp.parse().unwrap_or(0);
            let mantissa = mantissa.strip_suffix(".0").unwrap_or(mantissa);
            format!("{mantissa}e{}{:02}", if exp < 0 { '-' } else { '+' }, exp.abs())
        }
        None => text,
    }
}

/// `repr(s)`: single quotes unless it holds one and no double quote.
fn string_repr(s: &str) -> String {
    let quote = if s.contains('\'') && !s.contains('"') { '"' } else { '\'' };
    let mut out = String::with_capacity(s.len() + 2);
    out.push(quote);
    for c in s.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if !mm_kernel::text::is_printable(c) => {
                let n = u32::from(c);
                if n < 0x100 {
                    out.push_str(&format!("\\x{n:02x}"));
                } else if n < 0x10000 {
                    out.push_str(&format!("\\u{n:04x}"));
                } else {
                    out.push_str(&format!("\\U{n:08x}"));
                }
            }
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

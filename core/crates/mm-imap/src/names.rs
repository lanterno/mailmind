//! Values other than a folder name as IMAP command arguments, and Gmail's labels both ways (`imap_names`).
//!
//! A value goes only as a quoted string that can't end early or start another line: `imap_quote` refuses a CR, an LF
//! or a NUL, which a sender can fold into a Message-ID header so the ID stored holds a line break.

/// A value as an IMAP quoted string, or None when no quoted string can carry it.
pub fn imap_quote(value: &str) -> Option<String> {
    if value.contains(['\r', '\n', '\0']) {
        return None;
    }
    Some(format!("\"{}\"", value.replace('\\', "\\\\").replace('"', "\\\"")))
}

/// Whether a label is one of Gmail's system ones (`\Inbox`), which goes as it is.
fn is_system(label: &str) -> bool {
    label.strip_prefix('\\').is_some_and(|rest| !rest.is_empty() && rest.chars().all(|c| c.is_ascii_alphabetic()))
}

/// Gmail's labels as an X-GM-LABELS list: a system label as it is, any other quoted. None when one can't be sent.
pub fn gmail_labels(labels: &[&str]) -> Option<String> {
    let quoted: Option<Vec<String>> =
        labels.iter().map(|l| if is_system(l) { Some((*l).to_owned()) } else { imap_quote(l) }).collect();
    Some(format!("({})", quoted?.join(" ")))
}

/// A label as a FETCH answer named it (a quoted one with its escapes in place), as a person reads it: unescaped, and
/// IMAP's modified UTF-7 decoded. A system label is as it came.
pub fn label_name(raw: &str) -> String {
    let mut unescaped = String::with_capacity(raw.len());
    let mut chars = raw.chars();
    while let Some(c) = chars.next() {
        if c == '\\'
            && let Some(next) = chars.clone().next()
            && (next == '"' || next == '\\')
        {
            unescaped.push(next);
            chars.next();
            continue;
        }
        unescaped.push(c);
    }
    if is_system(&unescaped) { unescaped } else { modified_utf7(&unescaped) }
}

/// IMAP's modified UTF-7 (RFC 3501 5.1.3): `&-` is an ampersand, `&...-` base64 of UTF-16 with `,` for `/`. A run
/// that won't decode stays as it came.
fn modified_utf7(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut rest = text;
    while let Some(start) = rest.find('&') {
        out.push_str(&rest[..start]);
        let after = &rest[start + 1..];
        let Some(end) = after.find('-') else {
            out.push_str(&rest[start..]);
            return out;
        };
        let run = &after[..end];
        if run.is_empty() {
            out.push('&');
        } else {
            match decode_run(run) {
                Some(decoded) => out.push_str(&decoded),
                None => out.push_str(&rest[start..start + 1 + end + 1]),
            }
        }
        rest = &after[end + 1..];
    }
    out.push_str(rest);
    out
}

fn decode_run(run: &str) -> Option<String> {
    const ALPHABET: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+,";
    let mut bits: u32 = 0;
    let mut held = 0u32;
    let mut bytes = Vec::with_capacity(run.len());
    for b in run.bytes() {
        let value = ALPHABET.iter().position(|&a| a == b)? as u32;
        bits = (bits << 6) | value;
        held += 6;
        if held >= 8 {
            held -= 8;
            bytes.push((bits >> held) as u8);
            bits &= (1 << held) - 1;
        }
    }
    if bytes.len() % 2 != 0 {
        return None;
    }
    let units: Vec<u16> = bytes.chunks(2).map(|p| u16::from_be_bytes([p[0], p[1]])).collect();
    String::from_utf16(&units).ok()
}

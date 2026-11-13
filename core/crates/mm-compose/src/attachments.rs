//! A message's files (`compose.domain.attachments`): those a composer sends, decoded and capped, and those a forward
//! takes along. And the time a person asks a send to go at (`compose.domain.schedule`), judged before the original is
//! fetched, so a bad one costs nothing.

use std::collections::BTreeSet;

use serde_json::Value;

use crate::markdown;
use crate::spec::{Attachment, MAX_ATTACH_BYTES};

/// What a composer may put in the text (`PictureType`).
pub const PICTURES: [&str; 4] = ["image/png", "image/jpeg", "image/gif", "image/webp"];

/// The files a composer sent, each `{filename, content_type, b64, cid}`, decoded and capped (`decode`). Err is the
/// words a person is told.
pub fn decode(items: Option<&[Value]>) -> Result<Vec<Attachment>, &'static str> {
    let mut out = Vec::new();
    let mut total = 0usize;
    for item in items.unwrap_or_default() {
        let Value::Object(a) = item else { return Err("bad attachment") };
        let filename = match a.get("filename") {
            None | Some(Value::Null) => None,
            Some(Value::String(name)) => Some(name.as_str()),
            Some(_) => return Err("bad attachment"),
        };
        let b64 = match a.get("b64") {
            None | Some(Value::Null) => "",
            Some(Value::String(text)) => text.as_str(),
            Some(Value::Bool(false)) => "",
            Some(Value::Number(n)) if n.as_f64() == Some(0.0) => "",
            Some(Value::Array(v)) if v.is_empty() => "",
            Some(Value::Object(v)) if v.is_empty() => "",
            Some(_) => return Err("bad attachment encoding"),
        };
        let data = strict_base64(b64).ok_or("bad attachment encoding")?;
        total += data.len();
        if total > MAX_ATTACH_BYTES {
            return Err("attachments too large");
        }
        let mut content_type = match a.get("content_type") {
            None | Some(Value::Null) => "application/octet-stream".to_owned(),
            Some(Value::String(t)) if t.is_empty() => "application/octet-stream".to_owned(),
            Some(Value::String(t)) => t.clone(),
            Some(other) if !python_truthy(other) => "application/octet-stream".to_owned(),
            Some(other) => python_str(other),
        };
        if content_type.to_lowercase().starts_with("multipart/") {
            // A browser types .mhtml multipart/related: written as one, it has no boundary and every reader skips it.
            content_type = "application/octet-stream".to_owned();
        }
        let cid = match a.get("cid") {
            None | Some(Value::Null) => None,
            Some(Value::String(token)) if markdown::is_token(token) => Some(token.clone()),
            Some(_) => return Err("bad attachment"),
        };
        let essence =
            content_type.split(';').next().map(|t| mm_kernel::text::strip(t).to_lowercase()).unwrap_or_default();
        out.push(Attachment {
            filename: filename.filter(|f| !f.is_empty()).unwrap_or("attachment").to_owned(),
            content_type,
            data,
            params: Vec::new(),
            cid: cid.filter(|_| PICTURES.contains(&essence.as_str())),
        });
    }
    Ok(out)
}

/// Python's truth of a JSON value.
fn python_truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// `str(value)` of a JSON value that isn't text, as Python writes it.
fn python_str(v: &Value) -> String {
    match v {
        Value::Bool(true) => "True".to_owned(),
        Value::Bool(false) => "False".to_owned(),
        other => other.to_string(),
    }
}

/// `base64.b64decode(text, validate=True)`: the standard alphabet only, padded right, nothing after the padding.
pub fn strict_base64(text: &str) -> Option<Vec<u8>> {
    let bytes = text.as_bytes();
    let data_len = bytes.iter().position(|b| *b == b'=').unwrap_or(bytes.len());
    let (data, padding) = bytes.split_at(data_len);
    if !padding.iter().all(|b| *b == b'=') || (data.is_empty() && !padding.is_empty()) {
        return None;
    }
    let needed = match data.len() % 4 {
        0 => 0,
        2 => 2,
        3 => 1,
        _ => return None,
    };
    if padding.len() != needed {
        return None;
    }
    let value = |b: u8| -> Option<u32> {
        match b {
            b'A'..=b'Z' => Some(u32::from(b - b'A')),
            b'a'..=b'z' => Some(u32::from(b - b'a') + 26),
            b'0'..=b'9' => Some(u32::from(b - b'0') + 52),
            b'+' => Some(62),
            b'/' => Some(63),
            _ => None,
        }
    };
    let mut out = Vec::with_capacity(data.len() / 4 * 3 + 2);
    for chunk in data.chunks(4) {
        let mut n = 0u32;
        for (i, b) in chunk.iter().enumerate() {
            n |= value(*b)? << (18 - 6 * i);
        }
        let [_, a, b, c] = n.to_be_bytes();
        out.push(a);
        if chunk.len() > 2 {
            out.push(b);
        }
        if chunk.len() > 3 {
            out.push(c);
        }
    }
    Some(out)
}

/// The tokens of those a mark in the text may show (`pictures_of`).
pub fn pictures_of(files: &[Attachment]) -> BTreeSet<String> {
    files.iter().filter_map(|a| a.cid.clone()).collect()
}

/// Of the files a forward's original holds, those that go with the ones added under the budget: the first that
/// doesn't fit ends it (`take_along`).
pub fn take_along(have: &[Attachment], found: Vec<Attachment>) -> Vec<Attachment> {
    let mut total: usize = have.iter().map(|a| a.data.len()).sum();
    let mut taken = Vec::new();
    for a in found {
        if total + a.data.len() > MAX_ATTACH_BYTES {
            break;
        }
        total += a.data.len();
        taken.push(a);
    }
    taken
}

/// The files a stored message carries that a forward takes along (`extract`): those `travels` names, up to the
/// budget, read with the core's own parse, whose parts the Python app's reader numbers the same way.
pub fn extract(raw: Option<&[u8]>) -> Vec<Attachment> {
    let Some(raw) = raw.filter(|r| !r.is_empty()) else { return Vec::new() };
    let mut out = Vec::new();
    let mut total = 0usize;
    for part in mm_content::mime::parse(raw).attachments.into_iter().filter(|a| a.travels) {
        let Some((data, content_type)) = mm_content::mime::extract_part(raw, part.part_id) else { continue };
        total += data.len();
        if total > MAX_ATTACH_BYTES {
            break;
        }
        // The parse names a nameless file by its place, where the Python app's forward names it "attachment".
        let made_up = format!("part-{}", part.part_id);
        out.push(Attachment {
            filename: if part.filename == made_up { "attachment".to_owned() } else { part.filename },
            content_type,
            data: data.into_owned(),
            params: Vec::new(),
            cid: None,
        });
    }
    out
}

/// A send's time (`schedule.FURTHEST`): seconds ahead it may be scheduled.
pub const FURTHEST: i64 = 366 * 86_400;

/// Epoch seconds, or 0 for now, after the grace window (`schedule.send_after`). A time that has passed is refused
/// rather than quietly sent now. `now_micros` is the clock's.
pub fn send_after(asked: Option<i64>, now_micros: i64) -> Result<i64, &'static str> {
    let at = asked.unwrap_or(0);
    let at_micros = i128::from(at) * 1_000_000;
    if at != 0 && at_micros <= i128::from(now_micros) {
        return Err("that send time has passed");
    }
    if at != 0 && at_micros >= i128::from(now_micros) + i128::from(FURTHEST) * 1_000_000 {
        return Err("pick a send time within a year");
    }
    Ok(at)
}

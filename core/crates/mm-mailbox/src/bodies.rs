//! A message opened (`mailbox.application.bodies`): its body, from its server where it isn't stored, sanitized for
//! the reader's sandboxed frame; the document that frame loads; a part of it as a download, and an image's preview.
//!
//! A frame shows what it is sent, so the documents answer a failure as a document too, with its status.

use std::fmt;
use std::sync::Arc;

use mm_content::frame;
use mm_content::mime::{self, Attachment, Parsed};
use mm_kernel::errors::{Failure, Fault, Problem};
use serde_json::{Value, json};

use crate::inline_images;
use crate::ports::{BodyFetch, NOT_YET, Opening, Row, Sanitizer, StoredBodies};

/// What a frame says of a copy that isn't there.
pub const GONE: &str = "This message isn\u{2019}t here any more.";
/// What a preview says of a part it can't show.
pub const NO_PREVIEW: &str = "This file can\u{2019}t be previewed. Download it to see it.";
/// What a frame says when its document failed.
pub const FAILED: &str = "Couldn\u{2019}t load this message.";
/// What a preview says when it failed.
pub const PREVIEW_FAILED: &str = "Couldn\u{2019}t load this file.";
/// What a preview holds of one image, as its document carries the bytes.
pub const PREVIEW_MAX_BYTES: usize = 10 << 20;
/// The characters of a body's HTML read before it is sanitized.
const HTML_CAP: usize = 2_000_000;

/// A document for a frame, and the status it is answered with.
pub type Document = (u16, String);

/// The row a copy names now, its key as read and its uid as read.
type Named = (Option<i64>, Option<i64>, Option<i64>);

/// A part of a message, to download.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Part {
    pub data: Vec<u8>,
    pub content_type: Value,
    pub filename: Value,
}

/// A column as stored, null when the row hasn't it.
fn col<'a>(r: &'a Row, name: &str) -> &'a Value {
    r.get(name).unwrap_or(&Value::Null)
}

fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// A number a query gave as text, as the frame's routes read one: ASCII digits and nothing else, up to 2^53.
fn as_number(text: &str) -> Option<i64> {
    if text.is_empty() || !text.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    text.parse::<i64>().ok().filter(|n| *n <= 1 << 53)
}

fn not_found() -> Failure {
    Failure::Problem(Problem::not_found("not found"))
}

/// A part as the reader lists it (`mime.Attachment`).
fn listed(a: &Attachment) -> Value {
    json!({
        "part_id": a.part_id.to_string(),
        "filename": a.filename,
        "content_type": a.content_type,
        "size": a.size,
        "content_id": a.content_id,
        "is_inline": i64::from(a.is_inline),
        "travels": a.travels,
    })
}

pub struct Bodies {
    stored: Arc<dyn StoredBodies>,
    fetch: Arc<dyn BodyFetch>,
    opening: Arc<dyn Opening>,
    sanitizer: Arc<dyn Sanitizer>,
}

impl fmt::Debug for Bodies {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Bodies").finish_non_exhaustive()
    }
}

impl Bodies {
    /// `sanitizer` makes a body's HTML safe for the reader and its frame: the sanitizer itself, which only the test
    /// launcher swaps for one it can take away.
    pub fn new(
        stored: Arc<dyn StoredBodies>,
        fetch: Arc<dyn BodyFetch>,
        opening: Arc<dyn Opening>,
        sanitizer: Arc<dyn Sanitizer>,
    ) -> Bodies {
        Bodies { stored, fetch, opening, sanitizer }
    }

    /// The row the copy names now, or `not found`.
    pub fn copy_pk(&self, key: i64, folder: &str, uid: Option<i64>) -> Result<i64, Failure> {
        self.stored.copy_pk(key, folder, uid)?.ok_or_else(not_found)
    }

    /// A parsed message's HTML as its frame may show it, None when it has none, and how many remote images and CSS
    /// urls were left out.
    fn safe_html(
        &self,
        pk: i64,
        parsed: &Parsed<'_>,
        load_images: bool,
    ) -> Result<(Option<String>, usize, usize), Fault> {
        let Some(html) = parsed.html.as_deref().filter(|h| !h.is_empty()) else {
            return Ok((None, 0, 0));
        };
        let html = match html.char_indices().nth(HTML_CAP) {
            Some((cut, _)) => &html[..cut],
            None => html,
        };
        let clean = self.sanitizer.sanitize(html, load_images, true);
        let mut safe = clean.html;
        if safe.contains("cid:") {
            let raw = self.stored.raw(pk)?.unwrap_or_default();
            if !raw.is_empty() {
                let parts = mime::inline_parts(&raw);
                if !parts.is_empty() {
                    safe = inline_images::embed(&safe, &parts);
                }
            }
        }
        Ok((Some(safe), clean.blocked.images, clean.blocked.css_urls))
    }

    /// A message opened, or for a `prefetch` whether its body is stored or on its way.
    pub fn message_body(&self, pk: i64, prefetch: bool) -> Result<Value, Failure> {
        let row = self.stored.message(pk)?.ok_or_else(not_found)?;
        if prefetch {
            if truthy(col(&row, "body_fetched")) {
                return Ok(json!({"ok": true, "prefetched": true}));
            }
            return Ok(json!({"ok": true, "prefetched": self.fetch.prefetch(pk)?}));
        }
        let raw = self.fetch.fetch(pk)?.ok_or_else(not_found)?;
        let parsed = mime::parse(&raw);
        let (html, blocked_images, blocked_css_urls) = self.safe_html(pk, &parsed, false)?;
        let (invite, invite_part) = self.opening.invite_of(pk, &parsed.attachments, &raw)?;
        let sender = self.opening.sender_facts(col(&row, "from_addr").as_str())?;
        let mut answer = json!({
            "ok": true,
            "key": col(&row, "message_key"),
            "folder": col(&row, "folder"),
            "uid": col(&row, "uid"),
            "html": html,
            "text": parsed.text.as_deref(),
            "has_html": parsed.html.as_deref().is_some_and(|h| !h.is_empty()),
            "attachments": parsed.attachments.iter().map(listed).collect::<Vec<_>>(),
            "invite": invite,
            "invite_part": invite_part,
            "blocked_images": blocked_images,
            "blocked_css_urls": blocked_css_urls,
            "to": parsed.to,
            "cc": parsed.cc,
            "date": parsed.date,
            "reply_to": parsed.reply_to,
            "from_name": col(&row, "from_name"),
            "from_addr": col(&row, "from_addr"),
            "sender": sender,
            "subject": col(&row, "subject"),
            "account": col(&row, "account"),
            "thread_id": col(&row, "thread_id"),
            "message_id": col(&row, "message_id"),
            "in_reply_to": col(&row, "in_reply_to"),
            "references": col(&row, "refs"),
        });
        if let Value::Object(fields) = &mut answer {
            fields.extend(self.opening.draft_info(&row)?);
        }
        Ok(answer)
    }

    /// The row of the copy these name, its key as read and its uid as read: a uid left out ("") is the only copy
    /// there.
    fn copy_named(&self, key: &str, folder: &str, uid: &str) -> Result<Named, Fault> {
        let number = as_number(key);
        let at = if uid.is_empty() { None } else { as_number(uid) };
        let pk = match number {
            Some(n) if uid.is_empty() || at.is_some() => self.stored.copy_pk(n, folder, at)?,
            _ => None,
        };
        Ok((pk, number, at))
    }

    /// The document a frame of the row `pk` loads, so long as its key is `key` and its uid `uid` where one is given:
    /// a pk can come to name another message, and a frame never shows another's body.
    fn as_document(&self, pk: i64, key: i64, load_images: bool, uid: Option<i64>) -> Result<Document, Fault> {
        let row = self.stored.message(pk)?;
        let raw = match row {
            Some(row)
                if col(&row, "message_key").as_i64() == Some(key)
                    && uid.is_none_or(|uid| col(&row, "uid").as_i64() == Some(uid)) =>
            {
                self.fetch.fetch(pk)?
            }
            _ => None,
        };
        let Some(raw) = raw else { return Ok((404, frame::of_text(Some(GONE)))) };
        let parsed = mime::parse(&raw);
        if parsed.html.as_deref().is_none_or(str::is_empty) {
            return Ok((200, frame::of_text(parsed.text.as_deref())));
        }
        Ok((200, frame::of_html(self.safe_html(pk, &parsed, load_images)?.0.as_deref())))
    }

    /// The document the frame of the copy `key`, `folder` and `uid` loads: a key, folder and uid that name no copy
    /// now are a document saying so. A part the Rust server can't do yet is the `NOT_YET` fault.
    pub fn copy_document(&self, key: &str, folder: &str, uid: &str, load_images: bool) -> Result<Document, Fault> {
        let done = self.copy_named(key, folder, uid).and_then(|named| match named {
            (Some(pk), Some(number), at) => self.as_document(pk, number, load_images, at),
            _ => Ok((404, frame::of_text(Some(GONE)))),
        });
        match done {
            Err(fault) if fault == NOT_YET => Err(fault),
            Err(_) => Ok((500, frame::of_text(Some(FAILED)))),
            Ok(document) => Ok(document),
        }
    }

    /// The document the preview of a part loads: the image it holds, of the kinds `frame::image_type` finds in its
    /// bytes, whatever its sender called it, and otherwise a document saying why not.
    pub fn preview_document(&self, key: &str, folder: &str, uid: &str, part_id: &str) -> Result<Document, Fault> {
        let done = (|| {
            let (Some(pk), _, _) = self.copy_named(key, folder, uid)? else {
                return Ok((404, frame::of_text(Some(NO_PREVIEW))));
            };
            let Ok(part) = self.attachment(pk, part_id)? else {
                return Ok((404, frame::of_text(Some(NO_PREVIEW))));
            };
            match frame::image_type(&part.data) {
                Some(media_type) if part.data.len() <= PREVIEW_MAX_BYTES => {
                    Ok((200, frame::of_image(&part.data, media_type)))
                }
                _ => Ok((200, frame::of_text(Some(NO_PREVIEW)))),
            }
        })();
        match done {
            Err(fault) if fault == NOT_YET => Err(fault),
            Err(_) => Ok((500, frame::of_text(Some(PREVIEW_FAILED)))),
            Ok(document) => Ok(document),
        }
    }

    /// A part of a message: its bytes, and the content type and filename kept of it; or what says why not, `not
    /// found` for a message that has gone and `no such part` for a part it hasn't.
    pub fn attachment(&self, pk: i64, part_id: &str) -> Result<Result<Part, &'static str>, Fault> {
        if self.stored.message(pk)?.is_none() {
            return Ok(Err("not found"));
        }
        // Its body and what is kept of its parts, fetched first where they aren't stored.
        self.fetch.fetch(pk)?;
        let (Some(kept), Some(raw)) = (self.stored.part(pk, part_id)?, self.stored.raw(pk)?) else {
            return Ok(Err("no such part"));
        };
        let want = mm_kernel::text::strip(part_id).parse::<u32>().ok().filter(|n| *n > 0);
        let Some((data, _)) = want.filter(|_| !raw.is_empty()).and_then(|want| mime::extract_part(&raw, want)) else {
            return Ok(Err("no such part"));
        };
        Ok(Ok(Part {
            data: data.into_owned(),
            content_type: col(&kept, "content_type").clone(),
            filename: col(&kept, "filename").clone(),
        }))
    }
}

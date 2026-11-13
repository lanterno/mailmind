//! The leaves of a message in the order the Python app numbered them, and what each says of itself.
//!
//! The Python app walked a message depth first, an attached email as one leaf whatever it held, and counted every
//! part that is not a multipart from 1. Stored part ids are those numbers, so this counts the same way.

use mail_parser::{HeaderName, Message, MessagePart};

use super::header;

/// A part as the walk meets it.
pub(super) enum Node<'m> {
    /// A leaf and its number.
    Leaf(u32, MessagePart<'m>),
    /// A multipart that holds no parts: it named no boundary, or none was found. What it holds is plain text, and
    /// it takes no number.
    Unparted(MessagePart<'m>),
}

/// The parts of a message, depth first.
pub(super) struct Walk<'m> {
    stack: Vec<MessagePart<'m>>,
    counted: u32,
}

impl<'m> Walk<'m> {
    pub(super) fn new(message: &'m Message<'_>) -> Self {
        Walk { stack: vec![message.root().root_part()], counted: 0 }
    }
}

impl<'m> Iterator for Walk<'m> {
    type Item = Node<'m>;

    fn next(&mut self) -> Option<Node<'m>> {
        while let Some(part) = self.stack.pop() {
            if part.is_multipart() {
                let before = self.stack.len();
                let last = part.children().len().saturating_sub(1);
                let kept: Vec<_> = part
                    .children()
                    .enumerate()
                    .filter(|(at, child)| *at == last || !between_delimiters(child))
                    .collect();
                self.stack.extend(kept.into_iter().rev().map(|(_, child)| child));
                if self.stack.len() == before {
                    return Some(Node::Unparted(part));
                }
                continue;
            }
            if maintype(&part).eq_ignore_ascii_case("multipart") {
                return Some(Node::Unparted(part));
            }
            self.counted += 1;
            return Some(Node::Leaf(self.counted, part));
        }
        None
    }
}

/// A part with nothing in it at all, between two delimiters that follow each other: Python's parser read the second
/// as repeating the first, and no part stood between them. Before the close delimiter it did find one, empty.
fn between_delimiters(part: &MessagePart<'_>) -> bool {
    part.headers().is_empty() && part.raw().is_empty()
}

/// The type a part's Content-Type gives, before the slash.
fn maintype<'m>(part: &MessagePart<'m>) -> &'m str {
    part.content_type().map_or("", |content_type| content_type.ctype())
}

/// What a leaf says of itself in its headers, read as Python's `email` read them.
pub(super) struct Facts {
    /// The media type, lowercase: `text/plain` for a type that won't read.
    pub content_type: String,
    /// The first Content-Disposition's type, lowercase, or empty.
    pub disposition: String,
    /// The file name it gives, if it gives one.
    pub filename: Option<String>,
    /// The first Content-ID, without angle brackets, if it has one.
    pub content_id: Option<String>,
}

impl Facts {
    pub(super) fn of(part: &MessagePart<'_>) -> Facts {
        let headers = part.headers();
        let disposition_header = headers.all(HeaderName::ContentDisposition).next();
        let disposition = disposition_header.map_or_else(String::new, |found| header::first_word(found.raw_value()));
        let named = |content_type: Option<mail_parser::ContentType<'_>>, name: &str| {
            content_type.and_then(|content_type| content_type.attribute(name)).map(|value| value.trim().to_owned())
        };
        let filename = match disposition_header.and_then(|found| found.value().as_content_type()) {
            Some(parsed) if parsed.has_attribute("filename") => {
                header::encoded_param(disposition_header.map(|found| found.raw_value()), "filename")
                    .or_else(|| named(Some(parsed), "filename"))
            }
            _ => {
                let written = headers.all(HeaderName::ContentType).last().map(|found| found.raw_value());
                header::encoded_param(written, "name").or_else(|| named(part.content_type(), "name"))
            }
        };
        Facts { content_type: content_type(part), disposition, filename, content_id: header::content_id(&headers) }
    }

    /// Whether the part has a file name.
    fn named(&self) -> bool {
        self.filename.as_deref().is_some_and(|name| !name.is_empty())
    }

    fn has_cid(&self) -> bool {
        self.content_id.as_deref().is_some_and(|cid| !cid.is_empty())
    }

    /// Whether the part is listed as a file: an attachment, a named part not shown inline, an attached email, or a
    /// named part the text can't show for want of a Content-ID.
    pub(super) fn listed(&self) -> bool {
        self.disposition == "attachment"
            || (self.named() && self.disposition != "inline")
            || self.content_type == "message/rfc822"
            || (self.named() && !self.has_cid())
    }

    /// Whether a forward takes the part along: a file someone attached, or an attached email. A picture the text shows
    /// by its Content-ID stays with the text, and so does a named part marked inline.
    pub(super) fn travels(&self) -> bool {
        self.disposition == "attachment"
            || self.content_type == "message/rfc822"
            || (self.named() && self.disposition != "inline" && !self.has_cid())
    }
}

/// A part's media type, lowercase. One that won't read as type/subtype is text/plain, as RFC 2045 says; a part with
/// none is text/plain, or an email in a multipart/digest.
fn content_type(part: &MessagePart<'_>) -> String {
    match part.content_type() {
        Some(content_type) => match content_type.subtype() {
            Some(subtype) if !subtype.contains('/') => format!("{}/{}", content_type.ctype(), subtype).to_lowercase(),
            _ => written_type(part),
        },
        None if part.headers().contains(HeaderName::ContentType) => written_type(part),
        None if part.parent().is_some_and(|parent| parent.is_content_type("multipart", "digest")) => {
            "message/rfc822".to_owned()
        }
        None => "text/plain".to_owned(),
    }
}

/// A Content-Type mail-parser couldn't read, as Python read it: what comes before its first semicolon, lowercase,
/// when that holds one slash (a cut-off `application/`), else text/plain.
fn written_type(part: &MessagePart<'_>) -> String {
    let written = part.headers().all(HeaderName::ContentType).next().map(|found| header::first_word(found.raw_value()));
    written.filter(|kind| kind.matches('/').count() == 1).unwrap_or_else(|| "text/plain".to_owned())
}

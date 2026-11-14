//! A message's MIME structure: its text, its HTML, the parts a reader is offered, and the headers the reader shows.
//!
//! mail-parser reads the structure, the transfer encodings and the charsets. What is read off it is what the Python
//! app's `mailmind.content.mime` read with Python's `email` package, as the app stored it: the same leaves numbered
//! the same way, so a part id stored before still names the same part. Where the two read a message differently,
//! this gives what a modern mail client shows, and `tests/mime.rs` and the Python app's `test_mime_in_rust.py` say
//! where that is.

mod body;
mod date;
mod fields;
mod flowed;
mod header;
mod indexed;
mod walk;

use std::borrow::Cow;

use mail_parser::{HeaderName, Message, MessageParser, MessagePart, PartFlags};

use walk::{Node, Walk};

pub use fields::{Fields, fields, has_invite, text_body};
pub use indexed::{body_text_of, snippet_of};

/// A part a reader is offered: a file, an attached email, a picture the text shows, or an invite.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Attachment {
    /// Its place among the message's leaves, from 1, in the order a depth-first walk meets them.
    pub part_id: u32,
    /// Its file name, or one made up for it.
    pub filename: String,
    /// Its media type, lowercase.
    pub content_type: String,
    /// How many bytes it holds once its transfer encoding is undone.
    pub size: usize,
    /// Its Content-ID without angle brackets, if it has one.
    pub content_id: Option<String>,
    /// Whether it shows in the text rather than beside it.
    pub is_inline: bool,
    /// Whether a forward takes it along: a file someone attached, or an attached email.
    pub travels: bool,
}

/// A message as a reader reads it. `html` is not made safe yet.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct Parsed<'r> {
    /// The first plain text the message holds, as its writer typed it (format=flowed undone).
    pub text: Option<Cow<'r, str>>,
    /// The first HTML the message holds.
    pub html: Option<Cow<'r, str>>,
    /// The parts it offers, in part order, and an invite Outlook sent unnamed last.
    pub attachments: Vec<Attachment>,
    /// To, as one line of text.
    pub to: String,
    /// Cc, as one line of text.
    pub cc: String,
    /// The address a reply goes to, bare, or empty.
    pub reply_to: String,
    /// Date, written the RFC 5322 way when it reads as one, else as it came.
    pub date: String,
}

/// A picture the text shows by its Content-ID.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Inline<'r> {
    /// The Content-ID, without angle brackets.
    pub content_id: String,
    /// Its media type, lowercase.
    pub content_type: String,
    /// Its bytes, transfer encoding undone.
    pub data: Cow<'r, [u8]>,
}

impl Parsed<'_> {
    /// The same reading, holding its own text.
    pub fn into_owned(self) -> Parsed<'static> {
        Parsed {
            text: self.text.map(|text| Cow::Owned(text.into_owned())),
            html: self.html.map(|html| Cow::Owned(html.into_owned())),
            attachments: self.attachments,
            to: self.to,
            cc: self.cc,
            reply_to: self.reply_to,
            date: self.date,
        }
    }
}

/// Reads a raw message. A message too broken to have a header block is read as plain text.
pub fn parse(raw: &[u8]) -> Parsed<'_> {
    match reading(raw) {
        Reading::Raw(Some(message)) => parse_message(raw, &message),
        Reading::Raw(None) => Parsed { text: Some(body::utf8(raw)), ..Parsed::default() },
        Reading::Mended(copy) => match read(&copy) {
            Some(message) => parse_message(&copy, &message).into_owned(),
            None => Parsed { text: Some(Cow::Owned(body::utf8(&copy).into_owned())), ..Parsed::default() },
        },
    }
}

fn parse_message<'r>(raw: &'r [u8], message: &Message<'r>) -> Parsed<'r> {
    let mut parsed = Parsed::default();
    let mut calendar: Option<(u32, MessagePart<'_>)> = None;
    for node in Walk::new(message) {
        let (index, part) = match node {
            Node::Unparted(part) => {
                if parsed.text.is_none() {
                    parsed.text = Some(owned_by(raw, body::unparted_text(&part)));
                }
                continue;
            }
            Node::Leaf(index, part) => (index, part),
        };
        let facts = walk::Facts::of(&part);
        let content_type = facts.content_type.as_str();
        let cid = facts.content_id.clone().unwrap_or_default();
        if facts.listed() {
            let made_up =
                if content_type == "message/rfc822" { "message.eml".to_owned() } else { format!("part-{index}") };
            parsed.attachments.push(Attachment {
                part_id: index,
                filename: facts.filename.clone().filter(|name| !name.is_empty()).unwrap_or(made_up),
                content_type: facts.content_type.clone(),
                size: body::bytes(&part).len(),
                content_id: (!cid.is_empty()).then(|| cid.clone()),
                is_inline: facts.disposition == "inline" || !cid.is_empty(),
                travels: facts.travels(),
            });
        } else if !cid.is_empty() {
            parsed.attachments.push(Attachment {
                part_id: index,
                filename: facts.filename.clone().filter(|name| !name.is_empty()).unwrap_or(format!("inline-{index}")),
                content_type: facts.content_type.clone(),
                size: body::decoded_len(&part),
                content_id: Some(cid),
                is_inline: true,
                travels: false,
            });
        } else if content_type == "text/plain" && parsed.text.is_none() {
            let text = owned_by(raw, body::text(&part));
            parsed.text = Some(match flowed::of(&part) {
                Some(delsp) => Cow::Owned(flowed::unflowed(&text, delsp)),
                None => text,
            });
        } else if content_type == "text/html" && parsed.html.is_none() {
            parsed.html = Some(owned_by(raw, body::text(&part)));
        } else if content_type == "text/calendar" && calendar.is_none() {
            calendar = Some((index, part));
        }
    }
    // Outlook sends an invite only as an unnamed inline text/calendar part: it is listed as the invite.ics file other
    // clients attach, unless such a file carries the invite already.
    if let Some((index, part)) = calendar
        && !parsed.attachments.iter().any(|a| {
            a.content_type.contains("calendar")
                || a.content_type == "application/ics"
                || a.filename.to_lowercase().ends_with(".ics")
        })
    {
        parsed.attachments.push(Attachment {
            part_id: index,
            filename: "invite.ics".to_owned(),
            content_type: "text/calendar".to_owned(),
            size: body::decoded_len(&part),
            content_id: None,
            is_inline: true,
            travels: false,
        });
    }
    let headers = message.root().headers();
    parsed.to = header::addresses(&headers, HeaderName::To);
    parsed.cc = header::addresses(&headers, HeaderName::Cc);
    parsed.reply_to = header::reply_to(&headers);
    parsed.date = header::date(&headers);
    parsed
}

/// The leaf `part_id` names, as `parse` numbered it: its bytes, transfer encoding undone (an attached email as it
/// was sent), and its media type. None when no leaf has that number.
pub fn extract_part(raw: &[u8], part_id: u32) -> Option<(Cow<'_, [u8]>, String)> {
    match reading(raw) {
        Reading::Raw(Some(message)) => extract_from(raw, &message, part_id),
        Reading::Raw(None) => (part_id == 1).then(|| (Cow::Borrowed(raw), "text/plain".to_owned())),
        Reading::Mended(copy) => match read(&copy) {
            Some(message) => extract_from(&copy, &message, part_id)
                .map(|(data, content_type)| (Cow::Owned(data.into_owned()), content_type)),
            None => (part_id == 1).then(|| (Cow::Owned(copy.clone()), "text/plain".to_owned())),
        },
    }
}

fn extract_from<'r>(raw: &'r [u8], message: &Message<'r>, part_id: u32) -> Option<(Cow<'r, [u8]>, String)> {
    let part = Walk::new(message).find_map(|node| match node {
        Node::Leaf(index, part) if index == part_id => Some(part),
        _ => None,
    })?;
    let content_type = walk::Facts::of(&part).content_type;
    let data = owned_by(raw, body::bytes(&part));
    Some((data, if content_type.is_empty() { "application/octet-stream".to_owned() } else { content_type }))
}

/// Every leaf with a Content-ID: what the text can show by `cid:`. A Content-ID given twice is the later part's.
pub fn inline_parts(raw: &[u8]) -> Vec<Inline<'_>> {
    match reading(raw) {
        Reading::Raw(Some(message)) => inline_from(raw, &message),
        Reading::Raw(None) => Vec::new(),
        Reading::Mended(copy) => read(&copy).map_or_else(Vec::new, |message| {
            inline_from(&copy, &message)
                .into_iter()
                .map(|inline| Inline {
                    content_id: inline.content_id,
                    content_type: inline.content_type,
                    data: Cow::Owned(inline.data.into_owned()),
                })
                .collect()
        }),
    }
}

fn inline_from<'r>(raw: &'r [u8], message: &Message<'r>) -> Vec<Inline<'r>> {
    let mut found: Vec<Inline<'_>> = Vec::new();
    for node in Walk::new(message) {
        let Node::Leaf(_, part) = node else { continue };
        let Some(cid) = header::content_id(&part.headers()).filter(|cid| !cid.is_empty()) else { continue };
        let facts = walk::Facts::of(&part);
        let data = owned_by(raw, body::decoded(&part));
        match found.iter_mut().find(|inline| inline.content_id == cid) {
            Some(earlier) => {
                earlier.content_type = facts.content_type;
                earlier.data = data;
            }
            None => found.push(Inline { content_id: cid, content_type: facts.content_type, data }),
        }
    }
    found
}

/// What mail-parser reads: the message as it came (None when it has no header block), or a copy mended where
/// mail-parser would read its lines otherwise than Python's parser did, so that both find the same parts.
#[expect(clippy::large_enum_variant, reason = "one lives on the stack for a call, and is matched at once")]
enum Reading<'r> {
    Raw(Option<Message<'r>>),
    Mended(Vec<u8>),
}

/// How many times a copy's header blocks are mended at most: ending one can show mail-parser a part it hadn't seen,
/// whose own block may need ending too.
const MENDS: usize = 8;

/// The message as mail-parser reads it, mended where it must be, which is seldom.
///
/// Python's parser split a header block's lines at CR, LF or CRLF, where mail-parser splits them at LF: an LF goes
/// in after a lone CR. A header block ends at its first line that isn't a header, which starts the body, where
/// mail-parser reads past such a line to the blank one: a sender who left out the blank line would lose what came
/// before the next one. A blank line goes in before that line. A part that names its Content-Type or
/// Content-Transfer-Encoding twice is read by the first, as Python read it, where mail-parser reads the last: the
/// later one is renamed, and so is a type `multipart` or `message` with no subtype, which Python read as text/plain.
/// A multipart with an empty boundary gets one. And a message whose lines all end in CR alone,
/// as old Macs wrote them, has them end in CRLF.
fn reading(raw: &[u8]) -> Reading<'_> {
    let mut copy = match cr_lines(raw) {
        Some(lines) => lines,
        None => {
            let Some(message) = read(raw) else { return Reading::Raw(None) };
            match mend(raw, &message) {
                Some(copy) => copy,
                None => return Reading::Raw(Some(message)),
            }
        }
    };
    for _ in 0..MENDS {
        let Some(mended) = read(&copy).and_then(|message| mend(&copy, &message)) else { break };
        copy = mended;
    }
    Reading::Mended(copy)
}

/// One pass of mending: the header blocks' lines are split and ended as Python's were, or else, once they all are,
/// bytes that read to mail-parser otherwise than to Python are replaced. None when there is nothing to mend.
fn mend(raw: &[u8], message: &Message<'_>) -> Option<Vec<u8>> {
    // A lone CR at a delimiter moves where a part starts, and with it the header block mail-parser read for the part:
    // that block's mending waits for the next pass, or a line that ends it would go in twice.
    let mut insertions = lone_cr_delimiters(raw, message);
    if insertions.is_empty() {
        insertions = header_insertions(raw, message);
        insertions.extend(unread_tails(raw, message));
    }
    // An empty boundary goes into each `--` line of its part's body, and ending a header block moves where that body
    // starts: those wait for the next pass, which reads the mended blocks.
    if insertions.is_empty() {
        insertions.extend(empty_boundaries(raw, message));
    }
    insertions.sort_by_key(|&(at, _)| at);
    insertions.dedup();
    if !insertions.is_empty() {
        return Some(with_insertions(raw, &insertions));
    }
    let replaced = replacements(raw, message);
    if replaced.is_empty() {
        return None;
    }
    let mut copy = raw.to_vec();
    for (at, with) in replaced {
        if let Some(byte) = copy.get_mut(at) {
            *byte = with;
        }
    }
    Some(copy)
}

/// A byte of the message replaced in place, and what with.
type Replacement = (usize, u8);

/// The bytes in the message's own parts that read to mail-parser otherwise than they read to Python. A name or a type
/// is renamed, its first byte an X: a part that names its Content-Type or Content-Transfer-Encoding again, of which
/// Python read the first, and a part mail-parser reads as a multipart or an attached email, whose type Python read as
/// none, being without exactly one `/`: such a part is text/plain, as RFC 2045 has it. And a byte Python took as white
/// space at the edge of a Content-Type parameter's name or value, where mail-parser kept it, becomes a space: a
/// boundary or a charset after a vertical tab was lost.
fn replacements(raw: &[u8], message: &Message<'_>) -> Vec<Replacement> {
    let mut replaced = Vec::new();
    for part in message.root().parts() {
        let headers = part.headers();
        for name in [HeaderName::ContentType, HeaderName::ContentTransferEncoding] {
            replaced.extend(headers.all(name).skip(1).map(|again| (again.offset_field() as usize, b'X')));
        }
        if let Some(first) = headers.all(HeaderName::ContentType).next()
            && let Some(start) = offset_in(raw, first.raw_value())
        {
            let blanks = header::stripped_controls(first.raw_value());
            replaced.extend(blanks.into_iter().map(|at| (start + at, b' ')));
        }
        let holds_parts = part.content_type().is_some_and(|written| {
            written.ctype().eq_ignore_ascii_case("multipart") || written.ctype().eq_ignore_ascii_case("message")
        });
        let Some(first) = headers.all(HeaderName::ContentType).next().filter(|_| holds_parts) else { continue };
        let value = first.raw_value();
        let blanks = value.iter().take_while(|byte| byte.is_ascii_whitespace()).count();
        if header::first_word(value).matches('/').count() != 1
            && blanks < value.len()
            && let Some(start) = offset_in(raw, value)
        {
            replaced.push((start + blanks, b'X'));
        }
    }
    replaced
}

/// Bytes that go into a copy of the message, and where.
type Insertion = (usize, &'static [u8]);

/// What goes into each header block of the message's own parts for mail-parser to read its lines as Python did. Its
/// attached emails are sent on as they came, and are left as they are.
fn header_insertions(raw: &[u8], message: &Message<'_>) -> Vec<Insertion> {
    let mut insertions: Vec<Insertion> = Vec::new();
    for part in message.root().parts() {
        let headers = part.raw_headers();
        if let Some(start) = offset_in(raw, headers) {
            if leads_with_a_blank_fold(raw.get(start..).unwrap_or_default()) {
                insertions.push((start, b"X:"));
            }
            header_block(headers, start, &mut insertions);
        }
    }
    insertions
}

/// Whether a header block's first line holds only blanks, then a line break. Python read it as a header line, a fold
/// with no header to fold into, and left it out, where mail-parser ends the block there: a name goes in before it,
/// for a header no one reads, and the lines after it stay the block's.
fn leads_with_a_blank_fold(block: &[u8]) -> bool {
    let blanks = block.iter().take_while(|&&byte| byte == b' ' || byte == b'\t').count();
    blanks > 0 && block.get(blanks).is_some_and(|&byte| byte == b'\r' || byte == b'\n')
}

/// A header block's lines as Python's parser read them. A lone CR ends a line, so an LF goes in after it; a lone CR
/// on a line of its own ends the block. The first line that isn't a header starts the body, so a blank line goes in
/// before it.
fn header_block(headers: &[u8], start: usize, insertions: &mut Vec<Insertion>) {
    let mut at = 0;
    let mut crlf = true;
    while let Some(rest) = headers.get(at..).filter(|rest| !rest.is_empty()) {
        let (len, lone_cr) = match rest.iter().position(|&byte| byte == b'\r' || byte == b'\n') {
            Some(end) if rest.get(end..end + 2) == Some(b"\r\n") => (end + 2, false),
            Some(end) => (end + 1, rest.get(end) == Some(&b'\r')),
            None => (rest.len(), false),
        };
        let line = rest.get(..len).unwrap_or_default();
        if matches!(line, b"\r\n" | b"\n" | b"\r") || !python_header(line) {
            if line == b"\r" {
                insertions.push((start + at + 1, b"\n"));
            } else if !matches!(line, b"\r\n" | b"\n") {
                insertions.push((start + at, if crlf { b"\r\n" } else { b"\n" }));
            }
            return;
        }
        if lone_cr {
            insertions.push((start + at + len, b"\n"));
        }
        crlf = lone_cr || line.ends_with(b"\r\n");
        at += len;
    }
}

/// What follows the last delimiter of a multipart that never closed, where mail-parser reads no part and Python read
/// one: when it doesn't start with a header line, or is cut off before a line break. A blank line after the
/// delimiter makes it a part with no headers, as Python read it, and a line break at the end ends its last line;
/// either goes again with the multipart that never closed. A delimiter cut off after the CR of its line break ended
/// its line there for Python, as a lone CR does: an LF goes in after it.
fn unread_tails(raw: &[u8], message: &Message<'_>) -> Vec<Insertion> {
    let mut insertions: Vec<Insertion> = Vec::new();
    for part in message.root().parts().filter(|part| part.flags().contains(PartFlags::UNTERMINATED)) {
        let (Some(boundary), Some(start)) = (part.boundary(), offset_in(raw, part.raw_body())) else { continue };
        let mut tail = None;
        let mut at = start;
        for line in part.raw_body().split_inclusive(|&byte| byte == b'\n') {
            at += line.len();
            let bare = line.strip_suffix(b"\n").unwrap_or(line);
            let bare = bare.strip_suffix(b"\r").unwrap_or(bare);
            let rest = bare.strip_prefix(b"--").and_then(|rest| rest.strip_prefix(boundary.as_bytes()));
            if rest.is_some_and(|rest| rest.iter().all(|&byte| byte == b' ' || byte == b'\t')) {
                tail = Some((at, line.last().copied()));
            }
        }
        let Some((tail, last)) = tail else { continue };
        if part.children().any(|child| child.offset_header() as usize >= tail) {
            continue;
        }
        let crlf: &'static [u8] = if part.raw_headers().ends_with(b"\r\n") { b"\r\n" } else { b"\n" };
        // A delimiter on the last line opens an empty part: once it ends in a line break, a blank line makes it one.
        // One cut off after the CR of its line break gets the LF first, and the blank line on the next pass.
        if last == Some(b'\r') {
            insertions.push((tail, b"\n"));
            continue;
        }
        if last != Some(b'\n') || tail == raw.len() {
            insertions.push((raw.len(), crlf));
            continue;
        }
        let first = raw.get(tail..).and_then(|rest| rest.split_inclusive(|&byte| byte == b'\n').next());
        if !first.is_some_and(python_header) {
            insertions.push((tail, crlf));
        }
        if !raw.ends_with(b"\n") {
            insertions.push((raw.len(), crlf));
        }
    }
    insertions
}

/// The delimiter lines of the message's own multiparts that a lone CR ends, or comes just before. Python split a
/// body's lines at CR, LF or CRLF, as it did a header block's, where mail-parser splits them at LF: a delimiter a lone
/// CR ended took the line after it, and a part's headers with it, and one after a lone CR wasn't read at all, nor the
/// part it opened. An LF goes in after each such CR. One cut off after its CR is [`unread_tails`]'s.
fn lone_cr_delimiters(raw: &[u8], message: &Message<'_>) -> Vec<Insertion> {
    let mut insertions: Vec<Insertion> = Vec::new();
    for part in message.root().parts() {
        let (Some(boundary), Some(start)) = (part.boundary(), offset_in(raw, part.raw_body())) else { continue };
        let body = part.raw_body();
        let mut at = 0;
        while let Some(rest) = body.get(at..).filter(|rest| !rest.is_empty()) {
            let len = match rest.iter().position(|&byte| byte == b'\r' || byte == b'\n') {
                Some(end) if rest.get(end..end + 2) == Some(b"\r\n") => end + 2,
                Some(end) => end + 1,
                None => rest.len(),
            };
            let line = rest.get(..len).unwrap_or_default();
            if python_delimiter(line, boundary.as_bytes()) {
                if at > 0 && body.get(at - 1) == Some(&b'\r') {
                    insertions.push((start + at, b"\n"));
                }
                let end = start + at + len;
                if line.ends_with(b"\r") && raw.get(end).is_some_and(|&byte| byte != b'\n') {
                    insertions.push((end, b"\n"));
                }
            }
            at += len;
        }
    }
    insertions
}

/// Whether Python's parser read a line, with its line end, as a delimiter of this boundary, by its `boundaryendRE`:
/// `--`, the boundary, `--` if it closes, then only blanks.
fn python_delimiter(line: &[u8], boundary: &[u8]) -> bool {
    let Some(rest) = line.strip_prefix(b"--").and_then(|rest| rest.strip_prefix(boundary)) else { return false };
    let rest = rest.strip_suffix(b"\n").unwrap_or(rest);
    let rest = rest.strip_suffix(b"\r").unwrap_or(rest);
    let rest = rest.strip_prefix(b"--").unwrap_or(rest);
    rest.iter().all(|&byte| byte == b' ' || byte == b'\t')
}

/// A boundary no sender writes, which stands in for an empty one.
const EMPTY_BOUNDARY: &[u8] = b"=_mailmind_empty_boundary_=";
/// The same, as a value for a bare `boundary` that has none.
const EMPTY_BOUNDARY_VALUE: &[u8] = b"=\"=_mailmind_empty_boundary_=\"";
/// The same, with the closing quote a `boundary="` never had.
const EMPTY_BOUNDARY_CLOSED: &[u8] = b"=_mailmind_empty_boundary_=\"";

/// A multipart whose boundary is empty (`boundary=""`, a `boundary="` never closed, or a bare `boundary` with no `=`),
/// against RFC 2046. Python split it at every line that is `--` alone, then blanks, where mail-parser reads no parts
/// in it and its parts would be lost: a boundary goes into the header and into each of those lines.
fn empty_boundaries(raw: &[u8], message: &Message<'_>) -> Vec<Insertion> {
    let mut insertions = Vec::new();
    for part in message.root().parts() {
        if !part.content_type().is_some_and(|written| written.ctype().eq_ignore_ascii_case("multipart")) {
            continue;
        }
        let Some(written) = part.headers().all(HeaderName::ContentType).next() else { continue };
        let (Some(empty), Some(value), Some(start)) = (
            header::empty_param(written.raw_value(), "boundary"),
            offset_in(raw, written.raw_value()),
            offset_in(raw, part.raw_body()),
        ) else {
            continue;
        };
        insertions.push(match empty {
            header::EmptyParam::Quoted(quote) => (value + quote, EMPTY_BOUNDARY),
            header::EmptyParam::Unclosed(quote) => (value + quote, EMPTY_BOUNDARY_CLOSED),
            header::EmptyParam::Bare(end) => (value + end, EMPTY_BOUNDARY_VALUE),
        });
        let mut at = start;
        for line in part.raw_body().split_inclusive(|&byte| byte == b'\n') {
            let bare = line.strip_suffix(b"\n").unwrap_or(line);
            let bare = bare.strip_suffix(b"\r").unwrap_or(bare);
            if let Some(rest) = bare.strip_prefix(b"--") {
                let rest = rest.strip_prefix(b"--").unwrap_or(rest);
                if rest.iter().all(|&byte| byte == b' ' || byte == b'\t') {
                    insertions.push((at + 2, EMPTY_BOUNDARY));
                }
            }
            at += line.len();
        }
    }
    insertions
}

/// Whether Python's parser read a line as a header, by its `headerRE`: a name in printable characters then a colon,
/// a folded line, or an mbox `From ` line.
fn python_header(line: &[u8]) -> bool {
    line.starts_with(b"From ")
        || line.first().is_some_and(|&byte| byte == b' ' || byte == b'\t')
        || line.iter().position(|&byte| byte == b':').is_some_and(|colon| {
            line.get(..colon).is_some_and(|name| name.iter().all(|byte| (b'!'..=b'~').contains(byte)))
        })
}

/// A copy of `raw` with each insertion made.
fn with_insertions(raw: &[u8], insertions: &[Insertion]) -> Vec<u8> {
    let mut copy = Vec::with_capacity(raw.len() + 3 * insertions.len());
    let mut at = 0;
    for &(to, bytes) in insertions {
        copy.extend_from_slice(raw.get(at..to).unwrap_or_default());
        copy.extend_from_slice(bytes);
        at = to.max(at);
    }
    copy.extend_from_slice(raw.get(at..).unwrap_or_default());
    copy
}

/// A message whose lines end in CR alone, with CRLF line ends. None for a message with an LF in it.
fn cr_lines(raw: &[u8]) -> Option<Vec<u8>> {
    if raw.contains(&b'\n') || !raw.contains(&b'\r') {
        return None;
    }
    let mut lines = Vec::with_capacity(raw.len() + raw.len() / 16);
    for &byte in raw {
        lines.push(byte);
        if byte == b'\r' {
            lines.push(b'\n');
        }
    }
    Some(lines)
}

/// The parser every reading uses: mail-parser's own limits on depth and parts.
fn read(raw: &[u8]) -> Option<Message<'_>> {
    MessageParser::new().parse(raw)
}

/// Bytes or text read off `message` as borrowed from `raw` where they lie in it, so a body that needs no decoding
/// crosses to Python without a copy on the way. What was decoded is owned already.
fn owned_by<'r, T>(raw: &'r [u8], read: Cow<'_, T>) -> Cow<'r, T>
where
    T: ?Sized + ToOwned + Within,
{
    match read {
        Cow::Borrowed(slice) => T::within(raw, slice).map_or_else(|| Cow::Owned(slice.to_owned()), Cow::Borrowed),
        Cow::Owned(owned) => Cow::Owned(owned),
    }
}

/// What a slice of the raw message can be read back as.
trait Within: ToOwned {
    /// `slice` as a borrow of `raw`, when it lies in it.
    fn within<'r>(raw: &'r [u8], slice: &Self) -> Option<&'r Self>;
}

/// Where `slice` starts in `raw`, when it lies wholly in it.
fn offset_in(raw: &[u8], slice: &[u8]) -> Option<usize> {
    let start = slice.as_ptr().addr().checked_sub(raw.as_ptr().addr())?;
    (start.checked_add(slice.len())? <= raw.len()).then_some(start)
}

impl Within for [u8] {
    fn within<'r>(raw: &'r [u8], slice: &[u8]) -> Option<&'r [u8]> {
        let start = offset_in(raw, slice)?;
        raw.get(start..start + slice.len())
    }
}

impl Within for str {
    fn within<'r>(raw: &'r [u8], slice: &str) -> Option<&'r str> {
        let start = offset_in(raw, slice.as_bytes())?;
        std::str::from_utf8(raw.get(start..start + slice.len())?).ok()
    }
}

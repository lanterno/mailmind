//! A draft's parts as the Python app's `email` package cut them (`feedparser`, under `policy.default`), and what
//! `resume_files` asked of each: its type, file name, disposition and decoded body (`email.message`).
//!
//! The cut is the feed parser's, step for step, so the parts are the ones Python numbered and checked: a line ends at
//! CRLF, CR or LF; a part's headers run to the first line that can't be one; a multipart is cut at its delimiters,
//! and a part in it ends at its own multipart's delimiter or at any outer one; an attached email is a message of its
//! own. A header's text is read as `headers` reads it, as UTF-8 where Python kept each 8-bit byte as a lone
//! surrogate: the two part only for a boundary or a name with such bytes in it.

use std::collections::VecDeque;
use std::sync::LazyLock;

use mm_kernel::text::{is_space, strip};
use regex::Regex;

use super::headers;

/// How deep parts nest before Python's stack ran out reading them, which `_opened` answered as unreadable: a script
/// measured 972 multiparts, one in another, and 978 attached emails. The app's own calls ahead of it left less.
const DEEPEST_PART: usize = 960;

/// A header as `header_source_parse` stored it: its name as written, and its value with blanks and line breaks off
/// its start and line breaks off its end.
pub(super) type Header = (Vec<u8>, Vec<u8>);

/// What a part holds.
pub(super) enum Payload {
    /// Its body as written (Python's payload string).
    Text(Vec<u8>),
    /// The parts it holds, by their place in the tree: a multipart's, or an attached email.
    Parts(Vec<usize>),
}

pub(super) struct Part {
    pub headers: Vec<Header>,
    /// `get_content_type()`.
    pub content_type: String,
    pub payload: Payload,
}

/// Every part, the message itself first.
pub(super) struct Tree {
    pub parts: Vec<Part>,
}

/// What ends a part early (the feed parser's EOF matchers).
enum End {
    /// A blank line, between the blocks of a delivery report.
    Blank,
    /// A delimiter of an enclosing multipart, by its separator (`--` and the boundary).
    Delimiter(Vec<u8>),
}

impl End {
    fn matches(&self, line: &[u8]) -> bool {
        match self {
            End::Blank => line.first().is_some_and(|byte| matches!(byte, b'\r' | b'\n')),
            End::Delimiter(separator) => delimiter(line, separator).is_some(),
        }
    }
}

/// `FeedParser._parsegen` and its input, with all of the message pushed in at once.
struct Feed<'r> {
    lines: VecDeque<&'r [u8]>,
    ends: Vec<End>,
    parts: Vec<Part>,
    stack: Vec<usize>,
    last: usize,
    /// Python raised: it ran out of stack, or a check it asserted failed.
    raised: bool,
}

impl<'r> Feed<'r> {
    /// `BufferedSubFile.readline`: the next line, or nothing where the input ends or a part must end.
    fn readline(&mut self) -> &'r [u8] {
        let Some(line) = self.lines.pop_front() else { return b"" };
        if self.ends.iter().any(|end| end.matches(line)) {
            self.lines.push_front(line);
            return b"";
        }
        line
    }

    fn unreadline(&mut self, line: &'r [u8]) {
        self.lines.push_front(line);
    }

    /// The lines up to where the part ends, joined.
    fn rest(&mut self) -> Vec<u8> {
        let mut body = Vec::new();
        loop {
            let line = self.readline();
            if line.is_empty() {
                return body;
            }
            body.extend_from_slice(line);
        }
    }

    /// `_new_message`: a part, attached to the one being read. A digest's parts are attached emails unless they say.
    fn new_message(&mut self) -> usize {
        let parent = self.stack.last().copied();
        let digest = parent.is_some_and(|parent| self.parts[parent].content_type == "multipart/digest");
        let at = self.parts.len();
        let default_type = if digest { "message/rfc822" } else { "text/plain" };
        self.parts.push(Part {
            headers: Vec::new(),
            content_type: default_type.to_owned(),
            payload: Payload::Text(Vec::new()),
        });
        if let Some(parent) = parent {
            match &mut self.parts[parent].payload {
                Payload::Parts(parts) => parts.push(at),
                payload => *payload = Payload::Parts(vec![at]),
            }
        }
        self.stack.push(at);
        self.last = at;
        at
    }

    fn pop_message(&mut self) {
        self.stack.pop();
    }

    /// `_parsegen`: one part, its headers and then its body, from where the input stands.
    fn parse(&mut self, depth: usize) {
        let at = self.new_message();
        if depth > DEEPEST_PART || self.raised {
            self.raised = true;
            self.lines.clear();
            return;
        }
        let mut header_lines = Vec::new();
        loop {
            let line = self.readline();
            if line.is_empty() {
                break;
            }
            if !header_line(line) {
                if !matches!(line[0], b'\r' | b'\n') {
                    self.unreadline(line);
                }
                break;
            }
            header_lines.push(line);
        }
        self.parse_headers(at, &header_lines);
        let content_type = self.parts[at].content_type.clone();
        if content_type == "message/delivery-status" {
            loop {
                self.ends.push(End::Blank);
                self.parse(depth + 1);
                self.pop_message();
                self.ends.pop();
                self.readline();
                let line = self.readline();
                if line.is_empty() {
                    return;
                }
                self.unreadline(line);
            }
        }
        match main_type(&content_type) {
            "message" => {
                self.parse(depth + 1);
                self.pop_message();
            }
            "multipart" => self.parse_multipart(at, depth),
            _ => self.parts[at].payload = Payload::Text(self.rest()),
        }
    }

    /// `_parsegen`'s multipart: no boundary, and all of it is text; else a preamble, the parts between delimiters,
    /// and an epilogue after the close.
    fn parse_multipart(&mut self, at: usize, depth: usize) {
        let Some(boundary) = self.boundary(at) else {
            self.parts[at].payload = Payload::Text(self.rest());
            return;
        };
        let separator = [b"--", boundary.as_bytes()].concat();
        let (mut preamble, mut in_preamble, mut closed) = (Vec::new(), true, false);
        loop {
            let line = self.readline();
            if line.is_empty() {
                break;
            }
            match delimiter(line, &separator) {
                Some(true) => {
                    closed = true;
                    break;
                }
                Some(false) if in_preamble => {
                    in_preamble = false;
                    self.unreadline(line);
                }
                Some(false) => {
                    loop {
                        let line = self.readline();
                        if delimiter(line, &separator).is_none() {
                            self.unreadline(line);
                            break;
                        }
                    }
                    self.ends.push(End::Delimiter(separator.clone()));
                    self.parse(depth + 1);
                    self.trim_last();
                    self.ends.pop();
                    self.pop_message();
                    self.last = at;
                }
                None if in_preamble => preamble.extend_from_slice(line),
                None => {
                    self.raised = true;
                    return;
                }
            }
        }
        // A start delimiter never found leaves all of it text; past the close is the epilogue. Either way the rest
        // is read, as it isn't where the multipart ended early.
        if in_preamble {
            self.parts[at].payload = Payload::Text(preamble);
            self.rest();
        } else if closed {
            self.rest();
        }
    }

    /// The line end before a delimiter is the delimiter's, not the part's before it.
    fn trim_last(&mut self) {
        let last = &mut self.parts[self.last];
        if main_type(&last.content_type) == "multipart" {
            return; // its epilogue, which nothing here reads
        }
        if let Payload::Text(body) = &mut last.payload {
            if body.ends_with(b"\r\n") {
                body.truncate(body.len() - 2);
            } else if body.ends_with(b"\r") || body.ends_with(b"\n") {
                body.truncate(body.len() - 1);
            }
        }
    }

    /// `_parse_headers`: each header with its continuation lines, as `header_source_parse` stores it. A first line
    /// that is a continuation, or a name that is empty, is dropped; so is a `From ` line, which is the body's first
    /// line where it ends the headers.
    fn parse_headers(&mut self, at: usize, lines: &[&'r [u8]]) {
        let mut pending: Vec<&[u8]> = Vec::new();
        for (number, &line) in lines.iter().enumerate() {
            if matches!(line[0], b' ' | b'\t') {
                if !pending.is_empty() {
                    pending.push(line);
                }
                continue;
            }
            if !pending.is_empty() {
                self.parts[at].headers.push(source_parse(&pending));
                pending.clear();
            }
            if line.starts_with(b"From ") {
                if number > 0 && number == lines.len() - 1 {
                    self.unreadline(line);
                    break;
                }
                continue;
            }
            if line.iter().position(|&byte| byte == b':').is_some_and(|colon| colon > 0) {
                pending.push(line);
            }
        }
        if !pending.is_empty() {
            self.parts[at].headers.push(source_parse(&pending));
        }
        let default_type = std::mem::take(&mut self.parts[at].content_type);
        self.parts[at].content_type = python_type(&self.parts[at].headers, default_type);
    }

    /// `get_boundary()`.
    fn boundary(&self, at: usize) -> Option<String> {
        let boundary = param(&self.parts[at].headers, "content-type", "boundary")?;
        Some(boundary.trim_end_matches(is_space).to_owned())
    }
}

/// `headerRE`: a header's first line (a name and a colon), a continuation line, or a `From ` line.
fn header_line(line: &[u8]) -> bool {
    if line.starts_with(b"From ") || matches!(line[0], b' ' | b'\t') {
        return true;
    }
    let name = line.iter().take_while(|&&byte| (0x21..=0x7e).contains(&byte) && byte != b':').count();
    line.get(name) == Some(&b':')
}

/// `policy.default.header_source_parse` of a header's lines.
fn source_parse(lines: &[&[u8]]) -> Header {
    let first = lines[0];
    let colon = first.iter().position(|&byte| byte == b':').unwrap_or(first.len());
    let mut value = first.get(colon + 1..).unwrap_or_default().to_vec();
    for line in &lines[1..] {
        value.extend_from_slice(line);
    }
    let start = value.iter().position(|byte| !matches!(byte, b' ' | b'\t' | b'\r' | b'\n')).unwrap_or(value.len());
    let end = value.iter().rposition(|byte| !matches!(byte, b'\r' | b'\n')).map_or(0, |at| at + 1).max(start);
    (first[..colon].to_vec(), value[start..end].to_vec())
}

/// The lines of a message, each with its end (`StringIO(newline='')`).
fn lines_of(raw: &[u8]) -> VecDeque<&[u8]> {
    let mut lines = VecDeque::new();
    let (mut start, mut at) = (0, 0);
    while at < raw.len() {
        let end = match raw[at] {
            b'\n' => at + 1,
            b'\r' if raw.get(at + 1) == Some(&b'\n') => at + 2,
            b'\r' => at + 1,
            _ => {
                at += 1;
                continue;
            }
        };
        lines.push_back(&raw[start..end]);
        (start, at) = (end, end);
    }
    if start < raw.len() {
        lines.push_back(&raw[start..]);
    }
    lines
}

/// Whether a line is a delimiter of a multipart (`--boundary`, then `--` for the close, then blanks to the end of the
/// line, `boundaryendRE`), and whether it closes the multipart.
fn delimiter(line: &[u8], separator: &[u8]) -> Option<bool> {
    let bare = line.strip_suffix(b"\n").unwrap_or(line);
    let bare = bare.strip_suffix(b"\r").unwrap_or(bare);
    let rest = bare.strip_prefix(separator)?;
    let (closes, rest) = match rest.strip_prefix(b"--") {
        Some(rest) => (true, rest),
        None => (false, rest),
    };
    rest.iter().all(|&byte| byte == b' ' || byte == b'\t').then_some(closes)
}

/// `get_content_maintype()` of a type.
fn main_type(content_type: &str) -> &str {
    content_type.split('/').next().unwrap_or_default()
}

/// What a header's text says before its first `;`, stripped and lowercase (`_splitparam(value)[0].lower()`).
fn kind_of(value: &str) -> String {
    strip(value.split(';').next().unwrap_or_default()).to_lowercase()
}

/// A header's first value as the header registry writes it out (`str(msg.get(name))`), read as an unstructured one.
fn first_value(headers: &[Header], name: &str) -> Option<String> {
    headers::raw_values(headers, name).first().map(|value| headers::unstructured(&headers::fetched(value)))
}

/// `get_content_type()`: text/plain where the type isn't type/subtype, and the part's default where it has none.
fn python_type(headers: &[Header], default_type: String) -> String {
    match first_value(headers, "content-type") {
        Some(value) => {
            let kind = kind_of(&value);
            if kind.matches('/').count() == 1 { kind } else { "text/plain".to_owned() }
        }
        None => default_type,
    }
}

/// A parameter's value as `get_param` gives it.
enum Param {
    Plain(String),
    /// RFC 2231's charset and text (its language is read past: nothing asks for it).
    Encoded(Option<String>, String),
}

/// `get_param(name, header=...)` and `collapse_rfc2231_value`: a parameter of a header's first value, its quotes
/// gone and its RFC 2231 parts joined and decoded.
fn param(headers: &[Header], header: &str, name: &str) -> Option<String> {
    let value = first_value(headers, header)?;
    let name = name.to_lowercase();
    let (_, found) = decode_params(split_params(&value)).into_iter().find(|(key, _)| key.to_lowercase() == name)?;
    Some(match found {
        Param::Plain(text) => unquote(&unquote(&text)),
        Param::Encoded(charset, text) => {
            let text = unquote(&text);
            let charset = charset.unwrap_or_else(|| "us-ascii".to_owned());
            headers::in_known_charset(&charset, &raw_unicode_escape(&text)).unwrap_or_else(|| unquote(&text))
        }
    })
}

/// `_parseparam` and `_get_params_preserve`: a header's text split at each `;` outside quotes, as (name, value).
fn split_params(value: &str) -> Vec<(String, String)> {
    let s = format!(";{value}");
    let find = |sub: u8, from: isize| -> isize {
        let from = from.max(0) as usize;
        s.as_bytes()
            .get(from..)
            .and_then(|rest| rest.iter().position(|&byte| byte == sub))
            .map_or(-1, |at| (from + at) as isize)
    };
    let count = |sub: &str, start: isize, end: isize| -> isize {
        let len = s.len() as isize;
        let clamp = |at: isize| if at < 0 { (at + len).max(0) } else { at.min(len) };
        let (start, end) = (clamp(start) as usize, clamp(end) as usize);
        if start >= end {
            0
        } else {
            s.as_bytes()[start..end].windows(sub.len()).filter(|w| *w == sub.as_bytes()).count() as isize
        }
    };
    let mut out = Vec::new();
    let mut start: isize = 0;
    while find(b';', start) == start {
        start += 1;
        let mut end = find(b';', start);
        let (mut ind, mut diff) = (start, 0);
        while end > 0 {
            diff += count("\"", ind, end) - count("\\\"", ind, end);
            if diff % 2 == 0 {
                break;
            }
            (end, ind) = (ind, find(b';', end + 1));
        }
        if end < 0 {
            end = s.len() as isize;
        }
        let end = end.max(start);
        let piece = s.get(start as usize..end as usize).unwrap_or_default();
        let piece = match piece.find('=') {
            None => piece.to_owned(),
            Some(i) => {
                let (name, value) = (&piece[..i], &piece[i + 1..]);
                format!("{}={}", name.trim_end_matches(is_space).to_lowercase(), value.trim_start_matches(is_space))
            }
        };
        let piece = strip(&piece);
        out.push(match piece.split_once('=') {
            Some((name, value)) => (strip(name).to_owned(), strip(value).to_owned()),
            None => (piece.to_owned(), String::new()),
        });
        start = end;
    }
    out
}

/// An RFC 2231 parameter's sections: each one's number, if it has one, its text, and whether it is encoded.
type Sections = Vec<(Option<u64>, String, bool)>;

/// `email.utils.decode_params`: the first (the type itself) as it is, the others unquoted and quoted again, and RFC
/// 2231's numbered and encoded parts joined, after the others, in the order their names came.
fn decode_params(params: Vec<(String, String)>) -> Vec<(String, Param)> {
    static CONTINUATION: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r"\A([A-Za-z0-9_]+)\*(?:([0-9]+)\*?)?\z").expect("a pattern written here compiles")
    });
    let mut params = params.into_iter();
    let mut out: Vec<(String, Param)> =
        params.next().map(|(name, value)| (name, Param::Plain(value))).into_iter().collect();
    let mut groups: Vec<(String, Sections)> = Vec::new();
    for (name, value) in params {
        let encoded = name.ends_with('*');
        let value = unquote(&value);
        let Some(found) = CONTINUATION.captures(&name) else {
            out.push((name, Param::Plain(format!("\"{}\"", quote(&value)))));
            continue;
        };
        let base = found.get(1).map_or("", |m| m.as_str()).to_owned();
        let number = found.get(2).map(|m| m.as_str().parse().unwrap_or(u64::MAX));
        match groups.iter_mut().find(|(name, _)| *name == base) {
            Some((_, parts)) => parts.push((number, value, encoded)),
            None => groups.push((base, vec![(number, value, encoded)])),
        }
    }
    for (name, mut parts) in groups {
        if parts.iter().any(|(number, _, _)| *number == Some(0)) {
            parts.retain(|(number, _, _)| number.is_some());
        } else {
            for (number, _, _) in &mut parts {
                *number = Some(number.unwrap_or(0));
            }
        }
        parts.sort_by_key(|(number, _, _)| *number);
        let extended = parts.iter().any(|(_, _, encoded)| *encoded);
        let joined: String =
            parts.into_iter().map(|(_, text, encoded)| if encoded { percent_decoded(&text) } else { text }).collect();
        let value = quote(&joined);
        if extended {
            let mut pieces = value.splitn(3, '\'');
            match (pieces.next(), pieces.next(), pieces.next()) {
                (Some(charset), Some(_language), Some(text)) => {
                    out.push((name, Param::Encoded(Some(charset.to_owned()), format!("\"{text}\""))))
                }
                _ => out.push((name, Param::Encoded(None, format!("\"{value}\"")))),
            }
        } else {
            out.push((name, Param::Plain(format!("\"{value}\""))));
        }
    }
    out
}

/// `email.utils.unquote`.
fn unquote(text: &str) -> String {
    if text.chars().count() > 1 {
        if let Some(inner) = text.strip_prefix('"').and_then(|text| text.strip_suffix('"')) {
            return inner.replace("\\\\", "\\").replace("\\\"", "\"");
        }
        if let Some(inner) = text.strip_prefix('<').and_then(|text| text.strip_suffix('>')) {
            return inner.to_owned();
        }
    }
    text.to_owned()
}

/// `email.utils.quote`.
fn quote(text: &str) -> String {
    text.replace('\\', "\\\\").replace('"', "\\\"")
}

/// `urllib.parse.unquote(text, encoding="latin-1")`: each `%` and two hex digits the character of that byte.
fn percent_decoded(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut chars = text.char_indices();
    while let Some((at, c)) = chars.next() {
        let hex = text.get(at + 1..at + 3).filter(|hex| hex.bytes().all(|byte| byte.is_ascii_hexdigit()));
        match hex.and_then(|hex| u8::from_str_radix(hex, 16).ok()) {
            Some(byte) if c == '%' => {
                out.push(char::from(byte));
                chars.nth(1);
            }
            _ => out.push(c),
        }
    }
    out
}

/// `text.encode('raw-unicode-escape')`: a character under 256 as its byte, any other as a `\u` escape.
fn raw_unicode_escape(text: &str) -> Vec<u8> {
    let mut out = Vec::with_capacity(text.len());
    for c in text.chars() {
        match u32::from(c) {
            code @ 0..=0xff => out.push(code as u8),
            code @ 0x100..=0xffff => out.extend_from_slice(format!("\\u{code:04x}").as_bytes()),
            code => out.extend_from_slice(format!("\\U{code:08x}").as_bytes()),
        }
    }
    out
}

impl Tree {
    /// `email.message_from_bytes(raw, policy=email.policy.default)`, or None where Python raised.
    pub(super) fn parse(raw: &[u8]) -> Option<Tree> {
        let mut feed = Feed {
            lines: lines_of(raw),
            ends: Vec::new(),
            parts: Vec::new(),
            stack: Vec::new(),
            last: 0,
            raised: false,
        };
        feed.parse(0);
        (!feed.raised).then_some(Tree { parts: feed.parts })
    }

    pub(super) fn content_type(&self, at: usize) -> &str {
        &self.parts[at].content_type
    }

    pub(super) fn main_type(&self, at: usize) -> &str {
        main_type(&self.parts[at].content_type)
    }

    /// The parts a multipart (or an attached email) holds: none for any other part.
    pub(super) fn children(&self, at: usize) -> &[usize] {
        match &self.parts[at].payload {
            Payload::Parts(parts) => parts,
            Payload::Text(_) => &[],
        }
    }

    pub(super) fn is_multipart(&self, at: usize) -> bool {
        matches!(self.parts[at].payload, Payload::Parts(_))
    }

    /// `mime.walk`: a part and every part in it, depth first, an attached email one part.
    pub(super) fn walk(&self, at: usize) -> Vec<usize> {
        let mut out = Vec::new();
        let mut next = vec![at];
        while let Some(part) = next.pop() {
            out.push(part);
            if self.main_type(part) != "message" {
                next.extend(self.children(part).iter().rev());
            }
        }
        out
    }

    /// `get_filename()`: the Content-Disposition's `filename`, else the Content-Type's `name`, stripped.
    pub(super) fn filename(&self, at: usize) -> Option<String> {
        let headers = &self.parts[at].headers;
        let name =
            param(headers, "content-disposition", "filename").or_else(|| param(headers, "content-type", "name"))?;
        Some(strip(&name).to_owned())
    }

    /// `get_content_disposition()`.
    pub(super) fn disposition(&self, at: usize) -> Option<String> {
        first_value(&self.parts[at].headers, "content-disposition").map(|value| kind_of(&value))
    }

    /// Whether a part's text holds more than blanks (`str(p.get_payload()).strip()`).
    pub(super) fn holds_text(&self, at: usize) -> bool {
        match &self.parts[at].payload {
            Payload::Text(body) => body.iter().any(|&byte| !(byte.is_ascii() && is_space(char::from(byte)))),
            Payload::Parts(_) => true,
        }
    }

    /// `mime.decoded`: the body with its transfer encoding undone, or None for a part that holds parts.
    pub(super) fn decoded(&self, at: usize) -> Option<Vec<u8>> {
        let Payload::Text(body) = &self.parts[at].payload else { return None };
        let encoding = headers::raw_values(&self.parts[at].headers, "content-transfer-encoding")
            .first()
            .map(|value| headers::transfer_encoding(&headers::fetched(value)))
            .unwrap_or_default();
        Some(match encoding.as_str() {
            "quoted-printable" => a2b_qp(body),
            "base64" => headers::decode_b(
                &body.iter().copied().filter(|byte| !matches!(byte, b'\r' | b'\n')).collect::<Vec<_>>(),
            ),
            "x-uuencode" | "uuencode" | "uue" | "x-uue" => decode_uu(body).unwrap_or_else(|| body.clone()),
            _ => body.clone(),
        })
    }
}

/// `binascii.a2b_qp`: `=` and two hex digits a byte, `=` at a line's end joining it to the next.
fn a2b_qp(data: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(data.len());
    let mut at = 0;
    while at < data.len() {
        if data[at] != b'=' {
            out.push(data[at]);
            at += 1;
            continue;
        }
        at += 1;
        match data.get(at) {
            None => break,
            Some(b'\n' | b'\r') => {
                while at < data.len() && data[at] != b'\n' {
                    at += 1;
                }
                at = (at + 1).min(data.len());
            }
            Some(b'=') => {
                out.push(b'=');
                at += 1;
            }
            Some(_) => match data.get(at..at + 2).filter(|hex| hex.iter().all(u8::is_ascii_hexdigit)) {
                Some(hex) => {
                    let digit = |byte: u8| char::from(byte).to_digit(16).unwrap_or(0) as u8;
                    out.push(digit(hex[0]) << 4 | digit(hex[1]));
                    at += 2;
                }
                None => out.push(b'='),
            },
        }
    }
    out
}

/// `email.message._decode_uu`: the lines after a `begin` line with an octal mode, up to `end`; None where Python
/// raised, and the body stands as written.
fn decode_uu(encoded: &[u8]) -> Option<Vec<u8>> {
    let mut lines = byte_lines(encoded).into_iter();
    loop {
        let line = lines.next()?;
        if let Some(rest) = line.strip_prefix(b"begin ") {
            let mode = rest.split(|&byte| byte == b' ').next().unwrap_or_default();
            if octal(mode) {
                break;
            }
        }
    }
    let mut out = Vec::new();
    for line in lines {
        if line.is_empty() {
            return None;
        }
        let trimmed = line.iter().position(|byte| !b" \t\r\n\x0c".contains(byte)).map_or(&line[..0], |start| {
            let end = line.iter().rposition(|byte| !b" \t\r\n\x0c".contains(byte)).map_or(start, |end| end + 1);
            &line[start..end]
        });
        if trimmed == b"end" {
            break;
        }
        match a2b_uu(line) {
            Some(decoded) => out.extend(decoded),
            None => {
                let length = (((i32::from(line[0]) - 32) & 63) * 4 + 5) / 3;
                out.extend(a2b_uu(&line[..(length as usize).min(line.len())])?);
            }
        }
    }
    Some(out)
}

/// `bytes.splitlines()`: lines split at CRLF, CR or LF, without their ends.
fn byte_lines(data: &[u8]) -> Vec<&[u8]> {
    lines_of(data)
        .into_iter()
        .map(|line| {
            let line = line.strip_suffix(b"\n").unwrap_or(line);
            line.strip_suffix(b"\r").unwrap_or(line)
        })
        .collect()
}

/// Whether `int(text, base=8)` reads the text: blanks around, a sign, a `0o`, octal digits with single underscores
/// between them.
fn octal(text: &[u8]) -> bool {
    let Ok(text) = std::str::from_utf8(text) else { return false };
    let text = strip(text);
    let text = text.strip_prefix(['+', '-']).unwrap_or(text);
    let digits = text
        .strip_prefix("0o")
        .or_else(|| text.strip_prefix("0O"))
        .map_or(text, |rest| rest.strip_prefix('_').unwrap_or(rest));
    !digits.is_empty()
        && !digits.starts_with('_')
        && !digits.ends_with('_')
        && !digits.contains("__")
        && digits.chars().all(|c| matches!(c, '0'..='7' | '_'))
}

/// `binascii.a2b_uu` of one line: its first character says how many bytes it holds. None where Python raised.
fn a2b_uu(line: &[u8]) -> Option<Vec<u8>> {
    let (&first, mut rest) = line.split_first()?;
    let mut wanted = usize::from(first.wrapping_sub(b' ') & 0o77);
    let mut out = Vec::with_capacity(wanted);
    let (mut bits, mut held) = (0u32, 0u32);
    while wanted > 0 {
        let c = rest.first().copied();
        let sextet = match c {
            None | Some(b'\n' | b'\r') => 0,
            Some(c) if !(b' '..=b' ' + 64).contains(&c) => return None,
            Some(c) => u32::from((c - b' ') & 0o77),
        };
        bits = (bits << 6) | sextet;
        held += 6;
        if held >= 8 {
            held -= 8;
            out.push((bits >> held) as u8);
            bits &= (1 << held) - 1;
            wanted -= 1;
        }
        rest = rest.get(1..).unwrap_or_default();
    }
    rest.iter().all(|c| matches!(c, b' ' | b'`' | b'\n' | b'\r')).then_some(out)
}

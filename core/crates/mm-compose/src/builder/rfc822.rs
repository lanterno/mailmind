//! An attached email written again as Python writes one it parsed: `email.message_from_bytes(data,
//! policy=email.policy.default)` read as `email.feedparser` reads it, then written by the `BytesGenerator` of the
//! message it is attached to.
//!
//! Its headers go out as they came, their line ends made CRLF, unless a line is longer than 78 columns, when the
//! header is folded again as Python's registry parses its name: an address list, Message-IDs, a MIME type with
//! parameters, or else unstructured text. A Date folded again keeps its words, where Python writes the date anew; a
//! header with bytes that aren't ASCII and a long line goes out as it came, where Python encodes them as
//! `unknown-8bit`; and a `message/delivery-status` part is written as text.

use std::collections::VecDeque;

use super::fold::{self, Kind, Tok};
use super::{mime, parse};

/// A message or a part of one, as parsed.
struct Msg<'a> {
    headers: Vec<(&'a [u8], Vec<u8>)>,
    body: Body<'a>,
}

enum Body<'a> {
    /// A part's text, whose line ends the generator makes CRLF.
    Text(Vec<u8>),
    /// A multipart with no boundary, or with no first one: its text, written as it is.
    Raw(Vec<u8>),
    Parts {
        boundary: Vec<u8>,
        preamble: Option<Vec<u8>>,
        parts: Vec<Msg<'a>>,
        epilogue: Option<Vec<u8>>,
    },
    /// A `message/*` part's message.
    Message(Box<Msg<'a>>),
}

/// `BufferedSubFile`: the lines left, each with its line end, and the boundaries that end what a part may read.
struct Input<'a> {
    lines: VecDeque<&'a [u8]>,
    ends: Vec<Vec<u8>>,
}

impl<'a> Input<'a> {
    fn new(data: &'a [u8]) -> Input<'a> {
        let mut lines = VecDeque::new();
        let mut start = 0;
        let mut at = 0;
        while at < data.len() {
            match data[at] {
                b'\n' => {
                    lines.push_back(&data[start..=at]);
                    at += 1;
                    start = at;
                }
                b'\r' => {
                    let end = if data.get(at + 1) == Some(&b'\n') { at + 2 } else { at + 1 };
                    lines.push_back(&data[start..end]);
                    at = end;
                    start = at;
                }
                _ => at += 1,
            }
        }
        if start < data.len() {
            lines.push_back(&data[start..]);
        }
        Input { lines, ends: Vec::new() }
    }

    /// `readline`: the next line, or None at the end of the text or at a line an enclosing boundary matches.
    fn readline(&mut self) -> Option<&'a [u8]> {
        let line = self.lines.pop_front()?;
        if line.is_empty() {
            return None;
        }
        if self.ends.iter().rev().any(|separator| boundary_match(line, separator).is_some()) {
            self.lines.push_front(line);
            return None;
        }
        Some(line)
    }

    fn unreadline(&mut self, line: &'a [u8]) {
        self.lines.push_front(line);
    }
}

/// `boundarymatch`: whether the line is `--boundary`, and (whether it closes, whether a line end follows).
fn boundary_match(line: &[u8], separator: &[u8]) -> Option<(bool, bool)> {
    let rest = line.strip_prefix(separator)?;
    let (end, rest) = match rest.strip_prefix(b"--") {
        Some(rest) => (true, rest),
        None => (false, rest),
    };
    let rest = &rest[rest.iter().take_while(|b| **b == b' ' || **b == b'\t').count()..];
    match rest {
        b"" => Some((end, false)),
        b"\r\n" | b"\r" | b"\n" => Some((end, true)),
        _ => None,
    }
}

/// `NLCRE_eol`: the text without one line end at its end.
fn without_eol(text: &mut Vec<u8>) {
    if text.ends_with(b"\r\n") {
        text.truncate(text.len() - 2);
    } else if text.ends_with(b"\r") || text.ends_with(b"\n") {
        text.pop();
    }
}

/// `headerRE`: a line that starts a header, continues one, or is a Unix From line.
fn is_header_line(line: &[u8]) -> bool {
    if line.starts_with(b"From ") || line.first().is_some_and(|b| *b == b' ' || *b == b'\t') {
        return true;
    }
    let name = line.iter().take_while(|b| matches!(b, 0x21..=0x39 | 0x3b..=0x7e)).count();
    line.get(name) == Some(&b':')
}

/// `policy.header_source_parse`: (the name, the value after its blanks, line ends inside it kept).
fn source_parse<'a>(lines: &[&'a [u8]]) -> (&'a [u8], Vec<u8>) {
    let first = lines[0];
    let colon = first.iter().position(|b| *b == b':').unwrap_or(first.len());
    let name = &first[..colon];
    let mut value: Vec<u8> = first.get(colon + 1..).unwrap_or_default().to_vec();
    let blanks = value.iter().take_while(|b| **b == b' ' || **b == b'\t').count();
    value.drain(..blanks);
    for line in &lines[1..] {
        value.extend_from_slice(line);
    }
    while value.last().is_some_and(|b| *b == b'\r' || *b == b'\n') {
        value.pop();
    }
    (name, value)
}

/// `FeedParser._parse_headers`, after the lines that read as headers were gathered.
fn parse_headers<'a>(lines: &[&'a [u8]], input: &mut Input<'a>) -> Vec<(&'a [u8], Vec<u8>)> {
    let mut headers = Vec::new();
    let mut current: Vec<&'a [u8]> = Vec::new();
    for (lineno, &line) in lines.iter().enumerate() {
        if line.first().is_some_and(|b| *b == b' ' || *b == b'\t') {
            if !current.is_empty() {
                current.push(line);
            }
            continue;
        }
        if !current.is_empty() {
            headers.push(source_parse(&current));
            current.clear();
        }
        if line.starts_with(b"From ") {
            if lineno == lines.len() - 1 && lineno != 0 {
                input.unreadline(line);
                return headers;
            }
            continue;
        }
        match line.iter().position(|b| *b == b':') {
            Some(0) | None => continue,
            Some(_) => current.push(line),
        }
    }
    if !current.is_empty() {
        headers.push(source_parse(&current));
    }
    headers
}

/// The first value of a header, its line ends taken out.
fn header_value(headers: &[(&[u8], Vec<u8>)], name: &str) -> Option<String> {
    headers.iter().find(|(n, _)| n.eq_ignore_ascii_case(name.as_bytes())).map(|(_, v)| {
        String::from_utf8_lossy(&v.iter().copied().filter(|b| *b != b'\r' && *b != b'\n').collect::<Vec<u8>>())
            .into_owned()
    })
}

/// `get_content_type`: lowercased, `text/plain` for one that isn't a type and a subtype, the default for none.
fn content_type(headers: &[(&[u8], Vec<u8>)], default: &str) -> String {
    let Some(value) = header_value(headers, "content-type") else {
        return default.to_owned();
    };
    let ctype = value.split(';').next().unwrap_or_default().trim().to_lowercase();
    if ctype.matches('/').count() != 1 { "text/plain".to_owned() } else { ctype }
}

/// A parameter's RFC 2231 sections, each with its number.
type Sections = Vec<(u32, String)>;

/// The parameters of a MIME header's value, decoded: (name, value) in order, RFC 2231 sections joined.
fn mime_params(value: &str) -> Vec<(String, String)> {
    let mut pieces: Vec<String> = Vec::new();
    let mut current = String::new();
    let mut quoted = false;
    let mut escape = false;
    for c in value.chars() {
        if escape {
            escape = false;
        } else if c == '\\' && quoted {
            escape = true;
        } else if c == '"' {
            quoted = !quoted;
        } else if c == ';' && !quoted {
            pieces.push(std::mem::take(&mut current));
            continue;
        }
        current.push(c);
    }
    pieces.push(current);
    let mut out: Vec<(String, Sections)> = Vec::new();
    for piece in pieces.iter().skip(1) {
        let (name, raw) = piece.split_once('=').unwrap_or((piece, ""));
        let name = name.trim();
        let raw = raw.trim();
        let unquoted = match raw.strip_prefix('"').and_then(|r| r.strip_suffix('"')) {
            Some(inner) => {
                let mut out = String::new();
                let mut escape = false;
                for c in inner.chars() {
                    if c == '\\' && !escape {
                        escape = true;
                        continue;
                    }
                    escape = false;
                    out.push(c);
                }
                out
            }
            None => raw.to_owned(),
        };
        let (base, section, extended) = match name.split_once('*') {
            Some((base, rest)) => {
                let extended = rest.ends_with('*') || rest.is_empty();
                let section = rest.trim_end_matches('*').parse::<u32>().unwrap_or(0);
                (base.to_owned(), section, extended)
            }
            None => (name.to_owned(), 0, false),
        };
        let text = if extended { decode_2231(&unquoted, section == 0) } else { unquoted };
        match out.iter_mut().find(|(n, _)| *n == base) {
            Some(entry) => entry.1.push((section, text)),
            None => out.push((base, vec![(section, text)])),
        }
    }
    out.into_iter()
        .map(|(name, mut sections)| {
            sections.sort_by_key(|(n, _)| *n);
            (name, sections.into_iter().map(|(_, t)| t).collect())
        })
        .collect()
}

/// An RFC 2231 value: `charset'language'` before the first section, `%XX` escapes, read as UTF-8.
fn decode_2231(value: &str, first: bool) -> String {
    let encoded = if first { value.splitn(3, '\'').nth(2).unwrap_or(value) } else { value };
    let mut bytes = Vec::new();
    let raw = encoded.as_bytes();
    let mut at = 0;
    while at < raw.len() {
        if raw[at] == b'%'
            && let Some(hex) = raw.get(at + 1..at + 3).and_then(|h| std::str::from_utf8(h).ok())
            && let Ok(b) = u8::from_str_radix(hex, 16)
        {
            bytes.push(b);
            at += 3;
            continue;
        }
        bytes.push(raw[at]);
        at += 1;
    }
    String::from_utf8_lossy(&bytes).into_owned()
}

/// `get_boundary`: the boundary parameter, unquoted, blanks at its end dropped.
fn boundary(headers: &[(&[u8], Vec<u8>)]) -> Option<Vec<u8>> {
    let value = header_value(headers, "content-type")?;
    let (_, boundary) = mime_params(&value).into_iter().find(|(n, _)| n.eq_ignore_ascii_case("boundary"))?;
    Some(boundary.trim_end().as_bytes().to_vec())
}

/// `FeedParser._parsegen` of one message or part.
fn parse_message<'a>(input: &mut Input<'a>, default: &str) -> Msg<'a> {
    let mut lines: Vec<&'a [u8]> = Vec::new();
    while let Some(line) = input.readline() {
        if !is_header_line(line) {
            if !matches!(line.first(), Some(b'\r' | b'\n')) {
                input.unreadline(line);
            }
            break;
        }
        lines.push(line);
    }
    let headers = parse_headers(&lines, input);
    let ctype = content_type(&headers, default);
    let main = ctype.split('/').next().unwrap_or_default().to_owned();
    if main == "message" && ctype != "message/delivery-status" {
        let inner = parse_message(input, "text/plain");
        return Msg { headers, body: Body::Message(Box::new(inner)) };
    }
    if main == "multipart" {
        let Some(boundary) = boundary(&headers) else {
            let mut text = Vec::new();
            while let Some(line) = input.readline() {
                text.extend_from_slice(line);
            }
            return Msg { headers, body: Body::Raw(text) };
        };
        let body = parse_parts(input, boundary, &ctype);
        return Msg { headers, body };
    }
    let mut text = Vec::new();
    while let Some(line) = input.readline() {
        text.extend_from_slice(line);
    }
    Msg { headers, body: Body::Text(text) }
}

/// The message whose payload Python's parser last made, whose last line end belongs to the boundary after it.
fn last_made<'m, 'a>(msg: &'m mut Msg<'a>) -> &'m mut Msg<'a> {
    match msg.body {
        Body::Message(ref mut inner) => last_made(inner),
        _ => msg,
    }
}

/// The multipart branch of `_parsegen`: the preamble, each part to the next boundary, the epilogue.
fn parse_parts<'a>(input: &mut Input<'a>, boundary: Vec<u8>, ctype: &str) -> Body<'a> {
    let mut separator = b"--".to_vec();
    separator.extend_from_slice(&boundary);
    let default = if ctype == "multipart/digest" { "message/rfc822" } else { "text/plain" };
    let mut capturing_preamble = true;
    let mut preamble_lines: Vec<&'a [u8]> = Vec::new();
    let mut preamble: Option<Vec<u8>> = None;
    let mut parts: Vec<Msg<'a>> = Vec::new();
    let mut closed = None;
    while let Some(line) = input.readline() {
        let Some((end, linesep)) = boundary_match(line, &separator) else {
            preamble_lines.push(line);
            continue;
        };
        if end {
            closed = Some(linesep);
            break;
        }
        if capturing_preamble {
            if !preamble_lines.is_empty() {
                let mut text: Vec<u8> = preamble_lines.concat();
                without_eol(&mut text);
                preamble = Some(text);
            }
            capturing_preamble = false;
            input.unreadline(line);
            continue;
        }
        loop {
            match input.readline() {
                Some(next) if boundary_match(next, &separator).is_some() => {}
                Some(next) => {
                    input.unreadline(next);
                    break;
                }
                None => {
                    input.unreadline(b"");
                    break;
                }
            }
        }
        input.ends.push(separator.clone());
        let mut part = parse_message(input, default);
        let last = last_made(&mut part);
        match &mut last.body {
            Body::Parts { epilogue, .. } => match epilogue {
                Some(text) if text.is_empty() => *epilogue = None,
                Some(text) => without_eol(text),
                None => {}
            },
            Body::Text(text) => without_eol(text),
            Body::Raw(_) | Body::Message(_) => {}
        }
        input.ends.pop();
        parts.push(part);
    }
    if capturing_preamble {
        while input.readline().is_some() {}
        return Body::Raw(preamble_lines.concat());
    }
    let Some(linesep) = closed else {
        return Body::Parts { boundary, preamble, parts, epilogue: None };
    };
    let mut epilogue: Vec<&'a [u8]> = Vec::new();
    while let Some(line) = input.readline() {
        epilogue.push(line);
    }
    let mut text: Vec<u8> = epilogue.concat();
    if !linesep {
        for eol in [&b"\r\n"[..], b"\r", b"\n"] {
            if text.starts_with(eol) {
                text.drain(..eol.len());
                break;
            }
        }
    }
    Body::Parts { boundary, preamble, parts, epilogue: Some(text) }
}

/// `str.splitlines()` of a header's value.
fn splitlines(value: &[u8]) -> Vec<&[u8]> {
    let mut out = Vec::new();
    let mut start = 0;
    let mut at = 0;
    while at < value.len() {
        let b = value[at];
        if matches!(b, b'\n' | b'\r' | 0x0b | 0x0c | 0x1c..=0x1e) {
            out.push(&value[start..at]);
            at += if b == b'\r' && value.get(at + 1) == Some(&b'\n') { 2 } else { 1 };
            start = at;
        } else {
            at += 1;
        }
    }
    if start < value.len() {
        out.push(&value[start..]);
    }
    out
}

/// `policy.fold_binary(name, value)` of a header as parsed, and `verify_generated_headers` on what it gives.
fn write_header(name: &[u8], value: &[u8], out: &mut Vec<u8>) -> Result<(), String> {
    let lines = splitlines(value);
    let long = lines.first().is_some_and(|l| l.len() + name.len() + 2 > fold::MAX_LINE)
        || lines.iter().skip(1).any(|l| l.len() > fold::MAX_LINE);
    let folded = if long && value.is_ascii() && name.is_ascii() {
        let joined: String = lines.iter().map(|l| String::from_utf8_lossy(l)).collect();
        refold(&String::from_utf8_lossy(name), &joined).into_bytes()
    } else {
        [name, b": ", &lines.join(fold::LINESEP.as_bytes()), fold::LINESEP.as_bytes()].concat()
    };
    mime::verify(&folded)?;
    out.extend_from_slice(&folded);
    Ok(())
}

/// `header_factory(name, value).fold(policy)`, as the registry parses the name.
fn refold(name: &str, value: &str) -> String {
    let lower = name.to_lowercase();
    let tree = match lower.as_str() {
        "from" | "to" | "cc" | "bcc" | "reply-to" | "sender" | "resent-from" | "resent-to" | "resent-cc"
        | "resent-bcc" | "resent-sender" => {
            parse::get_address_list(value).unwrap_or_else(|()| parse::get_unstructured(value))
        }
        "message-id" => parse::parse_message_id(value),
        "in-reply-to" | "references" => parse::parse_message_ids(value),
        "content-type" | "content-disposition" => {
            let (head, _) = value.split_once(';').unwrap_or((value, ""));
            let head = head.trim();
            let mut parts = match head.split_once('/') {
                Some((main, sub)) if lower == "content-type" => vec![
                    Tok::list(Kind::Token, vec![Tok::value(main, "mime-ttext")]),
                    Tok::value("/", "content-type-separator"),
                    Tok::list(Kind::Token, vec![Tok::value(sub, "mime-ttext")]),
                ],
                _ => vec![Tok::list(Kind::Token, vec![Tok::value(head, "mime-ttext")])],
            };
            if value.contains(';') {
                parts.push(Tok::value(";", "parameter-separator"));
                parts.push(Tok::mime_parameters(mime_params(value)));
            }
            let kind = if lower == "content-type" { Kind::ContentType } else { Kind::ContentDisposition };
            Tok::list(kind, parts)
        }
        _ => parse::get_unstructured(value),
    };
    let mut parts =
        vec![Tok::list(Kind::HeaderLabel, vec![Tok::value(name, "header-name"), Tok::value(":", "header-sep")])];
    if !tree.parts.is_empty() {
        parts.push(Tok::list(Kind::Cfws, vec![Tok::space(" ", "fws")]));
    }
    parts.push(tree);
    fold::refold(&Tok::list(Kind::Header, parts))
}

/// `Generator._write_lines`.
fn write_lines(text: &[u8], out: &mut Vec<u8>) {
    let lines = Input::new(text).lines;
    for line in lines {
        let bare = line.strip_suffix(b"\r\n").or_else(|| line.strip_suffix(b"\n")).or_else(|| line.strip_suffix(b"\r"));
        match bare {
            Some(bare) => {
                out.extend_from_slice(bare);
                out.extend_from_slice(fold::LINESEP.as_bytes());
            }
            None => out.extend_from_slice(line),
        }
    }
}

/// `BytesGenerator.flatten(msg)`: the body first, as `Generator._write` writes it, then the headers above it.
fn write(msg: &Msg<'_>, out: &mut Vec<u8>) -> Result<(), String> {
    let nl = fold::LINESEP.as_bytes();
    let mut body = Vec::new();
    match &msg.body {
        Body::Text(text) => write_lines(text, &mut body),
        Body::Raw(text) => body.extend_from_slice(text),
        Body::Message(inner) => write(inner, &mut body)?,
        Body::Parts { boundary, preamble, parts, epilogue } => {
            if let Some(preamble) = preamble {
                write_lines(preamble, &mut body);
                body.extend_from_slice(nl);
            }
            body.extend_from_slice(b"--");
            body.extend_from_slice(boundary);
            body.extend_from_slice(nl);
            for (i, part) in parts.iter().enumerate() {
                if i > 0 {
                    body.extend_from_slice(nl);
                    body.extend_from_slice(b"--");
                    body.extend_from_slice(boundary);
                    body.extend_from_slice(nl);
                }
                write(part, &mut body)?;
            }
            body.extend_from_slice(nl);
            body.extend_from_slice(b"--");
            body.extend_from_slice(boundary);
            body.extend_from_slice(b"--");
            body.extend_from_slice(nl);
            if let Some(epilogue) = epilogue {
                write_lines(epilogue, &mut body);
            }
        }
    }
    for (name, value) in &msg.headers {
        write_header(name, value, out)?;
    }
    out.extend_from_slice(nl);
    out.extend_from_slice(&body);
    Ok(())
}

/// The attached message's bytes as Python writes the message it parsed from them, or the words of what Python
/// raises writing it.
pub(super) fn rewrite(data: &[u8]) -> Result<Vec<u8>, String> {
    let mut input = Input::new(data);
    let msg = parse_message(&mut input, "text/plain");
    let mut out = Vec::new();
    write(&msg, &mut out)?;
    Ok(out)
}

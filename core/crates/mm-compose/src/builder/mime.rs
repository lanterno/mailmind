//! A message as `email.message.EmailMessage` holds one once built, part by part, and its bytes as
//! `as_bytes(policy=email.policy.SMTP)` writes them: `email.generator.BytesGenerator`, with each multipart's boundary
//! made as `_make_boundary` makes one when the part is written.

use super::fold::{self, Kind, Tok};
use super::parse;
use crate::spec::Draws;

/// A header's value, as the header class Python's registry gives its name parses it.
#[derive(Clone, Debug)]
pub(super) enum Value {
    /// `UnstructuredHeader` (and `DateHeader`, which folds as one).
    Text(String),
    /// `AddressHeader`, from (display name, addr-spec) pairs.
    Addresses(Vec<(String, String)>),
    /// `MessageIDHeader`.
    MessageId(String),
    /// `ReferencesHeader`: In-Reply-To and References.
    MessageIds(String),
    /// `MIMEVersionHeader`, which says 1.0.
    MimeVersion,
    /// `ContentTypeHeader`.
    ContentType { main: String, sub: String, params: Vec<(String, String)> },
    /// `ContentDispositionHeader`.
    Disposition { kind: String, params: Vec<(String, String)> },
    /// `ContentTransferEncodingHeader`.
    Encoding(&'static str),
}

/// What a part holds below its headers.
#[derive(Clone, Debug)]
pub(super) enum Body {
    /// Text whose line ends the generator writes as CRLF (`_handle_text`).
    Lines(String),
    /// Text written as it is, line ends and all: what `_handle_message` and `_handle_multipart` write of a payload
    /// that is a string.
    Raw(String),
    /// A multipart's parts.
    Parts(Vec<Part>),
    /// An attached message's bytes, which the generator parses and writes again.
    Message(Vec<u8>),
    /// `message/delivery-status` over bytes, which Python's generator can't write.
    Unwritable,
}

/// A part, or the message itself: its headers in order, and its body.
#[derive(Clone, Debug)]
pub(super) struct Part {
    pub headers: Vec<(&'static str, Value)>,
    pub body: Body,
}

/// `str(Address(display_name, username, domain))`, split from an addr-spec as `builder._addr` splits it.
fn address(name: &str, spec: &str) -> String {
    let (local, domain) = spec.rsplit_once('@').unwrap_or(("", spec));
    let local = if local.chars().any(|c| (fold::SPECIALS.contains(c) && c != '.') || c == ' ' || c == '\t') {
        fold::quote_string(local)
    } else {
        local.to_owned()
    };
    let addr_spec = if !domain.is_empty() {
        format!("{local}@{domain}")
    } else if local.is_empty() {
        "<>".to_owned()
    } else {
        local
    };
    let name =
        if name.chars().any(|c| fold::SPECIALS.contains(c)) { fold::quote_string(name) } else { name.to_owned() };
    if name.is_empty() {
        return addr_spec;
    }
    let addr_spec = if addr_spec == "<>" { String::new() } else { addr_spec };
    format!("{name} <{addr_spec}>")
}

/// `get_token`'s tree of a MIME type or disposition: one `mime-ttext`, or, for text that isn't a single token,
/// the text as one terminal.
fn token(text: &str) -> Tok {
    Tok::list(Kind::Token, vec![Tok::value(text, "mime-ttext")])
}

/// A MIME token: no blanks, no controls, none of `TSPECIALS`.
fn is_token(text: &str) -> bool {
    !text.is_empty()
        && !text.chars().any(|c| {
            (fold::SPECIALS.contains(c) && c != '.') || matches!(c, '/' | '?' | '=' | ' ' | '\t') || c.is_control()
        })
}

/// The tree Python's registry parses the value into.
fn tree(value: &Value) -> Tok {
    match value {
        Value::Text(text) => parse::get_unstructured(text),
        Value::Addresses(list) => {
            let decoded = list.iter().map(|(name, spec)| address(name, spec)).collect::<Vec<_>>().join(", ");
            parse::get_address_list(&decoded)
                .unwrap_or_else(|()| Tok::list(Kind::AddressList, vec![Tok::value(decoded, "utext")]))
        }
        Value::MessageId(id) => parse::parse_message_id(id),
        Value::MessageIds(ids) => parse::parse_message_ids(ids),
        Value::MimeVersion => Tok::list(
            Kind::MimeVersion,
            vec![Tok::value("1", "digits"), Tok::value(".", "version-separator"), Tok::value("0", "digits")],
        ),
        Value::ContentType { main, sub, params } => {
            let mut parts = if is_token(main) && is_token(sub) {
                vec![token(main), Tok::value("/", "content-type-separator"), token(sub)]
            } else {
                vec![Tok::value(format!("{main}/{sub}"), "mime-ttext")]
            };
            if !params.is_empty() {
                parts.push(Tok::value(";", "parameter-separator"));
                parts.push(Tok::mime_parameters(params.clone()));
            }
            Tok::list(Kind::ContentType, parts)
        }
        Value::Disposition { kind, params } => {
            let mut parts = vec![token(kind)];
            if !params.is_empty() {
                parts.push(Tok::value(";", "parameter-separator"));
                parts.push(Tok::mime_parameters(params.clone()));
            }
            Tok::list(Kind::ContentDisposition, parts)
        }
        Value::Encoding(cte) => Tok::list(Kind::ContentTransferEncoding, vec![token(cte)]),
    }
}

/// `BaseHeader.fold`, then `policy.fold_binary`'s ASCII: the header's lines, or the words of the
/// `UnicodeEncodeError` Python raises for text a header couldn't encode.
pub(super) fn header(name: &str, value: &Value) -> Result<String, String> {
    let value = tree(value);
    let mut parts =
        vec![Tok::list(Kind::HeaderLabel, vec![Tok::value(name, "header-name"), Tok::value(":", "header-sep")])];
    if !value.parts.is_empty() || value.kind == Kind::MimeParameters {
        parts.push(Tok::list(Kind::Cfws, vec![Tok::space(" ", "fws")]));
    }
    parts.push(value);
    let folded = fold::refold(&Tok::list(Kind::Header, parts));
    ascii(&folded)?;
    verify(folded.as_bytes())?;
    Ok(folded)
}

/// `verify_generated_headers`: a folded header whose line ends each start a continuation, or the words of the
/// `HeaderWriteError` Python raises for one that doesn't.
pub(super) fn verify(folded: &[u8]) -> Result<(), String> {
    let inner = folded.strip_suffix(fold::LINESEP.as_bytes()).unwrap_or(folded);
    let bad = inner.windows(2).enumerate().any(|(at, pair)| match pair {
        [b'\r', b'\n'] => inner.get(at + 2).is_some_and(|c| *c != b' ' && *c != b'\t'),
        [b'\r', c] | [b'\n', c] => *c != b' ' && *c != b'\t',
        _ => false,
    });
    if bad {
        return Err(format!("folded header contains newline: {}", bytes_repr(folded)));
    }
    Ok(())
}

/// `repr(b)` of bytes.
fn bytes_repr(bytes: &[u8]) -> String {
    let quote = if bytes.contains(&b'\'') && !bytes.contains(&b'"') { '"' } else { '\'' };
    let mut out = format!("b{quote}");
    for &b in bytes {
        match b {
            b'\\' => out.push_str("\\\\"),
            b'\t' => out.push_str("\\t"),
            b'\n' => out.push_str("\\n"),
            b'\r' => out.push_str("\\r"),
            b if char::from(b) == quote => {
                out.push('\\');
                out.push(quote);
            }
            b' '..=b'~' => out.push(char::from(b)),
            b => out.push_str(&format!("\\x{b:02x}")),
        }
    }
    out.push(quote);
    out
}

/// `text.encode('ascii', 'surrogateescape')`, or the `UnicodeEncodeError`'s words.
pub(super) fn ascii(text: &str) -> Result<(), String> {
    let chars: Vec<char> = text.chars().collect();
    let Some(start) = chars.iter().position(|c| !c.is_ascii()) else {
        return Ok(());
    };
    let end = start + chars[start..].iter().take_while(|c| !c.is_ascii()).count();
    if end == start + 1 {
        let c = u32::from(chars[start]);
        let shown = if c < 0x100 {
            format!("\\x{c:02x}")
        } else if c < 0x10000 {
            format!("\\u{c:04x}")
        } else {
            format!("\\U{c:08x}")
        };
        return Err(format!(
            "'ascii' codec can't encode character '{shown}' in position {start}: ordinal not in range(128)"
        ));
    }
    Err(format!("'ascii' codec can't encode characters in position {start}-{}: ordinal not in range(128)", end - 1))
}

/// `base64.b64encode`.
pub(super) fn base64(bytes: &[u8]) -> String {
    const ALPHABET: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity(bytes.len().div_ceil(3) * 4);
    for chunk in bytes.chunks(3) {
        let n = chunk.iter().enumerate().fold(0u32, |n, (i, b)| n | u32::from(*b) << (16 - 8 * i));
        for i in 0..4 {
            if i <= chunk.len() {
                out.push(char::from(ALPHABET[(n >> (18 - 6 * i) & 63) as usize]));
            } else {
                out.push('=');
            }
        }
    }
    out
}

/// `contentmanager._encode_base64`: 57 bytes to a line of 76, each line ending `\n`.
pub(super) fn base64_lines(bytes: &[u8]) -> String {
    let mut out = String::new();
    for chunk in bytes.chunks(fold::MAX_LINE / 4 * 3) {
        out.push_str(&base64(chunk));
        out.push('\n');
    }
    out
}

/// `contentmanager._encode_text(text, 'utf-8', 'quoted-printable', policy)`: each line of the text, `\n` after
/// each, through `quoprimime.body_encode` at 78 columns.
pub(super) fn quoted_printable(text: &str) -> String {
    let bytes = text.as_bytes();
    let mut body: Vec<u8> = Vec::with_capacity(bytes.len() + 1);
    let mut at = 0;
    while at < bytes.len() {
        match bytes[at] {
            b'\r' => {
                body.push(b'\n');
                if bytes.get(at + 1) == Some(&b'\n') {
                    at += 1;
                }
            }
            b => body.push(b),
        }
        at += 1;
    }
    if !body.ends_with(b"\n") || bytes.is_empty() {
        body.push(b'\n');
    }
    body_encode(&body, fold::MAX_LINE)
}

/// `quoprimime.body_encode(body, maxlinelen)`, over text whose lines end `\n`.
fn body_encode(body: &[u8], maxlinelen: usize) -> String {
    let mut translated = String::with_capacity(body.len());
    for &b in body {
        if b == b'\n' || b == b'\r' || b == b'\t' || ((b' '..=b'~').contains(&b) && b != b'=') {
            translated.push(char::from(b));
        } else {
            translated.push_str(&format!("={b:02X}"));
        }
    }
    let quote = |c: u8| format!("={c:02X}");
    let mut encoded: Vec<String> = Vec::new();
    let maxlinelen1 = maxlinelen - 1;
    for line in translated.split_terminator('\n') {
        let line = line.as_bytes();
        let text = |from: usize, to: usize| String::from_utf8_lossy(&line[from..to.max(from)]).into_owned();
        let mut start = 0i64;
        let laststart = line.len() as i64 - 1 - maxlinelen as i64;
        while start <= laststart {
            let stop = (start as usize) + maxlinelen1;
            if line[stop - 2] == b'=' {
                encoded.push(text(start as usize, stop - 1));
                start = stop as i64 - 2;
            } else if line[stop - 1] == b'=' {
                encoded.push(text(start as usize, stop));
                start = stop as i64 - 1;
            } else {
                encoded.push(text(start as usize, stop) + "=");
                start = stop as i64;
            }
        }
        match line.last() {
            Some(&c) if c == b' ' || c == b'\t' => {
                let room = start - laststart;
                let q = if room >= 3 {
                    quote(c)
                } else if room == 2 {
                    format!("{}=\n", char::from(c))
                } else {
                    format!("=\n{}", quote(c))
                };
                encoded.push(text(start as usize, line.len() - 1) + &q);
            }
            _ => encoded.push(text(start as usize, line.len())),
        }
    }
    if translated.ends_with('\n') {
        encoded.push(String::new());
    }
    encoded.join("\n")
}

/// `Generator._write_lines`: each line ended CRLF, the last as it is.
fn write_lines(text: &[u8], out: &mut Vec<u8>) {
    let mut at = 0;
    let mut line_start = 0;
    while at < text.len() {
        match text[at] {
            b'\r' | b'\n' => {
                out.extend_from_slice(&text[line_start..at]);
                out.extend_from_slice(fold::LINESEP.as_bytes());
                if text[at] == b'\r' && text.get(at + 1) == Some(&b'\n') {
                    at += 1;
                }
                at += 1;
                line_start = at;
            }
            _ => at += 1,
        }
    }
    out.extend_from_slice(&text[line_start..]);
}

/// `Generator._make_boundary(text)`: fifteen `=`, a 19-digit number drawn as `random.randrange(sys.maxsize)`, `==`,
/// and `.0`, `.1`, ... after it while a line of the text is that boundary.
fn make_boundary(text: &[u8], draws: &dyn Draws) -> String {
    let token = loop {
        let drawn = draws.number() >> 1;
        if drawn < i64::MAX as u64 {
            break drawn;
        }
    };
    let boundary = format!("==============={token:019}==");
    let mut candidate = boundary.clone();
    let mut counter = 0u64;
    loop {
        let pattern = format!("(?m)^--{}(--)?$", regex::escape(&candidate));
        let taken = regex::bytes::Regex::new(&pattern).is_ok_and(|re| re.is_match(text));
        if !taken {
            return candidate;
        }
        candidate = format!("{boundary}.{counter}");
        counter += 1;
    }
}

/// The part's bytes as `BytesGenerator` writes them: its body first, which draws the boundaries of the multiparts
/// inside it before its own, then its headers above it.
pub(super) fn write(part: &Part, draws: &dyn Draws) -> Result<Vec<u8>, String> {
    let mut body: Vec<u8> = Vec::new();
    let mut boundary = None;
    match &part.body {
        Body::Lines(text) => write_lines(text.as_bytes(), &mut body),
        Body::Raw(text) => body.extend_from_slice(text.as_bytes()),
        Body::Message(bytes) => body.extend_from_slice(&super::rfc822::rewrite(bytes)?),
        Body::Unwritable => return Err("'str' object has no attribute 'policy'".to_owned()),
        Body::Parts(parts) => {
            let texts = parts.iter().map(|p| write(p, draws)).collect::<Result<Vec<_>, _>>()?;
            let chosen = make_boundary(&texts.join(fold::LINESEP.as_bytes()), draws);
            body.extend_from_slice(format!("--{chosen}{}", fold::LINESEP).as_bytes());
            for (i, text) in texts.iter().enumerate() {
                if i > 0 {
                    body.extend_from_slice(format!("{}--{chosen}{}", fold::LINESEP, fold::LINESEP).as_bytes());
                }
                body.extend_from_slice(text);
            }
            body.extend_from_slice(format!("{}--{chosen}--{}", fold::LINESEP, fold::LINESEP).as_bytes());
            boundary = Some(chosen);
        }
    }
    let mut out: Vec<u8> = Vec::new();
    for (name, value) in &part.headers {
        let value = match (value, &boundary) {
            (Value::ContentType { main, sub, params }, Some(chosen)) => {
                let mut params = params.clone();
                params.push(("boundary".to_owned(), chosen.clone()));
                Value::ContentType { main: main.clone(), sub: sub.clone(), params }
            }
            _ => value.clone(),
        };
        out.extend_from_slice(header(name, &value)?.as_bytes());
    }
    out.extend_from_slice(fold::LINESEP.as_bytes());
    out.extend_from_slice(&body);
    Ok(out)
}

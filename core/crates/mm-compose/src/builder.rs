//! Building an outgoing message (`mailmind.compose.domain.builder`), byte for byte as the Python app builds it with
//! `email.message.EmailMessage` and writes it with `as_bytes(policy=email.policy.SMTP)`: its headers in Python's order
//! and folded as Python folds them, its parts and their encodings, its MIME boundaries and its Message-ID drawn as
//! Python draws them. Every address is validated and IDNA-normalized first, with Python's words for what it refuses,
//! and Bcc is never written into the bytes: it rides only in the envelope.
//!
//! Where it stops matching Python: an RFC 2047 encoded word a person typed into a subject or a name is decoded only
//! when its charset is UTF-8, ASCII or Latin-1, as Python decodes any it knows; a MIME type that isn't two tokens
//! folds as one piece when it doesn't fit on its line; a reference that ends in a line feed is refused, where Python
//! took it; and an attached message is written again as `rfc822` says.

mod fold;
mod mime;
mod parse;
// A message of a few headers over a plain text: the export's, for a copy whose whole message was never fetched.
mod plain;
mod rfc822;

pub use plain::{Header, plain_message};

use std::collections::{BTreeSet, HashSet};

use mm_kernel::addresses::{MAX_RECIPIENTS, clean, getaddresses, idna_domain};
use mm_kernel::text::{is_printable, is_space, strip};

use crate::markdown::{content_id, plain_pictures};
use crate::spec::{Attachment, Built, MessageSpec, Minting};
use mime::{Body, Part, Value};

/// `repr(text)` of a `str`, as Python's error words echo an address.
fn py_repr(text: &str) -> String {
    let quote = if text.contains('\'') && !text.contains('"') { '"' } else { '\'' };
    let mut out = String::from(quote);
    for c in text.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\t' => out.push_str("\\t"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if c.is_ascii() && !(' '..='~').contains(&c) => out.push_str(&format!("\\x{:02x}", u32::from(c))),
            c if c.is_ascii() || is_printable(c) => out.push(c),
            c if u32::from(c) < 0x100 => out.push_str(&format!("\\x{:02x}", u32::from(c))),
            c if u32::from(c) < 0x10000 => out.push_str(&format!("\\u{:04x}", u32::from(c))),
            c => out.push_str(&format!("\\U{:08x}", u32::from(c))),
        }
    }
    out.push(quote);
    out
}

/// `addresses._DANGER`: never in a header.
fn is_danger(c: char) -> bool {
    matches!(c,
        '\u{0}'..='\u{1f}' | '\u{7f}'..='\u{9f}' | '\u{200b}'..='\u{200f}' | '\u{202a}'..='\u{202e}' | '\u{2060}'
        | '\u{2066}'..='\u{2069}' | '\u{2028}' | '\u{2029}' | '\u{feff}')
}

/// `addresses._ADDR_BAD`: never in an addr-spec.
fn is_addr_bad(c: char) -> bool {
    is_space(c) || ",;:<>()[]\\\"".contains(c) || c <= '\u{1f}' || c == '\u{7f}'
}

/// `addresses.idna_domain`, with its words.
fn worded_idna_domain(domain: &str) -> Result<String, String> {
    let trimmed = strip(domain).trim_end_matches('.');
    if trimmed.is_empty() || (!trimmed.contains('.') && trimmed.to_lowercase() != "localhost") {
        return Err("invalid domain".to_owned());
    }
    idna_domain(Some(domain)).ok_or_else(|| format!("invalid domain: {}", py_repr(trimmed)))
}

/// `addresses.validate_pair`, with its words: (the display name made clean, local@IDNA-domain).
fn validate_pair(name: &str, addr: &str) -> Result<(String, String), String> {
    let addr = strip(addr);
    let not_valid = || format!("not a valid address: {}", py_repr(addr));
    if addr.is_empty() || addr.matches('@').count() != 1 {
        return Err(not_valid());
    }
    if addr.chars().any(is_danger) {
        return Err(format!("unsafe characters in address: {}", py_repr(addr)));
    }
    let (local, domain) = addr.rsplit_once('@').ok_or_else(not_valid)?;
    if local.is_empty() || local.chars().any(is_addr_bad) || domain.chars().any(is_addr_bad) {
        return Err(not_valid());
    }
    let ascii = worded_idna_domain(domain)?;
    if ascii.contains('@') || ascii.chars().any(is_addr_bad) {
        return Err(not_valid());
    }
    Ok((clean(Some(name)), format!("{local}@{ascii}")))
}

/// `addresses.parse_addr_list` of a list of tokens, with its words: each (name, addr-spec), the first of each
/// addr-spec whatever its case.
fn parse_addr_list(tokens: &[String]) -> Result<Vec<(String, String)>, String> {
    let given: Vec<&str> = tokens.iter().map(String::as_str).filter(|t| !t.is_empty()).collect();
    let mut out: Vec<(String, String)> = Vec::new();
    let mut seen = HashSet::new();
    for (name, addr) in getaddresses(&given) {
        let (name, spec) = validate_pair(&name, &addr)?;
        if seen.insert(spec.to_lowercase()) {
            out.push((name, spec));
        }
    }
    Ok(out)
}

/// `_MSGID.match(token)`: `<`, an id-left and an id-right around an `@`, `>`, nothing blank or angled inside.
fn is_msgid(token: &str) -> bool {
    let Some(inner) = token.strip_prefix('<').and_then(|t| t.strip_suffix('>')) else {
        return false;
    };
    if inner.chars().any(|c| c == '<' || c == '>' || is_space(c)) {
        return false;
    }
    let chars: Vec<char> = inner.chars().collect();
    chars.len() >= 3 && chars[1..chars.len() - 1].contains(&'@')
}

/// `re.findall(r"<[^<>\s]+>", text)`.
fn angled(text: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let mut at = 0;
    while let Some(open) = text[at..].find('<') {
        let start = at + open;
        let inner = &text[start + 1..];
        let run = inner.find(|c: char| c == '<' || c == '>' || is_space(c)).unwrap_or(inner.len());
        if run > 0 && inner[run..].starts_with('>') {
            out.push(&text[start..start + 1 + run + 1]);
            at = start + run + 2;
        } else {
            at = start + 1;
        }
    }
    out
}

/// `reference_chain`: the References chain of a reply (RFC 5322 3.6.4), each once, the parent last.
pub fn reference_chain(
    orig_message_id: Option<&str>,
    orig_refs: Option<&str>,
    orig_in_reply_to: Option<&str>,
) -> Vec<String> {
    let mut ids: Vec<&str> = Vec::new();
    for blob in [orig_refs.unwrap_or_default(), orig_in_reply_to.unwrap_or_default()] {
        ids.extend(angled(blob));
    }
    if let Some(id) = orig_message_id.filter(|id| !id.is_empty()) {
        ids.push(strip(id));
    }
    let mut out: Vec<String> = Vec::new();
    for id in ids {
        if is_msgid(id) && !out.iter().any(|o| o == id) {
            out.push(id.to_owned());
        }
    }
    out
}

/// `thread_ids(in_reply_to, references)`: a saved draft's In-Reply-To and References, well-formed Message-IDs
/// only, the references each once in order and at most 50 (the thread's root and the last 49).
pub fn thread_ids(in_reply_to: Option<&str>, references: Option<&str>) -> (Option<String>, Vec<String>) {
    let irt = angled(in_reply_to.unwrap_or_default()).into_iter().find(|t| is_msgid(t)).map(str::to_owned);
    let mut refs: Vec<String> = Vec::new();
    for token in angled(references.unwrap_or_default()) {
        if is_msgid(token) && !refs.iter().any(|r| r == token) {
            refs.push(token.to_owned());
        }
    }
    if refs.len() > 50 {
        let tail = refs.split_off(refs.len() - 49);
        refs.truncate(1);
        refs.extend(tail);
    }
    (irt, refs)
}

/// `_each_once`: a person named in To and again in Cc or Bcc gets one RCPT TO, by their first spelling.
fn each_once<'a>(addrs: impl Iterator<Item = &'a str>) -> Vec<String> {
    let mut seen = HashSet::new();
    addrs.filter(|a| seen.insert(a.to_lowercase())).map(str::to_owned).collect()
}

/// `_kind`: a file's (maintype, subtype), its parameters dropped, `application/octet-stream` for no subtype.
fn kind(att: &Attachment) -> (String, String) {
    let ct = if att.content_type.is_empty() { "application/octet-stream" } else { att.content_type.as_str() };
    let ct = strip(ct.split(';').next().unwrap_or_default());
    match ct.split_once('/') {
        Some((main, sub)) if !sub.is_empty() => (main.to_owned(), sub.to_owned()),
        _ => ("application".to_owned(), "octet-stream".to_owned()),
    }
}

/// `str.splitlines()` finds more than one line in `value`: what `header_store_parse` refuses in a header.
fn breaks_lines(value: &str) -> bool {
    let is_break = |c: char| {
        matches!(c, '\n' | '\r' | '\u{b}' | '\u{c}' | '\u{1c}'..='\u{1e}' | '\u{85}' | '\u{2028}' | '\u{2029}')
    };
    let trimmed = value.strip_suffix("\r\n").or_else(|| value.strip_suffix(is_break)).unwrap_or(value);
    trimmed.contains(is_break)
}

const HEADER_BREAK: &str = "Header values may not contain linefeed or carriage return characters";

/// `email.utils.format_datetime` of the moment in UTC: `Mon, 02 Nov 2026 12:00:00 +0000`.
fn format_datetime(minting: &Minting<'_>) -> String {
    let seconds = minting.now.whole_seconds();
    let days = seconds.div_euclid(86_400);
    let of_day = seconds.rem_euclid(86_400);
    let (year, month, day) = mm_kernel::when::civil(days);
    let weekday = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][(days + 3).rem_euclid(7) as usize];
    let month =
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][month as usize - 1];
    format!(
        "{weekday}, {day:02} {month} {year:04} {:02}:{:02}:{:02} +0000",
        of_day / 3600,
        of_day % 3600 / 60,
        of_day % 60
    )
}

/// `email.utils.make_msgid(domain=domain)`: hundredths of a second, the process id and 64 random bits.
fn make_msgid(minting: &Minting<'_>, domain: &str) -> String {
    let timeval = (minting.now.seconds() * 100.0) as i64;
    format!("<{timeval}.{}.{}@{domain}>", minting.pid, minting.draws.number())
}

/// A text part as `set_content(text, subtype=..., charset="utf-8", cte="quoted-printable")` makes it.
fn text_part(subtype: &str, text: &str) -> Part {
    Part {
        headers: vec![
            (
                "Content-Type",
                Value::ContentType {
                    main: "text".to_owned(),
                    sub: subtype.to_owned(),
                    params: vec![("charset".to_owned(), "utf-8".to_owned())],
                },
            ),
            ("Content-Transfer-Encoding", Value::Encoding("quoted-printable")),
        ],
        body: Body::Lines(mime::quoted_printable(text)),
    }
}

/// A file's base64 as the generator writes it for its type: its lines ended CRLF, or, for a `message/*` or
/// `multipart/*` file, as they are.
fn file_body(main: &str, sub: &str, data: &[u8]) -> Body {
    let ctype = format!("{main}/{sub}").to_lowercase();
    let (main, sub) = ctype.split_once('/').filter(|(_, s)| !s.contains('/')).unwrap_or(("text", "plain"));
    let encoded = mime::base64_lines(data);
    match (main, sub) {
        ("message", "delivery-status") => Body::Unwritable,
        ("message" | "multipart", _) => Body::Raw(encoded),
        _ => Body::Lines(encoded),
    }
}

/// `set_param(key, value)` of each parameter in turn on a value without them: a parameter of the same name, in
/// any case, takes the place of the one before.
fn with_params(params: &[(String, String)]) -> Vec<(String, String)> {
    let mut out: Vec<(String, String)> = Vec::new();
    for (key, value) in params {
        match out.iter_mut().find(|(k, v)| k.eq_ignore_ascii_case(key) && !v.is_empty()) {
            Some(slot) => *slot = (key.clone(), value.clone()),
            None => out.push((key.clone(), value.clone())),
        }
    }
    out
}

/// What `_formatparam` writes of a parameter, as `header_store_parse` checks it: refused when it breaks a line.
fn param_breaks(key: &str, value: &str) -> bool {
    breaks_lines(key) || (value.is_ascii() && breaks_lines(&format!("\"{value}\"")))
}

/// `build_message(spec, draft)`: the message's bytes, its Message-ID, its envelope and who it is to, or the words
/// of the ValueError Python raises. `draft` lifts the need for a recipient and keeps each picture's mark in the text.
pub fn build_message(spec: &MessageSpec, draft: bool, minting: Minting<'_>) -> Result<Built, String> {
    let (from_name, from_spec) = validate_pair(&spec.account_name, &spec.account)?;
    let from_domain = from_spec.rsplit_once('@').map_or("", |(_, d)| d).to_owned();
    let to = parse_addr_list(&spec.to)?;
    let cc = parse_addr_list(&spec.cc)?;
    let bcc = parse_addr_list(&spec.bcc)?;
    if to.is_empty() && cc.is_empty() && bcc.is_empty() && !draft {
        return Err("at least one recipient is required".to_owned());
    }
    if to.len() + cc.len() + bcc.len() > MAX_RECIPIENTS {
        return Err(format!("too many recipients (max {MAX_RECIPIENTS})"));
    }

    let mut headers: Vec<(&'static str, Value)> = vec![("From", Value::Addresses(vec![(from_name, from_spec)]))];
    if !to.is_empty() {
        headers.push(("To", Value::Addresses(to.clone())));
    }
    if !cc.is_empty() {
        headers.push(("Cc", Value::Addresses(cc.clone())));
    }
    headers.push(("Subject", Value::Text(clean(Some(&spec.subject)))));
    headers.push(("Date", Value::Text(format_datetime(&minting))));
    let mid = make_msgid(&minting, &from_domain);
    headers.push(("Message-ID", Value::MessageId(mid.clone())));
    let irt = strip(spec.in_reply_to.as_deref().unwrap_or_default());
    if is_msgid(irt) {
        headers.push(("In-Reply-To", Value::MessageIds(irt.to_owned())));
    }
    let refs: Vec<&str> = spec.references.iter().map(String::as_str).filter(|t| is_msgid(t)).collect();
    if !refs.is_empty() {
        headers.push(("References", Value::MessageIds(refs.join(" "))));
    }
    headers.push(("User-Agent", Value::Text("MailMind".to_owned())));

    let html = spec.html.as_deref();
    let shown = |a: &Attachment| {
        html.is_some_and(|h| a.cid.as_ref().is_some_and(|cid| h.contains(&format!("cid:{}", content_id(cid)))))
    };
    let (pictures, files): (Vec<&Attachment>, Vec<&Attachment>) = spec.attachments.iter().partition(|a| shown(a));
    let mut text = spec.text.clone();
    if !pictures.is_empty() && !draft {
        let tokens: BTreeSet<String> = pictures.iter().map(|a| a.cid.clone().unwrap_or_default()).collect();
        text = plain_pictures(&text, &tokens);
    }
    let first = text_part("plain", &text);
    let mut body_part: Part = first.clone();
    let mut alternative: Option<Vec<Part>> = None;
    if let Some(html) = html.filter(|h| !h.is_empty()) {
        let mut html_part = text_part("html", html);
        if !pictures.is_empty() {
            let mut related = vec![html_part];
            for att in &pictures {
                let (main, sub) = kind(att);
                if breaks_lines(&format!("{main}/{sub}")) {
                    return Err(HEADER_BREAK.to_owned());
                }
                let cid = format!("<{}>", content_id(att.cid.as_deref().unwrap_or_default()));
                if breaks_lines(&cid) {
                    return Err(HEADER_BREAK.to_owned());
                }
                let name = clean(Some(if att.filename.is_empty() { "picture" } else { &att.filename }));
                related.push(Part {
                    headers: vec![
                        ("Content-Type", Value::ContentType { main: main.clone(), sub: sub.clone(), params: vec![] }),
                        ("Content-Transfer-Encoding", Value::Encoding("base64")),
                        (
                            "Content-Disposition",
                            Value::Disposition {
                                kind: "inline".to_owned(),
                                params: vec![("filename".to_owned(), name)],
                            },
                        ),
                        ("Content-ID", Value::Text(cid)),
                        ("MIME-Version", Value::MimeVersion),
                    ],
                    body: file_body(&main, &sub, &att.data),
                });
            }
            html_part = Part {
                headers: vec![("MIME-Version", Value::MimeVersion), ("Content-Type", multipart("related"))],
                body: Body::Parts(related),
            };
        } else {
            html_part.headers.push(("MIME-Version", Value::MimeVersion));
        }
        alternative = Some(vec![first, html_part]);
    }
    let mut mixed: Vec<Part> = Vec::new();
    for att in &files {
        let (main, sub) = kind(att);
        if breaks_lines(&format!("{main}/{sub}")) {
            return Err(HEADER_BREAK.to_owned());
        }
        let name = clean(Some(if att.filename.is_empty() { "attachment" } else { &att.filename }));
        let disposition =
            Value::Disposition { kind: "attachment".to_owned(), params: vec![("filename".to_owned(), name)] };
        if main == "message" && sub == "rfc822" {
            mixed.push(Part {
                headers: vec![
                    ("Content-Type", Value::ContentType { main, sub, params: vec![] }),
                    ("Content-Transfer-Encoding", Value::Encoding("8bit")),
                    ("Content-Disposition", disposition),
                    ("MIME-Version", Value::MimeVersion),
                ],
                body: Body::Message(att.data.clone()),
            });
            continue;
        }
        if att.params.iter().any(|(k, v)| param_breaks(k, v)) {
            return Err(HEADER_BREAK.to_owned());
        }
        let body = file_body(&main, &sub, &att.data);
        let content_type = Value::ContentType { main, sub, params: with_params(&att.params) };
        let headers = if att.params.is_empty() {
            vec![
                ("Content-Type", content_type),
                ("Content-Transfer-Encoding", Value::Encoding("base64")),
                ("Content-Disposition", disposition),
                ("MIME-Version", Value::MimeVersion),
            ]
        } else {
            vec![
                ("Content-Transfer-Encoding", Value::Encoding("base64")),
                ("Content-Disposition", disposition),
                ("Content-Type", content_type),
                ("MIME-Version", Value::MimeVersion),
            ]
        };
        mixed.push(Part { headers, body });
    }

    let top_body = match (alternative, mixed.is_empty()) {
        (None, true) => {
            headers.append(&mut body_part.headers);
            headers.push(("MIME-Version", Value::MimeVersion));
            body_part.body
        }
        (Some(parts), true) => {
            headers.push(("MIME-Version", Value::MimeVersion));
            headers.push(("Content-Type", multipart("alternative")));
            Body::Parts(parts)
        }
        (alternative, false) => {
            let lead = match alternative {
                Some(parts) => {
                    Part { headers: vec![("Content-Type", multipart("alternative"))], body: Body::Parts(parts) }
                }
                None => body_part,
            };
            mixed.insert(0, lead);
            headers.push(("MIME-Version", Value::MimeVersion));
            headers.push(("Content-Type", multipart("mixed")));
            Body::Parts(mixed)
        }
    };
    let raw = mime::write(&Part { headers, body: top_body }, minting.draws)?;
    let head_end = raw.windows(4).position(|w| w == b"\r\n\r\n").unwrap_or(raw.len());
    if names_bcc(&raw[..head_end]) {
        return Err("Bcc must never appear in the transmitted/stored bytes".to_owned());
    }
    Ok(Built {
        raw,
        message_id: mid,
        envelope: each_once(to.iter().chain(&cc).chain(&bcc).map(|(_, a)| a.as_str())),
        to_disp: to.into_iter().map(|(_, a)| a).collect(),
        cc_disp: cc.into_iter().map(|(_, a)| a).collect(),
        bcc_disp: bcc.into_iter().map(|(_, a)| a).collect(),
    })
}

/// A multipart's Content-Type before its boundary, which the generator adds.
fn multipart(subtype: &str) -> Value {
    Value::ContentType { main: "multipart".to_owned(), sub: subtype.to_owned(), params: vec![] }
}

/// `_BCC_HEADER.search(head)`: a line of the headers starting `Bcc:`, in any case.
fn names_bcc(head: &[u8]) -> bool {
    head.split(|b| *b == b'\n').any(|line| {
        line.len() >= 3
            && line[..3].eq_ignore_ascii_case(b"bcc")
            && line[3..].iter().find(|b| **b != b' ' && **b != b'\t') == Some(&b':')
    })
}

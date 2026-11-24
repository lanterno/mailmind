//! Building an outgoing message (`mailmind.compose.domain.builder`), byte for byte as the Python app builds it with
//! `email.message.EmailMessage` and writes it with `as_bytes(policy=email.policy.SMTP)`: its headers in Python's order
//! and folded as Python folds them, its parts and their encodings, its MIME boundaries and its Message-ID drawn as
//! Python draws them. Every address is validated and IDNA-normalized first, and Bcc is never written into the bytes:
//! it rides only in the envelope. A send is refused at the first recipient that isn't an address, in words naming it
//! as it was typed. A draft keeps whatever was typed, as Mail does: what isn't an address goes on one line
//! (`one_line`) into a header of MailMind's own for To and for Cc (`TYPED_TO`, `TYPED_CC`), one a chip, which
//! reopening reads back after the addresses, and into nothing for Bcc, which a save records beside the bytes.
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

use mm_kernel::addresses::{MAX_RECIPIENTS, clean, parse_addr_list, validate_pair};
use mm_kernel::seams::Moment;
use mm_kernel::text::{is_space, strip};

use crate::markdown::{content_id, plain_pictures};
use crate::spec::{Attachment, Built, MessageSpec, Minting};
use mime::{Body, Part, Value};

/// Where a draft keeps what was typed in To and in Cc that isn't an address yet (`builder.TYPED`). Another app shows
/// none of it.
pub const TYPED_TO: &str = "X-MailMind-Draft-To";
pub const TYPED_CC: &str = "X-MailMind-Draft-Cc";
/// Characters of what was typed that a refusal names, past which it is cut short.
pub const SHOWN_MOST: usize = 80;
pub const NO_RECIPIENT: &str = "Add at least one recipient.";

/// `builder.TOO_MANY`.
fn too_many() -> String {
    format!("A message may have at most {MAX_RECIPIENTS} recipients.")
}

/// What `one_line` leaves of `addresses._DANGER`, the characters that show as nothing (`builder._HIDDEN`).
fn is_hidden(c: char) -> bool {
    matches!(c, '\u{200b}'..='\u{200f}' | '\u{202a}'..='\u{202e}' | '\u{2060}' | '\u{2066}'..='\u{2069}' | '\u{feff}')
}

/// `builder.one_line`: what was typed, on one line, as the Mac's `Addresses.oneLine` makes a name: each control
/// character and each line or paragraph separator a space, a run of spaces one, and the ends trimmed. Nothing left in
/// it can break a header.
pub fn one_line(typed: &str) -> String {
    let spaced: String =
        typed.chars().map(|c| if c.is_control() || matches!(c, '\u{2028}' | '\u{2029}') { ' ' } else { c }).collect();
    let joined = spaced.split(' ').filter(|p| !p.is_empty()).collect::<Vec<_>>().join(" ");
    strip(&joined).to_owned()
}

/// `builder.not_an_address`: what a send is told of what was typed in `field` that isn't an address: it named as
/// typed, on one line, cut short past `SHOWN_MOST` characters, a character that shows as nothing written as its
/// escape.
pub fn not_an_address(field: &str, typed: &str) -> String {
    let line = one_line(typed);
    let cut: String = if line.chars().count() <= SHOWN_MOST {
        line
    } else {
        line.chars().take(SHOWN_MOST - 1).chain(['\u{2026}']).collect()
    };
    let mut shown = String::new();
    for c in cut.chars() {
        if is_hidden(c) {
            shown.push_str(&format!("\\u{:04x}", u32::from(c)));
        } else {
            shown.push(c);
        }
    }
    format!("\u{201c}{shown}\u{201d} in {field} isn\u{2019}t a valid address.")
}

/// What `recipients` makes of one field: its addresses, each (clean display name, local@IDNA-domain), and what was
/// typed in it that isn't one, each on one line.
pub type Typed = (Vec<(String, String)>, Vec<String>);

/// `builder.recipients`: what was typed in `field`, a token for each chip: each address in it, once whatever its case,
/// and, for a draft, each chip that isn't one, on one line, each once. A send is refused at the first that isn't, in
/// words naming it. A chip with nothing on it is no recipient.
pub fn recipients(field: &str, tokens: &[String], draft: bool) -> Result<Typed, String> {
    let mut pairs: Vec<(String, String)> = Vec::new();
    let mut typed: Vec<String> = Vec::new();
    let (mut seen, mut seen_typed) = (HashSet::new(), HashSet::new());
    for token in tokens {
        let line = one_line(token);
        if line.is_empty() {
            continue;
        }
        let Some(found) = parse_addr_list(&[token.as_str()]) else {
            if !draft {
                return Err(not_an_address(field, token));
            }
            if seen_typed.insert(line.to_lowercase()) {
                typed.push(line);
            }
            continue;
        };
        for (name, spec) in found {
            if seen.insert(spec.to_lowercase()) {
                pairs.push((name, spec));
            }
        }
    }
    Ok((pairs, typed))
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
    date_of(minting.now)
}

/// The message with its Date header saying `now`, as `builder.restamped` makes it: a message goes out with the moment
/// it leaves, not the one it was written. Only the header block's own Date changes, folded lines and all; with none,
/// the bytes are as they were.
pub fn restamped(raw: &[u8], now: Moment) -> Vec<u8> {
    let head_ends = raw.windows(4).position(|w| w == b"\r\n\r\n").map_or(raw.len(), |at| at + 2);
    let line_end =
        |from: usize| raw[from..head_ends].windows(2).position(|w| w == b"\r\n").map_or(head_ends, |n| from + n + 2);
    let mut at = 0;
    while at < head_ends {
        let mut ends = line_end(at);
        if raw[at..ends].len() >= 5 && raw[at..at + 5].eq_ignore_ascii_case(b"Date:") {
            while ends < head_ends && matches!(raw[ends], b' ' | b'\t') {
                ends = line_end(ends);
            }
            let mut out = Vec::with_capacity(raw.len());
            out.extend_from_slice(&raw[..at]);
            out.extend_from_slice(format!("Date: {}\r\n", date_of(now)).as_bytes());
            out.extend_from_slice(&raw[ends..]);
            return out;
        }
        at = ends;
    }
    raw.to_vec()
}

/// `email.utils.format_datetime` of `now` in UTC.
fn date_of(now: Moment) -> String {
    let seconds = now.whole_seconds();
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
/// of the ValueError Python raises. `draft` lifts the rules a draft may be half-written past: it needs no recipient,
/// may have any number, and keeps what was typed that isn't an address in `TYPED_TO` and `TYPED_CC`, one header a
/// chip, where a send is refused it; and it keeps each picture's mark in the text.
pub fn build_message(spec: &MessageSpec, draft: bool, minting: Minting<'_>) -> Result<Built, String> {
    let (from_name, from_spec) =
        validate_pair(&spec.account_name, &spec.account).ok_or_else(|| not_an_address("From", &spec.account))?;
    let from_domain = from_spec.rsplit_once('@').map_or("", |(_, d)| d).to_owned();
    let (to, typed_to) = recipients("To", &spec.to, draft)?;
    let (cc, typed_cc) = recipients("Cc", &spec.cc, draft)?;
    // A draft's save records what was typed in Bcc.
    let (bcc, _) = recipients("Bcc", &spec.bcc, draft)?;
    if to.is_empty() && cc.is_empty() && bcc.is_empty() && !draft {
        return Err(NO_RECIPIENT.to_owned());
    }
    if to.len() + cc.len() + bcc.len() > MAX_RECIPIENTS && !draft {
        return Err(too_many());
    }

    let mut headers: Vec<(&'static str, Value)> = vec![("From", Value::Addresses(vec![(from_name, from_spec)]))];
    if !to.is_empty() {
        headers.push(("To", Value::Addresses(to.clone())));
    }
    if !cc.is_empty() {
        headers.push(("Cc", Value::Addresses(cc.clone())));
    }
    // On one line, so no break reaches the header.
    for (name, typed) in [(TYPED_TO, typed_to), (TYPED_CC, typed_cc)] {
        headers.extend(typed.into_iter().map(|line| (name, Value::Text(line))));
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

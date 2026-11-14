//! A message of a few headers over a plain text (`email.message.EmailMessage()`, each header set by its name and
//! the text given with `set_content(text)`), written as `as_bytes(policy=email.policy.SMTP)` writes it: what the
//! export writes for a copy whose whole message was never fetched (`export._synth`).

use super::fold;
use super::mime::{self, Body, Part, Value};
use crate::spec::Draws;

/// No multipart is written here, so nothing is ever drawn.
#[derive(Debug)]
struct NoDraws;

impl Draws for NoDraws {
    fn number(&self) -> u64 {
        0
    }
}

/// A header of a plain message, as it is set.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Header {
    /// `msg[name] = value`: text, which parses as the class Python's header registry gives the name.
    Named(&'static str, String),
    /// `msg[name] = Address(display_name, addr_spec=addr)`: a person, by name and address, the name quoted when it
    /// holds what would otherwise end it, so it names one person.
    Person(&'static str, String, String),
}

/// What Python's registry makes of `value` for a header of this name: an address list for those that name people,
/// one message id, a list of them for the threading headers, and unstructured text for the rest. A list here is
/// read as mailboxes only: a group, which only a display name with a bare colon writes, isn't read as one. A date
/// is text by the time it is here: Python reads one and writes it again as `format_datetime` wrote it, so a value
/// written that way goes through as it is.
fn registered(name: &str, value: &str) -> Value {
    match name.to_ascii_lowercase().as_str() {
        "from" | "to" | "cc" | "bcc" | "reply-to" | "sender" | "resent-from" | "resent-to" | "resent-cc"
        | "resent-bcc" | "resent-sender" => Value::AddressText(value.to_owned()),
        "message-id" => Value::MessageId(value.to_owned()),
        "in-reply-to" | "references" => Value::MessageIds(value.to_owned()),
        _ => Value::Text(value.to_owned()),
    }
}

/// `contentmanager._encode_text(text, 'utf-8', None, email.policy.default)`: the transfer encoding Python picks for
/// the text and the body it writes in it. Lines that fit go as they are, in 7bit when they are ASCII and 8bit when
/// not; longer ones as quoted-printable or base64, whichever the first ten lines come to less in.
fn encoded(text: &str) -> (&'static str, String) {
    // `bytes.splitlines`: a line ends at CR, LF or CRLF, and the last one's end is nothing to split on.
    let bytes = text.as_bytes();
    let mut lines: Vec<&[u8]> = Vec::new();
    let (mut at, mut start) = (0, 0);
    while at < bytes.len() {
        match bytes[at] {
            b'\r' | b'\n' => {
                lines.push(&bytes[start..at]);
                if bytes[at] == b'\r' && bytes.get(at + 1) == Some(&b'\n') {
                    at += 1;
                }
                at += 1;
                start = at;
            }
            _ => at += 1,
        }
    }
    if start < bytes.len() {
        lines.push(&bytes[start..]);
    }
    // The default policy's line end is `\n`, so the body as written and as embedded are the same bytes.
    let body = |lines: &[&[u8]]| -> Vec<u8> {
        let mut out = lines.join(&b'\n');
        out.push(b'\n');
        out
    };
    let as_text = |bytes: Vec<u8>| String::from_utf8(bytes).unwrap_or_default();
    if lines.iter().map(|l| l.len()).max().unwrap_or(0) <= fold::MAX_LINE {
        let whole = body(&lines);
        return (if whole.is_ascii() { "7bit" } else { "8bit" }, as_text(whole));
    }
    let sniff = body(&lines[..lines.len().min(10)]);
    let sniff_qp = mime::body_encode(&sniff, fold::MAX_LINE);
    // `binascii.b2a_base64`: the sniff on one line, with its line end.
    let sniff_base64 = sniff.len().div_ceil(3) * 4 + 1;
    if sniff_qp.len() > sniff_base64 {
        return ("base64", mime::base64_lines(&body(&lines)));
    }
    if lines.len() <= 10 {
        return ("quoted-printable", sniff_qp);
    }
    ("quoted-printable", mime::body_encode(&body(&lines), fold::MAX_LINE))
}

/// The message's bytes: each of `headers` in its order, then the text's Content-Type, Content-Transfer-Encoding
/// and MIME-Version, as `set_content(text)` adds them, and the text. A header Python would refuse to set or to write
/// is the words of what it raised: text that breaks a line, above all.
pub fn plain_message(headers: &[Header], text: &str) -> Result<Vec<u8>, String> {
    let (cte, body) = encoded(text);
    let mut all: Vec<(&'static str, Value)> = Vec::with_capacity(headers.len() + 3);
    for header in headers {
        all.push(match header {
            Header::Named(name, value) if super::breaks_lines(value) => return Err(super::HEADER_BREAK.to_owned()),
            Header::Named(name, value) => (*name, registered(name, value)),
            Header::Person(name, display, addr) => (*name, Value::Addresses(vec![(display.clone(), addr.clone())])),
        });
    }
    all.push((
        "Content-Type",
        Value::ContentType {
            main: "text".to_owned(),
            sub: "plain".to_owned(),
            params: vec![("charset".to_owned(), "utf-8".to_owned())],
        },
    ));
    all.push(("Content-Transfer-Encoding", Value::Encoding(cte)));
    all.push(("MIME-Version", Value::MimeVersion));
    mime::write(&Part { headers: all, body: Body::Lines(body) }, &NoDraws)
}

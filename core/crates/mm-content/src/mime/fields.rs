//! What a sync stores of a message from its header fields (`mailbox.application.sync`'s reading of them, with
//! `content.mime`'s `dec`, `sender_of`, `recipient_addrs` and `list_unsub`, and `mailbox.domain.ingested`), and the
//! text a list shows of its body (`content.text.text_body`).
//!
//! The fields are read as Python's `email` read them under compat32: the first of a header given twice, its value as
//! it came, folds and all, until something decodes it.

use mail_parser::HeaderForm;
use regex::Regex;
use std::sync::LazyLock;

use super::date;

/// Of the To and Cc text kept for search: a long list, not a forged one's whole length (`RECIPIENTS_CHARS`).
const RECIPIENTS_CHARS: usize = 20_000;
/// What `list_unsub` keeps of the headers it read (`_UNSUB_RAW_MAX`).
const UNSUB_RAW_MAX: usize = 4000;
/// Of a body's text, what a sync keeps (`BODY_CHARS`).
const BODY_CHARS: usize = 3000;

/// What a sync stores of one message's header fields.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Fields {
    pub from_name: String,
    /// Lower-case.
    pub from_addr: String,
    pub subject: String,
    /// `datetime.isoformat()` of the Date, a zone left out read as UTC; None when it doesn't read.
    pub date_iso: Option<String>,
    /// Its Unix time, or 0.
    pub ts: i64,
    pub message_id: String,
    pub in_reply_to: String,
    /// References on one line.
    pub refs: String,
    /// Whether it says List-Unsubscribe or List-ID: the classifier's sign of a list.
    pub has_list_unsub: bool,
    /// What `list_unsub_stored` reads the way out back from.
    pub unsub_raw: String,
    /// `one-click`, `mailto`, `http-page` or `none`.
    pub unsub_method: &'static str,
    /// To and Cc, bare and lower-case, each once, in order.
    pub recipient_addrs: Vec<String>,
    /// To and Cc as the text search reads.
    pub recipients: String,
}

/// The header block as compat32 read it: each field's name lower-case, and its value after the colon with leading
/// blanks gone and its folds kept.
fn raw_fields(raw: &[u8]) -> Vec<(String, String)> {
    let text = String::from_utf8_lossy(raw);
    let mut out: Vec<(String, String)> = Vec::new();
    for line in text.split_inclusive('\n') {
        if line.trim_end_matches(['\r', '\n']).is_empty() {
            break;
        }
        if line.starts_with([' ', '\t']) {
            if let Some((_, value)) = out.last_mut() {
                value.push_str(line);
            }
            continue;
        }
        if let Some((name, value)) = line.split_once(':') {
            out.push((name.trim_end().to_ascii_lowercase(), value.trim_start_matches([' ', '\t']).to_owned()));
        }
    }
    for (_, value) in &mut out {
        let kept = value.trim_end_matches(['\r', '\n']).len();
        value.truncate(kept);
    }
    out
}

fn first<'f>(fields: &'f [(String, String)], name: &str) -> Option<&'f str> {
    fields.iter().find(|(n, _)| n == name).map(|(_, v)| v.as_str())
}

fn all<'f>(fields: &'f [(String, String)], name: &'f str) -> impl Iterator<Item = &'f str> {
    fields.iter().filter(move |(n, _)| n == name).map(|(_, v)| v.as_str())
}

static FOLD: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\r?\n[ \t]+").expect("a pattern written in this crate compiles"));

/// A value as one line: each fold a single space (`_unfold`).
fn unfold(value: &str) -> String {
    FOLD.replace_all(value, " ").into_owned()
}

/// Python's `str.strip()`.
fn strip(text: &str) -> &str {
    text.trim_matches(|c: char| c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c))
}

/// A header's text with its encoded words decoded, line breaks as spaces (`dec`).
fn dec(value: Option<&str>) -> String {
    let Some(value) = value.filter(|v| !v.is_empty()) else { return String::new() };
    let line = unfold(value);
    let text = if line.contains("=?") {
        let parsed = HeaderForm::Text.parse(line.as_bytes());
        parsed.value().as_text().map_or_else(|| line.clone(), str::to_owned)
    } else {
        line
    };
    strip(&text.replace(['\r', '\n'], " ")).to_owned()
}

/// The mailboxes a header's value names, as (name, address), groups' members among them.
fn addresses(value: &str) -> Vec<(String, String)> {
    let line = unfold(value);
    let parsed = HeaderForm::Addresses.parse(line.as_bytes());
    let value = parsed.value();
    let Some(list) = value.as_address() else { return Vec::new() };
    list.mailboxes()
        .map(|m| (m.name().unwrap_or_default().to_owned(), m.address().unwrap_or_default().to_owned()))
        .collect()
}

static URI: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"<([^>]+)>").expect("a pattern written in this crate compiles"));

/// The way out of a list the headers offer (`list_unsub`): its method, and the text it is read back from.
fn list_unsub(lu: &str, post: &str) -> (&'static str, String) {
    let lu = strip(lu);
    let lup: String = post.chars().filter(|c| !c.is_whitespace()).collect::<String>().to_lowercase();
    let uris: Vec<String> =
        URI.captures_iter(lu).map(|c| c[1].chars().filter(|ch| !ch.is_whitespace()).collect()).collect();
    let https = uris.iter().any(|u| u.to_lowercase().starts_with("https://"));
    let mailto = uris.iter().any(|u| u.to_lowercase().starts_with("mailto:"));
    let method = if https && lup == "list-unsubscribe=one-click" {
        "one-click"
    } else if mailto {
        "mailto"
    } else if https {
        "http-page"
    } else {
        "none"
    };
    let mut raw = lu.to_owned();
    if !lup.is_empty() {
        raw.push_str(" | POST:");
        raw.push_str(&lup);
    }
    (method, raw.chars().take(UNSUB_RAW_MAX).collect())
}

/// The fields a sync stores, from a header fetch's bytes.
pub fn fields(raw: &[u8]) -> Fields {
    let fields = raw_fields(raw);
    let get = |name: &str| first(&fields, name);
    let (name, addr) =
        get("from").map(|from| addresses(from).into_iter().next().unwrap_or_default()).unwrap_or_default();
    let (date_iso, ts) = date::dated(get("date").unwrap_or_default()).map_or((None, 0), |(iso, ts)| (Some(iso), ts));
    let (unsub_method, unsub_raw) = list_unsub(&dec(get("list-unsubscribe")), &dec(get("list-unsubscribe-post")));
    let mut recipient_addrs: Vec<String> = ["to", "cc"]
        .into_iter()
        .flat_map(|field| all(&fields, field).flat_map(addresses).collect::<Vec<_>>())
        .map(|(_, a)| a.trim_matches(['.', '\'']).to_lowercase())
        .filter(|a| a.contains('@'))
        .collect();
    recipient_addrs.sort();
    recipient_addrs.dedup();
    let recipients = format!("{} {}", dec(get("to")), dec(get("cc")));
    Fields {
        from_name: strip(&name.replace(['\r', '\n'], " ")).to_owned(),
        from_addr: addr.to_lowercase(),
        subject: dec(get("subject")),
        date_iso,
        ts,
        message_id: strip(get("message-id").unwrap_or_default()).to_owned(),
        in_reply_to: strip(get("in-reply-to").unwrap_or_default()).to_owned(),
        refs: strip(&get("references").unwrap_or_default().replace('\n', " ")).to_owned(),
        has_list_unsub: get("list-unsubscribe").is_some_and(|v| !v.is_empty())
            || get("list-id").is_some_and(|v| !v.is_empty()),
        unsub_raw,
        unsub_method,
        recipient_addrs,
        recipients: strip(&recipients).chars().take(RECIPIENTS_CHARS).collect(),
    }
}

static NONCONTENT: LazyLock<Vec<Regex>> = LazyLock::new(|| {
    ["script", "style", "head", "title", "noscript"]
        .iter()
        .map(|tag| {
            Regex::new(&format!(r"(?is)<{tag}\b[^>]*>.*?</{tag}\s*>"))
                .expect("a pattern written in this crate compiles")
        })
        .collect()
});
pub(super) static COMMENT: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?is)<!--.*?-->").expect("a pattern written in this crate compiles"));
pub(super) static TAG: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"<[^>]+>").expect("a pattern written in this crate compiles"));
static ENTITY: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"&(#[0-9]+|#[xX][0-9a-fA-F]+|[A-Za-z]+);").expect("a pattern written in this crate compiles")
});
static BLANKS: LazyLock<Regex> =
    LazyLock::new(|| Regex::new("[ \t\u{a0}]+").expect("a pattern written in this crate compiles"));
static PARAGRAPHS: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\n\s*\n+").expect("a pattern written in this crate compiles"));

/// The common named entities and every numbered one; any other stays as written.
pub(super) fn unescape(text: &str) -> String {
    ENTITY
        .replace_all(text, |c: &regex::Captures<'_>| {
            let name = &c[1];
            let found = if let Some(hex) = name.strip_prefix("#x").or_else(|| name.strip_prefix("#X")) {
                u32::from_str_radix(hex, 16).ok().and_then(char::from_u32)
            } else if let Some(decimal) = name.strip_prefix('#') {
                decimal.parse().ok().and_then(char::from_u32)
            } else {
                match name {
                    "amp" => Some('&'),
                    "lt" => Some('<'),
                    "gt" => Some('>'),
                    "quot" => Some('"'),
                    "apos" => Some('\''),
                    "nbsp" => Some('\u{a0}'),
                    "zwnj" => Some('\u{200c}'),
                    "zwj" => Some('\u{200d}'),
                    "shy" => Some('\u{ad}'),
                    "hellip" => Some('\u{2026}'),
                    "mdash" => Some('\u{2014}'),
                    "ndash" => Some('\u{2013}'),
                    "rsquo" => Some('\u{2019}'),
                    "lsquo" => Some('\u{2018}'),
                    "rdquo" => Some('\u{201d}'),
                    "ldquo" => Some('\u{201c}'),
                    "copy" => Some('\u{a9}'),
                    "reg" => Some('\u{ae}'),
                    _ => None,
                }
            };
            found.map_or_else(|| c[0].to_owned(), String::from)
        })
        .into_owned()
}

/// Invisible preheader padding: zero-width spaces and joiners, the word joiner, the BOM, the combining grapheme
/// joiner, the soft hyphen and the bidi marks (`INVISIBLE`).
pub(super) fn visible(c: char) -> bool {
    !matches!(
        c,
        '\u{200b}' | '\u{200c}' | '\u{200d}' | '\u{2060}' | '\u{feff}' | '\u{34f}' | '\u{ad}' | '\u{200e}' | '\u{200f}'
    )
}

/// The text a list shows of a message and the AI reads (`text_body`): its first plain text, or its HTML's text,
/// with invisible padding gone, blanks and blank lines collapsed, at most `BODY_CHARS`.
pub fn text_body(raw: &[u8]) -> String {
    let parsed = super::parse(raw);
    let body = match (parsed.text, parsed.html) {
        (Some(text), _) => text.into_owned(),
        (None, Some(html)) => {
            let mut h = html.into_owned();
            for block in NONCONTENT.iter() {
                h = block.replace_all(&h, " ").into_owned();
            }
            let h = COMMENT.replace_all(&h, " ");
            unescape(&TAG.replace_all(&h, " "))
        }
        (None, None) => String::new(),
    };
    let body: String = body.replace("\r\n", "\n").replace('\r', "\n").chars().filter(|c| visible(*c)).collect();
    let body = BLANKS.replace_all(&body, " ");
    let body = PARAGRAPHS.replace_all(strip(&body), "\n\n");
    body.chars().take(BODY_CHARS).collect()
}

/// Whether a message carries a calendar invite: a text/calendar or application/ics part, or an .ics file.
pub fn has_invite(raw: &[u8]) -> bool {
    super::parse(raw).attachments.iter().any(|a| {
        a.content_type == "text/calendar"
            || a.content_type == "application/ics"
            || a.filename.to_lowercase().ends_with(".ics")
    })
}

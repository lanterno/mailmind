//! A reply's body and a forward's: what you typed, then the original, quoted under who wrote it or whole under a
//! banner (`compose.domain.reply` and `compose.domain.forward`). An HTML original goes through the sanitizer's
//! allowlist first, so a hostile message can't put active content into what you send.

use std::borrow::Cow;
use std::sync::LazyLock;

use mm_content::sanitize::html::sanitize;
use mm_kernel::text::strip;
use regex::{Captures, Regex};

use crate::quote::{BLOCKQUOTE, html_escape, own_html};

/// The line a forward's banner opens with.
const FORWARDED: &str = "---------- Forwarded message ----------";

/// The message being answered or forwarded, as far as it is known (`reply.Original`). An empty field is as unknown
/// as a missing one.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Original {
    pub from_name: Option<String>,
    pub from_addr: Option<String>,
    pub subject: Option<String>,
    pub date: Option<String>,
    pub to: Option<String>,
    pub cc: Option<String>,
    pub text: Option<String>,
    pub html: Option<String>,
}

/// A field's value, None where it is missing or empty: Python's `orig.get(...) or ...`.
fn given(field: &Option<String>) -> Option<&str> {
    field.as_deref().filter(|value| !value.is_empty())
}

fn pattern(source: &str) -> Regex {
    Regex::new(source).expect("a pattern written in this crate compiles")
}

/// `(?is)<br\s*/?>`, Python's `\s` being `str.isspace`.
static BREAK: LazyLock<Regex> = LazyLock::new(|| pattern(r"(?i)<br[\s\x1C-\x1F]*/?>"));
/// `(?is)</p\s*>`.
static PARAGRAPH_END: LazyLock<Regex> = LazyLock::new(|| pattern(r"(?i)</p[\s\x1C-\x1F]*>"));
/// `(?s)<[^>]+>`.
static TAG: LazyLock<Regex> = LazyLock::new(|| pattern(r"<[^>]+>"));
/// The character references Python's `html.unescape` reads (`html._charref`).
static CHARREF: LazyLock<Regex> =
    LazyLock::new(|| pattern(r"&(#[0-9]+;?|#[xX][0-9a-fA-F]+;?|[^\t\n\x0C <&#;]{1,32};?)"));

/// The text of an original that has no text/plain part, from its HTML (`reply.plain_from_html`): breaks and
/// paragraphs as line ends, every tag gone, its character references read.
pub fn plain_from_html(html: &str) -> String {
    let safe = sanitize(html, false, false).html;
    let text = BREAK.replace_all(&safe, "\n");
    let text = PARAGRAPH_END.replace_all(&text, "\n\n");
    let text = TAG.replace_all(&text, "");
    strip(&unescape(&text)).to_owned()
}

/// Python's `html.unescape` of what the sanitizer writes. Python knows every HTML5 name; the sanitizer writes only
/// `&amp;`, `&lt;` and `&gt;` in text, and `&quot;` and `&#x27;` besides in an attribute's value, so those names, and
/// every numeric reference as Python reads it, are what is read here. Any other name stays as written. The one place
/// another could stand is a `<style>` sheet, whose CSS the sanitizer writes as it kept it: where that holds one,
/// Python reads it and this doesn't.
fn unescape(text: &str) -> Cow<'_, str> {
    if !text.contains('&') {
        return Cow::Borrowed(text);
    }
    CHARREF.replace_all(text, |c: &Captures<'_>| reference(&c[1]))
}

/// `html._replace_charref`: what a reference reads as, written without its `&`.
fn reference(name: &str) -> String {
    let Some(number) = name.strip_prefix('#') else {
        return match name {
            "amp;" => "&".to_owned(),
            "lt;" => "<".to_owned(),
            "gt;" => ">".to_owned(),
            "quot;" => "\"".to_owned(),
            _ => format!("&{name}"),
        };
    };
    let number = number.trim_end_matches(';');
    // A number too long for a u32 is past U+10FFFF, as Python finds.
    let value = match number.strip_prefix(['x', 'X']) {
        Some(hex) => u32::from_str_radix(hex, 16).ok(),
        None => number.parse().ok(),
    };
    numbered(value.unwrap_or(u32::MAX))
}

/// `html._invalid_charrefs` from U+0080: what windows-1252 has there.
const WINDOWS_1252: [char; 32] = [
    '\u{20ac}', '\u{81}', '\u{201a}', '\u{192}', '\u{201e}', '\u{2026}', '\u{2020}', '\u{2021}', '\u{2c6}', '\u{2030}',
    '\u{160}', '\u{2039}', '\u{152}', '\u{8d}', '\u{17d}', '\u{8f}', '\u{90}', '\u{2018}', '\u{2019}', '\u{201c}',
    '\u{201d}', '\u{2022}', '\u{2013}', '\u{2014}', '\u{2dc}', '\u{2122}', '\u{161}', '\u{203a}', '\u{153}', '\u{9d}',
    '\u{17e}', '\u{178}',
];

/// What a numeric reference reads as, as the HTML standard and Python's `html.unescape` read it: a few numbers as
/// windows-1252 has them, a surrogate or a number past Unicode as U+FFFD, and a control or noncharacter as nothing.
fn numbered(value: u32) -> String {
    let c = match value {
        0 => '\u{fffd}',
        0x0d => '\r',
        0x80..=0x9f => WINDOWS_1252[(value - 0x80) as usize],
        0xd800..=0xdfff | 0x11_0000.. => '\u{fffd}',
        0x01..=0x08 | 0x0b | 0x0e..=0x1f | 0x7f | 0xfdd0..=0xfdef => return String::new(),
        _ if value & 0xfffe == 0xfffe => return String::new(),
        _ => char::from_u32(value).unwrap_or('\u{fffd}'),
    };
    c.to_string()
}

/// `str.splitlines()`: the lines of `text` without their ends, which are any of Python's line boundaries. A last line
/// end makes no empty line after it.
fn splitlines(text: &str) -> Vec<&str> {
    let mut lines = Vec::new();
    let mut start = 0;
    let mut chars = text.char_indices().peekable();
    while let Some((at, c)) = chars.next() {
        if matches!(c, '\n' | '\r' | '\x0b' | '\x0c' | '\x1c' | '\x1d' | '\x1e' | '\u{85}' | '\u{2028}' | '\u{2029}') {
            lines.push(&text[start..at]);
            start = at + c.len_utf8();
            if c == '\r' && chars.next_if(|(_, next)| *next == '\n').is_some() {
                start += 1;
            }
        }
    }
    if start < text.len() {
        lines.push(&text[start..]);
    }
    lines
}

/// `reply._attribution`: "On (date), (who) <(address)> wrote:".
fn attribution(orig: &Original) -> String {
    let who = given(&orig.from_name).or(given(&orig.from_addr)).unwrap_or("someone");
    let addr = given(&orig.from_addr).unwrap_or("");
    let date = given(&orig.date).unwrap_or("");
    let tail = if !addr.is_empty() && addr != who { format!(" <{addr}>") } else { String::new() };
    strip(&format!("On {date}, {who}{tail} wrote:")).to_owned()
}

/// `reply._quote_plain`: each line of `text` after `> `, or `>` alone for no text.
fn quote_plain(text: &str) -> String {
    let quoted = splitlines(text).iter().map(|line| format!("> {line}")).collect::<Vec<_>>().join("\n");
    if quoted.is_empty() { ">".to_owned() } else { quoted }
}

/// The original's text: its own, or what its HTML reads as.
fn original_text(orig: &Original) -> String {
    match (given(&orig.text), given(&orig.html)) {
        (Some(text), _) => text.to_owned(),
        (None, Some(html)) => plain_from_html(html),
        (None, None) => String::new(),
    }
}

/// A reply's text and its HTML, which there is only for an original with HTML (`reply.reply_bodies`): what you typed,
/// who wrote the original and when, and the original quoted.
pub fn reply_bodies(new_text: &str, orig: &Original) -> (String, Option<String>) {
    let attr = attribution(orig);
    let text = format!("{new_text}\n\n{attr}\n{}\n", quote_plain(&original_text(orig)));
    let html = given(&orig.html).map(|html| {
        let safe = sanitize(html, false, false).html;
        format!("{}<div>{}</div>{BLOCKQUOTE}{safe}</blockquote>", own_html(new_text), html_escape(&attr))
    });
    (text, html)
}

/// A forward's text and its HTML, which there is only for an original with HTML (`forward.forward_bodies`): what you
/// typed, a banner of the original's headers, and the original whole.
pub fn forward_bodies(new_text: &str, orig: &Original) -> (String, Option<String>) {
    let from = format!("{} <{}>", given(&orig.from_name).unwrap_or(""), given(&orig.from_addr).unwrap_or(""));
    let mut rows = vec![
        ("From", strip(&from).to_owned()),
        ("Date", given(&orig.date).unwrap_or("").to_owned()),
        ("Subject", given(&orig.subject).unwrap_or("").to_owned()),
        ("To", given(&orig.to).unwrap_or("").to_owned()),
    ];
    if let Some(cc) = given(&orig.cc) {
        rows.push(("Cc", cc.to_owned()));
    }
    let shown: Vec<String> =
        rows.iter().filter(|(_, value)| !value.is_empty()).map(|(name, value)| format!("{name}: {value}")).collect();
    let text = format!("{new_text}\n\n{FORWARDED}\n{}\n\n{}", shown.join("\n"), original_text(orig));
    let html = given(&orig.html).map(|html| {
        let safe = sanitize(html, false, false).html;
        let banner = shown.iter().map(|row| html_escape(row)).collect::<Vec<_>>().join("<br>");
        format!("{}<div>{FORWARDED}<br>{banner}</div><hr>{safe}", own_html(new_text))
    });
    (text, html)
}

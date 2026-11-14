//! What search indexes of a message once its body is fetched, and the snippet a list shows of it where its sync kept
//! none (`content.text.body_text_of`, `strip_jsonld` and `snippet_of`).
//!
//! Entities are undone as `text_body` undoes them: the common named ones and every numbered one.

use std::sync::LazyLock;

use regex::Regex;

use super::Parsed;
use super::fields::{COMMENT, TAG, unescape, visible};

/// Of one body's text, what is indexed (`_FTS_CAP`).
const FTS_CAP: usize = 100_000;
/// Of a snippet, the characters kept.
const SNIPPET_CHARS: usize = 280;
/// How many JSON-LD blocks one text loses at most.
const JSONLD_BLOCKS: usize = 12;

/// The elements whose contents are no text: an opening tag, its name captured (`_NOTEXT`'s first half).
static NOTEXT: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?is)<(script|style|head|title)\b[^>]*>").expect("a pattern written in this crate compiles")
});
/// The end of each, in `NOTEXT`'s names' order, any case.
static NOTEXT_END: LazyLock<[Regex; 4]> = LazyLock::new(|| {
    ["script", "style", "head", "title"]
        .map(|name| Regex::new(&format!("(?i)</{name}>")).expect("a pattern written in this crate compiles"))
});

/// `_NOTEXT.sub(" ", html)`: each element that holds no text, its contents and its end tag, as one space. An element
/// that is never closed stays, and the search goes on from the character after its `<`, as the pattern's would.
fn without_notext(html: &str) -> String {
    let mut out = String::with_capacity(html.len());
    let (mut kept, mut at) = (0, 0);
    while let Some(open) = NOTEXT.captures_at(html, at) {
        let (Some(whole), Some(name)) = (open.get(0), open.get(1)) else { break };
        let ends = match name.as_str().to_ascii_lowercase().as_str() {
            "script" => &NOTEXT_END[0],
            "style" => &NOTEXT_END[1],
            "head" => &NOTEXT_END[2],
            _ => &NOTEXT_END[3],
        };
        match ends.find_at(html, whole.end()) {
            Some(end) => {
                out.push_str(&html[kept..whole.start()]);
                out.push(' ');
                kept = end.end();
                at = kept;
            }
            // `<` is one byte: the next character starts after it.
            None => at = whole.start() + 1,
        }
    }
    out.push_str(&html[kept..]);
    out
}

/// `strip_jsonld`: each JSON-LD block a sender put in its text (a balanced `[..]` or `{..}` holding an `@context`),
/// with the openers that wrap it, as one space, so what the text says reads as prose.
fn strip_jsonld(text: &str) -> String {
    let mut out = text.to_owned();
    let mut guard = 0;
    while guard < JSONLD_BLOCKS {
        let Some(at) = out.find("@context") else { break };
        guard += 1;
        // Every byte looked at here is ASCII, so each index found is a character's.
        let bytes = out.as_bytes();
        let Some(mut start) = bytes[..at].iter().rposition(|b| matches!(b, b'[' | b'{')) else { break };
        let mut j = start;
        while j > 0 && matches!(bytes[j - 1], b'[' | b'{' | b' ' | b'\t' | b'\r' | b'\n') {
            j -= 1;
            if matches!(bytes[j], b'[' | b'{') {
                start = j;
            }
        }
        let mut depth = 0i64;
        let mut end = None;
        for (i, b) in bytes.iter().enumerate().skip(start) {
            match b {
                b'[' | b'{' => depth += 1,
                b']' | b'}' => {
                    depth -= 1;
                    if depth == 0 {
                        end = Some(i);
                        break;
                    }
                }
                _ => {}
            }
        }
        let Some(end) = end else { break };
        out = format!("{} {}", &out[..start], &out[end + 1..]);
    }
    out
}

/// A message's text for search (`body_text_of`): its plain text, or else its HTML's text, with what holds no text
/// and its tags gone and its entities undone; JSON-LD blocks left out, and at most `FTS_CAP` characters.
pub fn body_text_of(parsed: &Parsed<'_>) -> String {
    let text = parsed.text.as_deref().filter(|t| !t.is_empty());
    let text = match (text, parsed.html.as_deref().filter(|h| !h.is_empty())) {
        (None, Some(html)) => {
            let html = COMMENT.replace_all(html, " ");
            let html = without_notext(&html);
            unescape(&TAG.replace_all(&html, " "))
        }
        (text, _) => text.unwrap_or_default().to_owned(),
    };
    strip_jsonld(&text).chars().take(FTS_CAP).collect()
}

/// A clean one-paragraph preview for a list (`snippet_of`): entities undone, invisible padding gone, every run of
/// whitespace one space, at most `SNIPPET_CHARS` characters.
pub fn snippet_of(body_text: &str) -> String {
    let text: String = unescape(body_text).chars().filter(|c| visible(*c)).collect();
    let words: Vec<&str> = text.split(is_space).filter(|w| !w.is_empty()).collect();
    words.join(" ").chars().take(SNIPPET_CHARS).collect()
}

/// `str.isspace()`: Unicode's White_Space, and the four ASCII separators Python counts too.
fn is_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

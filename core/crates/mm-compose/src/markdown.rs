//! What a person typed in Markdown, as the HTML that goes out beside it (`compose.domain.markdown`). A subset that
//! reads well in mail: paragraphs, *emphasis* and **strong**, [links](https://example.test), lists, > quotes, `code`
//! and fences, # headings and a picture the message itself carries, ![its name](cid:its-id). A line break stays one,
//! as it does in a message. What it doesn't know stays as typed, escaped, so no HTML a person typed gets through, and
//! what comes out goes through the sanitizer all the same.
//!
//! `render` answers None for text with no Markdown in it, which goes out as plain text. A bare address is no Markdown,
//! and neither is a `>` line, which plain mail has always quoted with: each becomes a link or a quote only in a
//! message that has HTML anyway. Each span gives up at the first thing it can't close and bounds what it looks ahead
//! at, so the time this takes follows the length of the text.
//!
//! It answers as the Python app does, character for character. Its limits count characters, as Python's `len` does,
//! and what is a space, a letter or a digit is what `str.isspace`, `str.isalnum` and `re`'s `\s` and `\d` say:
//! Unicode's, with Python's own additions. Its HTML goes through the sanitizer the Python app calls, so the two send
//! the same.

use std::collections::BTreeSet;
use std::sync::LazyLock;

use mm_content::sanitize::html::sanitize;
use mm_kernel::text::{is_space, strip};
use regex::Regex;

use crate::quote::html_escape;

/// Past this many characters, text goes out as typed: no message is this much writing.
pub const MAX_TEXT: usize = 200_000;
/// Quotes and lists inside each other, past which a line is a paragraph.
const MAX_DEPTH: usize = 8;
/// Emphasis inside emphasis, past which it is text.
const MAX_SPAN_DEPTH: usize = 4;
/// How far, in characters, a link's `]` may stand from its `[`.
const MAX_LINK_TEXT: usize = 2000;
/// The most characters of an address read past its `https:` or `mailto:` in angle brackets, or past a bare one's
/// `https://`.
const MAX_URL: usize = 2048;
/// A Content-ID has an address's form.
const CID_SUFFIX: &str = "@mailmind";
/// What a bare address can't end in: the sentence's punctuation, and the marks of emphasis around it.
const TRAILING: &[char] = &['.', ',', ';', ':', '!', '?', '*', '_', '~'];

/// Python's `\s`, which is `str.isspace`: Unicode's White_Space and the four separators U+001C to U+001F.
macro_rules! space {
    () => {
        r"[\s\x1C-\x1F]"
    };
}

/// A character of an address in a link's target: no space, angle bracket or parenthesis.
macro_rules! url_char {
    () => {
        r"[^\s\x1C-\x1F<>()]"
    };
}

/// `https?:` as Python's `re.I` reads it, which takes `ſ` for `s`.
macro_rules! http {
    () => {
        r"[hH][tT][tT][pP][sS\x{17F}]?:"
    };
}

/// `mailto:` as Python's `re.I` reads it, which takes `İ` and `ı` for `i`.
macro_rules! mailto {
    () => {
        r"[mM][aA][iI\x{130}\x{131}][lL][tT][oO]:"
    };
}

fn pattern(source: &str) -> Regex {
    Regex::new(source).expect("a pattern written in this crate compiles")
}

/// `_HASHES`: a heading's hashes.
static HASHES: LazyLock<Regex> = LazyLock::new(|| pattern(r"\A {0,3}(#{1,6})[ \t]+"));
/// `_QUOTE`: a quoted line's mark.
static QUOTE: LazyLock<Regex> = LazyLock::new(|| pattern(r"\A {0,3}> ?"));
/// `_ITEM` but for its look-ahead, which `item` makes: a list item's bullet or number, and the spaces after it.
static ITEM: LazyLock<Regex> = LazyLock::new(|| pattern(r"\A( {0,3})([-*+]|(\d{1,9})[.)]) +"));
/// `_SPECIAL`: where a span could start.
static SPECIAL: LazyLock<Regex> = LazyLock::new(|| pattern(concat!(r"[\\`*_\[!<\n]|", http!(), "//")));
/// `_PICTURE`: a picture's mark, its name and the token of the file it shows.
static PICTURE: LazyLock<Regex> = LazyLock::new(|| pattern(r"!\[([^\[\]\n]{0,300})\]\(cid:([A-Za-z0-9_-]{1,64})\)"));
/// `_PICTURE`, where it starts.
static PICTURE_AT: LazyLock<Regex> =
    LazyLock::new(|| pattern(r"\A!\[([^\[\]\n]{0,300})\]\(cid:([A-Za-z0-9_-]{1,64})\)"));
/// `_LINK_TARGET`: a link's `(target "title")`, the target an address with a scheme a link keeps, which may hold
/// parentheses in pairs.
static LINK_TARGET: LazyLock<Regex> = LazyLock::new(|| {
    pattern(concat!(
        r"\A\(",
        space!(),
        "*<?((?:",
        http!(),
        "|",
        mailto!(),
        ")",
        url_char!(),
        r"*(?:\(",
        url_char!(),
        r"*\)",
        url_char!(),
        "*)*)>?(?:",
        space!(),
        r#"+"[^"\n]*")?"#,
        space!(),
        r"*\)",
    ))
});
/// The scheme `_ANGLED` starts with.
static ANGLED_SCHEME: LazyLock<Regex> = LazyLock::new(|| pattern(concat!(r"\A(?:", http!(), "|", mailto!(), ")")));
/// The scheme `_BARE_URL` starts with.
static BARE_SCHEME: LazyLock<Regex> = LazyLock::new(|| pattern(concat!(r"\A", http!(), "//")));
/// `str.isalnum` of a character: a letter or a number, by Unicode's general category, which is what Python reads.
static ALNUM: LazyLock<Regex> = LazyLock::new(|| pattern(r"\A[\p{L}\p{N}]"));
/// Python's `\d`: a decimal digit, Unicode's Nd.
static DECIMAL: LazyLock<Regex> = LazyLock::new(|| pattern(r"\A\d\z"));

/// `TOKEN.fullmatch(s)`: whether `s` is what a picture's mark names it by, 1 to 64 ASCII letters, digits, `_` or `-`.
pub fn is_token(s: &str) -> bool {
    (1..=64).contains(&s.len()) && s.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
}

/// The Content-ID, without its angle brackets, of the picture a mark names by `token` (`markdown.content_id`).
pub fn content_id(token: &str) -> String {
    format!("{token}{CID_SUFFIX}")
}

/// The token of a Content-ID this renderer gave out, and None for anyone else's (`markdown.token_of`).
pub fn token_of(cid: Option<&str>) -> Option<String> {
    let token = cid?.strip_suffix(CID_SUFFIX)?;
    is_token(token).then(|| token.to_owned())
}

/// What a picture's mark reads as for someone reading the text alone: its name, as a mention of what they aren't
/// seeing (`markdown.plain_pictures`). A mark naming none of `shown` stays as typed.
pub fn plain_pictures(text: &str, shown: &BTreeSet<String>) -> String {
    PICTURE
        .replace_all(
            text,
            |c: &regex::Captures<'_>| {
                if shown.contains(&c[2]) { format!("[{}]", &c[1]) } else { c[0].to_owned() }
            },
        )
        .into_owned()
}

/// The sanitized HTML of what a person typed, or None where there is no Markdown in it (`markdown.render`).
/// `pictures` are the tokens of the files this message carries in its text: a mark naming any other stays as typed.
pub fn render(text: &str, pictures: &BTreeSet<String>) -> Option<String> {
    if text.is_empty() || (text.len() > MAX_TEXT && text.chars().count() > MAX_TEXT) {
        return None;
    }
    let lf = text.replace("\r\n", "\n").replace('\r', "\n");
    let lines: Vec<&str> = lf.split('\n').collect();
    let mut r = Render { formatted: false, pictures };
    let html = r.blocks(&lines, 0);
    // No image gets here but a mark's, so images load only where the message carries one.
    r.formatted.then(|| sanitize(&html, !pictures.is_empty(), false).html)
}

/// `html.escape(s, quote=False)`, written onto `out`.
fn push_text(out: &mut String, s: &str) {
    for c in s.chars() {
        match c {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            _ => out.push(c),
        }
    }
}

/// `markdown._text`: `html.escape(s, quote=False)`.
fn escape_text(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    push_text(&mut out, s);
    out
}

/// Whether `str.isalnum` holds of `c`.
fn is_alnum(c: char) -> bool {
    c.is_ascii_alphanumeric() || (!c.is_ascii() && ALNUM.is_match(c.encode_utf8(&mut [0; 4])))
}

/// The value `int` gives a decimal digit. Unicode gives its decimal digits in runs of ten, 0 to 9, one after another,
/// so a digit's value is how far it is into its run.
fn digit_value(c: char) -> u64 {
    if c.is_ascii_digit() {
        return u64::from(c as u32 - '0' as u32);
    }
    let is_decimal = |cp: u32| char::from_u32(cp).is_some_and(|d| DECIMAL.is_match(d.encode_utf8(&mut [0; 4])));
    let mut first = c as u32;
    while first > 0 && is_decimal(first - 1) {
        first -= 1;
    }
    u64::from((c as u32 - first) % 10)
}

/// `int(digits)` of a list item's number, up to nine decimal digits.
fn number(digits: &str) -> u64 {
    digits.chars().fold(0, |n, c| n * 10 + digit_value(c))
}

/// The character at byte `at` of `s`, if any.
fn char_at(s: &str, at: usize) -> Option<char> {
    s.get(at..)?.chars().next()
}

/// The character before byte `at` of `s`, if any.
fn char_before(s: &str, at: usize) -> Option<char> {
    s.get(..at)?.chars().next_back()
}

/// `str.rstrip()`.
fn rstrip(s: &str) -> &str {
    s.trim_end_matches(is_space)
}

/// `_FENCE.match(line)`: up to three spaces, a run of three or more backticks or tildes, and the rest of the line.
fn fence(line: &str) -> Option<(&str, &str)> {
    let rest = line.trim_start_matches(' ');
    if line.len() - rest.len() > 3 {
        return None;
    }
    let ch = *rest.as_bytes().first().filter(|ch| matches!(ch, b'`' | b'~'))?;
    let width = rest.bytes().take_while(|b| *b == ch).count();
    (width >= 3).then(|| rest.split_at(width))
}

/// `markdown._closing_fence`: the line that closes a fence opening on line `at`, or None. A fence that never closes
/// is no fence, so one stray run of backticks can't swallow the rest of a message.
fn closing_fence(lines: &[&str], at: usize) -> Option<usize> {
    let (run, info) = fence(lines[at])?;
    let ch = run.as_bytes()[0];
    if ch == b'`' && info.contains('`') {
        return None;
    }
    // A closing line holds the fence's character alone, so its length in characters is its length in bytes.
    (at + 1..lines.len()).find(|&j| {
        let t = strip(lines[j]);
        t.len() >= run.len() && t.bytes().all(|b| b == ch)
    })
}

/// `markdown._title`: a heading line's level and words.
fn heading(line: &str) -> Option<(usize, &str)> {
    let m = HASHES.captures(line)?;
    let level = m.get(1)?.len();
    let mut title = rstrip(&line[m.get(0)?.end()..]);
    let bare = title.trim_end_matches('#');
    if bare.len() != title.len() && (bare.is_empty() || bare.ends_with([' ', '\t'])) {
        // A closing run of hashes.
        title = rstrip(bare);
    }
    (!title.is_empty()).then_some((level, title))
}

/// `markdown._indent`: how many spaces a line starts with.
fn indent(line: &str) -> usize {
    line.len() - line.trim_start_matches(' ').len()
}

/// A line's list item mark, as `_ITEM` matches it.
#[derive(Debug, Clone, Copy)]
struct Item<'a> {
    /// The spaces before the mark (`m[1]`).
    indent: usize,
    /// Where the mark and the spaces after it end, in bytes.
    end: usize,
    /// The same, in characters (`m.end()`).
    width: usize,
    /// The number of a numbered item (`m[3]`).
    number: Option<&'a str>,
}

/// `_ITEM.match(line)`: a bullet, or a number and `.` or `)`, then spaces and something that isn't one.
fn item(line: &str) -> Option<Item<'_>> {
    let m = ITEM.captures(line)?;
    let end = m.get(0)?.end();
    // The pattern's spaces run as far as they go, and what follows them is no space: `(?=\S)`.
    if char_at(line, end).is_none_or(is_space) {
        return None;
    }
    let number = m.get(3).map(|d| d.as_str());
    Some(Item { indent: m.get(1)?.len(), end, width: line[..end].chars().count(), number })
}

/// `_Render._interrupts`: whether line `i` ends the paragraph above it. A numbered line does only if it starts at 1:
/// "1985. A vintage year" in the middle of a paragraph is a sentence.
fn interrupts(lines: &[&str], i: usize) -> bool {
    let line = lines[i];
    if QUOTE.is_match(line) || heading(line).is_some() || closing_fence(lines, i).is_some() {
        return true;
    }
    item(line).is_some_and(|item| item.number.is_none_or(|n| n == "1"))
}

/// `_Render._item_lines`: an item's lines, its first, then those indented under it, with the blank lines between.
fn item_lines<'a>(lines: &[&'a str], mut i: usize, need: usize, first: &'a str) -> (usize, Vec<&'a str>) {
    let (mut body, n) = (vec![first], lines.len());
    while i < n {
        if !strip(lines[i]).is_empty() {
            if indent(lines[i]) < need {
                break;
            }
            // Its first `need` characters are spaces.
            body.push(&lines[i][need..]);
            i += 1;
            continue;
        }
        let mut k = i;
        while k < n && strip(lines[k]).is_empty() {
            k += 1;
        }
        if k == n || indent(lines[k]) < need {
            break;
        }
        body.extend(std::iter::repeat_n("", k - i));
        i = k;
    }
    (i, body)
}

/// `markdown._opens`: whether the emphasis mark of `width` at `at` can open: something but a space follows it, and no
/// letter or digit comes before it.
fn opens(s: &str, at: usize, width: usize) -> bool {
    char_at(s, at + width).is_some_and(|after| !is_space(after)) && !char_before(s, at).is_some_and(is_alnum)
}

/// `markdown._closes`: whether the emphasis mark of `width` at `at` can close: no space comes before it, and no letter
/// or digit follows it.
fn closes(s: &str, at: usize, width: usize) -> bool {
    !char_before(s, at).is_some_and(is_space) && !char_at(s, at + width).is_some_and(is_alnum)
}

/// `markdown._closer`: where the emphasis `mark` opened before `start` closes. A run longer than the mark is taken from
/// its far end, so ***both*** comes apart, and a single mark goes past a double one.
fn closer(s: &str, start: usize, mark: &str) -> Option<usize> {
    let b = s.as_bytes();
    let (ch, width) = (mark.as_bytes()[0], mark.len());
    let mut at = start;
    while let Some(found) = s.get(at..).and_then(|rest| rest.find(mark)) {
        at += found;
        if width == 1 && b.get(at + 1) == Some(&ch) {
            while b.get(at) == Some(&ch) {
                at += 1;
            }
            continue;
        }
        if width == 2 {
            while b.get(at + 2) == Some(&ch) {
                at += 1;
            }
        }
        if at > start && closes(s, at, width) {
            return Some(at);
        }
        at += width;
    }
    None
}

/// The end of the run of backticks that starts at `at`.
fn backticks_end(s: &str, at: usize) -> usize {
    at + s[at..].bytes().take_while(|b| *b == b'`').count()
}

/// `_ANGLED.match(s, at)`: `<`, an address with a scheme a link keeps and up to `MAX_URL` characters past it with no
/// space or angle bracket, and `>`. Where the address ends, and the match.
fn angled(s: &str, at: usize) -> Option<(usize, usize)> {
    let start = at + 1 + ANGLED_SCHEME.find(&s[at + 1..])?.end();
    let end = start + run(&s[start..], |c| !matches!(c, '<' | '>'));
    (end > start && s[end..].starts_with('>')).then_some((end, end + 1))
}

/// `_BARE_URL.match(s, at)`: an address's scheme and up to `MAX_URL` characters past it with no space, angle bracket
/// or quote. Where it ends.
fn bare_url_end(s: &str, at: usize) -> Option<usize> {
    let start = at + BARE_SCHEME.find(&s[at..])?.end();
    let end = start + run(&s[start..], |c| !matches!(c, '<' | '>' | '"' | '\''));
    (end > start).then_some(end)
}

/// How many bytes of `s` the first `MAX_URL` characters at most run to that are no space and that `keep` holds of.
fn run(s: &str, keep: impl Fn(char) -> bool) -> usize {
    s.char_indices()
        .take(MAX_URL)
        .take_while(|(_, c)| !is_space(*c) && keep(*c))
        .last()
        .map_or(0, |(at, c)| at + c.len_utf8())
}

/// `_LINK_TARGET.match(s, at)`: a link's target, where its address is and where the match ends.
fn link_target(s: &str, at: usize) -> Option<(std::ops::Range<usize>, usize)> {
    let m = LINK_TARGET.captures(s.get(at..)?)?;
    let url = m.get(1)?.range();
    Some((url.start + at..url.end + at, at + m.get(0)?.end()))
}

/// `markdown._Render._link`.
fn link_html(url: &str, inner: &str) -> String {
    format!("<a href=\"{}\">{inner}</a>", html_escape(url))
}

/// A mark with nothing left to close it in the text a span reads (`dead` in `_Render.spans`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
enum Mark {
    /// `](`, which a link's words end with.
    Link,
    /// An emphasis mark, as written.
    Emphasis(&'static str),
    /// A run of backticks this long.
    Code(usize),
}

/// How many characters come before a byte of a text, counted as a span reads on: each count goes on from the last.
#[derive(Debug, Default)]
struct Counted {
    byte: usize,
    chars: usize,
}

impl Counted {
    fn to(&mut self, s: &str, byte: usize) -> usize {
        self.chars += s[self.byte..byte].chars().count();
        self.byte = byte;
        self.chars
    }
}

/// The next `](` ahead in a span's text, and the target after it (`link_end` and `link` in `_Render.spans`).
#[derive(Debug)]
struct LinkAhead {
    /// Where the `](` is, in bytes.
    end: usize,
    /// The same, in characters.
    end_chars: usize,
    /// The target after it, if it is one: where its address is, and where it ends.
    target: Option<(std::ops::Range<usize>, usize)>,
}

/// One text's rendering (`markdown._Render`). `formatted` says whether anything in it was Markdown.
#[derive(Debug)]
struct Render<'p> {
    formatted: bool,
    pictures: &'p BTreeSet<String>,
}

impl Render<'_> {
    /// `_Render.blocks`.
    fn blocks(&mut self, lines: &[&str], depth: usize) -> String {
        let mut out: Vec<String> = Vec::new();
        let (mut i, n) = (0, lines.len());
        while i < n {
            let line = lines[i];
            if strip(line).is_empty() {
                i += 1;
            } else if let Some(end) = closing_fence(lines, i) {
                self.formatted = true;
                out.push(format!("<pre><code>{}</code></pre>", escape_text(&lines[i + 1..end].join("\n"))));
                i = end + 1;
            } else if let Some((level, title)) = heading(line) {
                self.formatted = true;
                out.push(format!("<h{level}>{}</h{level}>", self.spans(title, 0, false)));
                i += 1;
            } else if depth < MAX_DEPTH && QUOTE.is_match(line) {
                let quote;
                (i, quote) = self.quote(lines, i, depth);
                out.push(quote);
            } else if depth < MAX_DEPTH
                && let Some(first) = item(line)
            {
                let listed;
                (i, listed) = self.list(lines, i, depth, first);
                out.push(listed);
            } else {
                let paragraph;
                (i, paragraph) = self.paragraph(lines, i);
                out.push(paragraph);
            }
        }
        out.join("\n")
    }

    /// `_Render._quote`.
    fn quote(&mut self, lines: &[&str], mut i: usize, depth: usize) -> (usize, String) {
        let mut inner = Vec::new();
        while i < lines.len()
            && let Some(q) = QUOTE.find(lines[i])
        {
            inner.push(&lines[i][q.end()..]);
            i += 1;
        }
        (i, format!("<blockquote>{}</blockquote>", self.blocks(&inner, depth + 1)))
    }

    /// `_Render._list`, from the item `blocks` has just found on line `i`.
    fn list(&mut self, lines: &[&str], mut i: usize, depth: usize, first: Item<'_>) -> (usize, String) {
        let (ordered, n) = (first.number.is_some(), lines.len());
        let mut items = String::new();
        while i < n
            && let Some(m) = item(lines[i])
            && m.number.is_some() == ordered
        {
            let body;
            (i, body) = item_lines(lines, i + 1, m.width.min(m.indent + 2), &lines[i][m.end..]);
            let inner = self.blocks(&body, depth + 1);
            // A tight item's words stand in the item itself.
            let tight = if body.iter().any(|line| line.is_empty()) {
                None
            } else {
                inner.split_once("</p>").and_then(|(head, rest)| Some(format!("{}{rest}", head.strip_prefix("<p>")?)))
            };
            items.push_str("<li>");
            items.push_str(&tight.unwrap_or(inner));
            items.push_str("</li>");
            let mut k = i;
            while k < n && strip(lines[k]).is_empty() {
                k += 1;
            }
            if k < n && k != i && item(lines[k]).is_some_and(|next| next.number.is_some() == ordered) {
                i = k;
            }
        }
        self.formatted = true;
        let tag = if ordered { "ol" } else { "ul" };
        let start = match first.number.map(number) {
            Some(start) if start != 1 => format!(" start=\"{start}\""),
            _ => String::new(),
        };
        (i, format!("<{tag}{start}>{items}</{tag}>"))
    }

    /// `_Render._paragraph`.
    fn paragraph(&mut self, lines: &[&str], mut i: usize) -> (usize, String) {
        let mut para = vec![strip(lines[i])];
        i += 1;
        while i < lines.len() && !strip(lines[i]).is_empty() && !interrupts(lines, i) {
            para.push(strip(lines[i]));
            i += 1;
        }
        (i, format!("<p>{}</p>", self.spans(&para.join("\n"), 0, false)))
    }

    /// `_Render.spans`. Every place a span starts is an ASCII character, so a step past one is a byte.
    fn spans(&mut self, s: &str, depth: usize, in_link: bool) -> String {
        let mut out = String::with_capacity(s.len());
        let mut pos = 0;
        let mut dead = BTreeSet::new();
        let mut ahead: Option<LinkAhead> = None;
        let mut counted = Counted::default();
        while pos < s.len() {
            let Some(found) = SPECIAL.find_at(s, pos) else {
                push_text(&mut out, &s[pos..]);
                break;
            };
            let at = found.start();
            push_text(&mut out, &s[pos..at]);
            pos = at + 1;
            match s.as_bytes()[at] {
                b'\n' => out.push_str("<br>\n"),
                b'\\' => match char_at(s, pos) {
                    Some(c) if c.is_ascii_punctuation() => {
                        push_text(&mut out, &s[pos..pos + 1]);
                        pos += 1;
                    }
                    _ => out.push('\\'),
                },
                b'!' => match PICTURE_AT.captures(&s[at..]) {
                    Some(picture) if self.pictures.contains(&picture[2]) => {
                        out.push_str(&format!(
                            "<img src=\"cid:{}\" alt=\"{}\">",
                            content_id(&picture[2]),
                            html_escape(&picture[1])
                        ));
                        self.formatted = true;
                        pos = at + picture[0].len();
                    }
                    _ => out.push('!'),
                },
                b'<' => match if in_link { None } else { angled(s, at) } {
                    Some((url_end, end)) => {
                        let url = &s[at + 1..url_end];
                        out.push_str(&link_html(url, &escape_text(url)));
                        pos = end;
                    }
                    None => out.push_str("&lt;"),
                },
                b'`' => {
                    let span;
                    (pos, span) = self.code(s, at, &mut dead);
                    out.push_str(&span);
                }
                b'*' | b'_' => {
                    let span;
                    (pos, span) = self.emphasis(s, at, &mut dead, depth, in_link);
                    out.push_str(&span);
                }
                b'[' => {
                    if in_link {
                        out.push('[');
                        continue;
                    }
                    let at_chars = counted.to(s, at);
                    if ahead.as_ref().is_none_or(|a| a.end < at) && !dead.contains(&Mark::Link) {
                        ahead = match s[at + 1..].find("](") {
                            None => {
                                dead.insert(Mark::Link);
                                None
                            }
                            Some(found) => {
                                let end = at + 1 + found;
                                let end_chars = at_chars + s[at..end].chars().count();
                                Some(LinkAhead { end, end_chars, target: link_target(s, end + 1) })
                            }
                        };
                    }
                    match &ahead {
                        Some(LinkAhead { end, end_chars, target: Some((url, target_end)) })
                            if *end >= at + 2
                                && end_chars - at_chars <= MAX_LINK_TEXT
                                && !dead.contains(&Mark::Link) =>
                        {
                            let words = &s[at + 1..*end];
                            let inner = if depth < MAX_SPAN_DEPTH {
                                self.spans(words, depth + 1, true)
                            } else {
                                escape_text(words)
                            };
                            out.push_str(&link_html(&s[url.clone()], &inner));
                            self.formatted = true;
                            pos = *target_end;
                        }
                        _ => out.push('['),
                    }
                }
                // The start of a bare address.
                _ => {
                    let span;
                    (pos, span) = self.bare_url(s, at, in_link);
                    out.push_str(&span);
                }
            }
        }
        out
    }

    /// `_Render._code`: a code span from the run of backticks at `at` to the next run as long, or the run as typed.
    fn code(&mut self, s: &str, at: usize, dead: &mut BTreeSet<Mark>) -> (usize, String) {
        let run_end = backticks_end(s, at);
        let width = run_end - at;
        if !dead.contains(&Mark::Code(width)) {
            let mut from = run_end;
            while let Some(found) = s[from..].find('`') {
                let start = from + found;
                let end = backticks_end(s, start);
                if end - start == width {
                    let code = s[run_end..start].replace('\n', " ");
                    let code = if code.chars().count() > 2
                        && code.starts_with(' ')
                        && code.ends_with(' ')
                        && !strip(&code).is_empty()
                    {
                        &code[1..code.len() - 1]
                    } else {
                        &code[..]
                    };
                    self.formatted = true;
                    return (end, format!("<code>{}</code>", escape_text(code)));
                }
                from = end;
            }
            dead.insert(Mark::Code(width));
        }
        (run_end, s[at..run_end].to_owned())
    }

    /// `_Render._emphasis`: emphasis or strong from the mark at `at` to where it closes, or the mark's first character.
    fn emphasis(
        &mut self,
        s: &str,
        at: usize,
        dead: &mut BTreeSet<Mark>,
        depth: usize,
        in_link: bool,
    ) -> (usize, String) {
        let b = s.as_bytes();
        let double = b.get(at + 1) == Some(&b[at]);
        let mark = match (b[at], double) {
            (b'*', false) => "*",
            (b'*', true) => "**",
            (_, false) => "_",
            (_, true) => "__",
        };
        let width = mark.len();
        if !dead.contains(&Mark::Emphasis(mark)) && opens(s, at, width) {
            match closer(s, at + width, mark) {
                None => {
                    dead.insert(Mark::Emphasis(mark));
                }
                Some(close) => {
                    let body = &s[at + width..close];
                    let inner =
                        if depth < MAX_SPAN_DEPTH { self.spans(body, depth + 1, in_link) } else { escape_text(body) };
                    self.formatted = true;
                    let tag = if width == 2 { "strong" } else { "em" };
                    return (close + width, format!("<{tag}>{inner}</{tag}>"));
                }
            }
        }
        (at + 1, mark[..1].to_owned())
    }

    /// `_Render._bare_url`: a bare address as a link, without the punctuation that ends its sentence or the closing
    /// parentheses it doesn't open.
    fn bare_url(&mut self, s: &str, at: usize, in_link: bool) -> (usize, String) {
        let found = bare_url_end(s, at);
        let (Some(end), false) = (found, in_link || char_before(s, at).is_some_and(is_alnum)) else {
            return (at + 1, escape_text(&s[at..at + 1]));
        };
        let mut url = s[at..end].trim_end_matches(TRAILING);
        // Python counts both again each time round. Stripping takes no `(` off, so one count of each is enough.
        let (opened, mut closed) = (url.matches('(').count(), url.matches(')').count());
        while url.ends_with(')') && closed > opened {
            url = url[..url.len() - 1].trim_end_matches(TRAILING);
            closed -= 1;
        }
        (at + url.len(), link_html(url, &escape_text(url)))
    }
}

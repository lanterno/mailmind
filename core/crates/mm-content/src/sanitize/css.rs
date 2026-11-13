//! The CSS sanitizer: a `style` attribute's declarations and a `<style>` element's sheet, reduced to what the
//! allowlist keeps. It reads text, not HTML, and never fails: what it can't vet, it drops.
//!
//! Every value is reduced once to one decoded string, its canonical form: comments gone, escapes decoded, controls
//! and runs of whitespace made one space. The checks, the url() gate and what is written all read that one string,
//! the checks only lower-cased, so nothing is written that the checks didn't see decoded. A backslash, a control
//! character or a stray `url(` left after that drops the declaration.
//!
//! It is the Python app's sanitizer, answer for answer: tests/unit/content/test_sanitize_in_rust.py holds the two
//! together. Where Python's `str` and `re` read text their own way, `super::text` reads it as they did.

use std::borrow::Cow;
use std::fmt::Write as _;
use std::sync::LazyLock;

use regex::{Captures, Regex};

use super::text::{self, pattern, space};

/// A `style` attribute longer than this, in characters, keeps nothing. It is judged before any work is done on it.
pub const INLINE_MAX: usize = 4096;
/// A declaration's value longer than this once canonical drops the declaration.
pub const DECL_VALUE_MAX: usize = 1024;
/// A property name longer than this drops the declaration.
pub const PROP_MAX: usize = 64;
/// How many declarations a `style` attribute is read for.
pub const INLINE_DECLS: usize = 64;
/// How much of a `<style>` element is read, in characters.
pub const SHEET_MAX: usize = 50_000;
/// How many rules of a sheet are read, nested ones included.
pub const SHEET_RULES: usize = 1000;
/// How many declarations a rule's block is read for.
pub const SHEET_DECLS: usize = 256;
/// How deep `@media`, `@supports` and `@layer` may nest.
pub const NEST_MAX: usize = 3;
/// A selector longer than this drops its rule.
pub const SELECTOR_MAX: usize = 512;
/// A url() whose argument is longer than this drops the declaration.
pub const URL_ARG_MAX: usize = 4096;

/// The properties kept. Every other is dropped.
const ALLOWED_PROPS: &[&str] = &[
    // color and text
    "color",
    "background-color",
    "opacity",
    "font",
    "font-family",
    "font-size",
    "font-style",
    "font-weight",
    "font-variant",
    "font-stretch",
    "line-height",
    "letter-spacing",
    "word-spacing",
    "text-align",
    "text-decoration",
    "text-decoration-line",
    "text-decoration-color",
    "text-decoration-style",
    "text-transform",
    "text-indent",
    "text-overflow",
    "text-shadow",
    "white-space",
    "word-break",
    "word-wrap",
    "overflow-wrap",
    "direction",
    "unicode-bidi",
    "vertical-align",
    "list-style-type",
    "list-style-position",
    // the box
    "margin",
    "margin-top",
    "margin-right",
    "margin-bottom",
    "margin-left",
    "padding",
    "padding-top",
    "padding-right",
    "padding-bottom",
    "padding-left",
    "border",
    "border-width",
    "border-style",
    "border-color",
    "border-top",
    "border-right",
    "border-bottom",
    "border-left",
    "border-top-width",
    "border-top-style",
    "border-top-color",
    "border-right-width",
    "border-right-style",
    "border-right-color",
    "border-bottom-width",
    "border-bottom-style",
    "border-bottom-color",
    "border-left-width",
    "border-left-style",
    "border-left-color",
    "border-radius",
    "border-top-left-radius",
    "border-top-right-radius",
    "border-bottom-left-radius",
    "border-bottom-right-radius",
    "border-collapse",
    "border-spacing",
    "box-shadow",
    "box-sizing",
    "outline",
    // sizes
    "width",
    "height",
    "min-width",
    "min-height",
    "max-width",
    "max-height",
    // layout that draws nothing over anything else
    "display",
    "float",
    "clear",
    "overflow",
    "overflow-x",
    "overflow-y",
    "visibility",
    "table-layout",
    "caption-side",
    "empty-cells",
    // those that may carry a url(), which the gate reads
    "background",
    "background-image",
    "background-repeat",
    "background-position",
    "background-size",
    "background-clip",
    "background-origin",
    "background-attachment",
    "list-style",
    "list-style-image",
    // kept only with the values `value_allowed` lists
    "position",
];

/// The properties a url() may stand in. Any other with a url() is dropped.
const URL_PROPS: &[&str] = &["background", "background-image", "list-style", "list-style-image"];

/// Properties whose every word must be one of these: `position` may never place anything over the page.
fn value_allowed(prop: &str) -> Option<&'static [&'static str]> {
    match prop {
        "position" => Some(&["static", "relative", "inherit", "initial", "unset", "revert"]),
        _ => None,
    }
}

/// Properties that take keywords only: a function, a url, a scheme or a nested declaration drops them.
const KEYWORD_ONLY: &[&str] = &[
    "display",
    "float",
    "clear",
    "overflow",
    "overflow-x",
    "overflow-y",
    "visibility",
    "position",
    "table-layout",
    "border-collapse",
    "caption-side",
    "empty-cells",
    "white-space",
    "text-transform",
    "text-align",
    "font-style",
    "font-weight",
    "list-style-type",
    "list-style-position",
    "vertical-align",
    "box-sizing",
    "direction",
    "unicode-bidi",
    "word-break",
];

/// Sizes a viewport unit, or a percentage height, would let a message stretch into a curtain over the whole pane.
const SIZING_PROPS: &[&str] = &["width", "height", "min-width", "min-height", "max-width", "max-height"];

/// The viewport units, which `has_viewport_unit` looks for.
const VIEWPORT_UNITS: &[&str] = &["vh", "vw", "vmin", "vmax", "dvh", "svh", "lvh", "dvw", "svw", "lvw"];

/// What drops a value wherever it stands, matched on the lower-cased canonical value. Each arm is flat, so the match
/// is linear in the value's length. Any angle bracket keeps the sheet inside its `<style>`; a backslash left after
/// decoding means an escape that was malformed or decoded to another.
static BAD_VALUE: LazyLock<Regex> = LazyLock::new(|| {
    pattern(concat!(
        r"(?i)expression",
        space!(),
        r"*\(|javascript",
        space!(),
        r"*:|vbscript",
        space!(),
        r"*:|behaviou?r",
        space!(),
        r"*:?|-moz-binding|binding",
        space!(),
        r"*:|@import|@charset|@namespace|image-set",
        space!(),
        r"*\(|<",
        space!(),
        r"*/?",
        space!(),
        r"*style|[<>]|&",
        space!(),
        r"*\{|\\",
    ))
});

/// Functions that bring a value in from elsewhere, or a remote image by another name. A url() has its own gate.
static BAD_FUNCTION: LazyLock<Regex> =
    LazyLock::new(|| pattern(concat!(r"(?i)(?:var|env|attr|image-set|cross-fade|element|paint)", space!(), r"*\(")));

/// A hex escape: a backslash, one to six hex digits and one optional whitespace character after them.
static HEX_ESCAPE: LazyLock<Regex> = LazyLock::new(|| pattern(r"\\([0-9a-fA-F]{1,6})[ \t\r\n\x0C]?"));
/// An escaped character that isn't a hex digit or a line break.
static CHAR_ESCAPE: LazyLock<Regex> = LazyLock::new(|| pattern(r"\\([^0-9a-fA-F\r\n\x0C])"));
/// A url() token, quoted or not, read on the canonical value.
static URL_TOKEN: LazyLock<Regex> =
    LazyLock::new(|| pattern(concat!(r#"(?i:url)\("#, space!(), r#"*("[^"]*"|'[^']*'|[^)]*)"#, space!(), r#"*\)"#)));
/// The url("...") form the gate writes, which is all a written value may hold of a url().
static CANONICAL_URL: LazyLock<Regex> = LazyLock::new(|| pattern(r#"url\("(?:[^"\\]|\\.)*"\)"#));

/// The schemes a URL may have: `^\s*(https?:|mailto:|cid:)`, ignoring case. Matched on `text::i_folded`.
pub(crate) static SAFE_URL: LazyLock<Regex> =
    LazyLock::new(|| pattern(concat!(r"\A", space!(), r"*(?i:https?:|mailto:|cid:)")));
/// A raster image as a data URL, never SVG, which can hold script. Matched on `text::i_folded`.
pub(crate) static DATA_IMAGE: LazyLock<Regex> =
    LazyLock::new(|| pattern(concat!(r"\A", space!(), r"*(?i:data:image/(?:png|jpe?g|gif|webp|bmp);)")));

/// What drops a rule's selector, matched on `text::i_folded`.
static BAD_SELECTOR: LazyLock<Regex> = LazyLock::new(|| pattern(r"(?i)url\(|expression|javascript:|[<>{}\\]"));
/// What an `@media`, `@supports` or `@layer` prelude may hold: Python's `\w` (a letter, a number or `_`) and a few
/// marks. Python's `$` also stands before a final line break.
static PRELUDE: LazyLock<Regex> = LazyLock::new(|| pattern(r"\A[\p{L}\p{N}_ \t()\[\]:,.%/*+\-]*\n?\z"));

/// What the CSS sanitizer reads of the message it is part of, and tells it back.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct CssContext {
    /// Whether the person asked for this message's images: a remote url() is kept only then.
    pub load_images: bool,
    /// How many declarations were dropped for a remote url() while images were blocked.
    pub blocked_urls: usize,
}

impl CssContext {
    /// A context for a message whose images are loaded or not, with nothing blocked yet.
    pub fn new(load_images: bool) -> Self {
        Self { load_images, blocked_urls: 0 }
    }
}

/// A `style` attribute's value, sanitized: `prop:value;prop:value`, or empty.
pub fn sanitize_inline_style(value: &str, ctx: &mut CssContext) -> String {
    if value.is_empty() || text::length(value) > INLINE_MAX {
        return String::new();
    }
    declarations(value, ctx, INLINE_DECLS)
}

/// A `<style>` element's text, sanitized: rules and the at-rules kept, or empty. It holds no angle bracket, so it
/// can't end its `<style>`.
pub fn sanitize_css_stylesheet(css: &str, ctx: &mut CssContext) -> String {
    if css.is_empty() {
        return String::new();
    }
    let mut counted = 0;
    let mut sheet = rules(text::prefix(css, SHEET_MAX), ctx, 0, &mut counted);
    sheet.retain(|c| c != '<' && c != '>');
    sheet
}

/// Each declaration of a block, sanitized and joined with `;`. Only the first `max` are read.
fn declarations(block: &str, ctx: &mut CssContext, max: usize) -> String {
    let mut kept = Vec::new();
    for declaration in split_declarations(block).into_iter().take(max) {
        let Some((prop, value)) = declaration.split_once(':') else { continue };
        if let Some(declaration) = sanitize_one(prop, value, ctx) {
            kept.push(declaration);
        }
    }
    kept.join(";")
}

/// A block's declarations, split on each `;` outside strings and parentheses, so one inside `url("a;b")` or a quoted
/// value splits nothing. An empty one between two `;` counts as one; an empty last one doesn't.
fn split_declarations(block: &str) -> Vec<&str> {
    // Every byte that matters here is ASCII, and a byte inside a longer character is never one of them, so reading
    // bytes is reading characters.
    let bytes = block.as_bytes();
    let (mut split, mut start, mut depth, mut quote, mut at) = (Vec::new(), 0, 0usize, None, 0);
    while at < bytes.len() {
        let b = bytes[at];
        if let Some(q) = quote {
            if b == b'\\' && at + 1 < bytes.len() {
                at += 2;
                continue;
            }
            if b == q {
                quote = None;
            }
        } else {
            match b {
                b'"' | b'\'' => quote = Some(b),
                b'(' => depth += 1,
                b')' => depth = depth.saturating_sub(1),
                b';' if depth == 0 => {
                    split.push(&block[start..at]);
                    start = at + 1;
                }
                _ => {}
            }
        }
        at += 1;
    }
    if start < bytes.len() {
        split.push(&block[start..]);
    }
    split
}

/// One declaration, sanitized: `prop:value`, its value canonical, or None to drop it.
fn sanitize_one(prop: &str, raw: &str, ctx: &mut CssContext) -> Option<String> {
    let prop = text::lower(text::strip(prop));
    let prop = decode_escapes(&prop);
    let prop = text::lower(text::strip(&prop));
    if prop.is_empty() || text::length(&prop) > PROP_MAX {
        return None;
    }
    let prop: &'static str = ALLOWED_PROPS.iter().find(|allowed| **allowed == prop)?;

    let (canon, lowered) = canonical(raw);
    if canon.is_empty() || text::length(&canon) > DECL_VALUE_MAX {
        return None;
    }
    let folded = text::i_folded(&lowered);
    if BAD_VALUE.is_match(&folded) || breaks_out(&canon) || BAD_FUNCTION.is_match(&folded) {
        return None;
    }
    if SIZING_PROPS.contains(&prop)
        && (has_viewport_unit(&lowered)
            || (lowered.contains('%') && matches!(prop, "height" | "min-height" | "max-height")))
    {
        return None;
    }
    if let Some(allowed) = value_allowed(prop) {
        let words = lowered.replace(',', " ");
        let mut words = words.split(' ').filter(|word| !word.is_empty()).peekable();
        if words.peek().is_none() || words.any(|word| !allowed.contains(&word)) {
            return None;
        }
    }
    if KEYWORD_ONLY.contains(&prop) && (lowered.contains(['(', ':', ';']) || lowered.contains("url")) {
        return None;
    }
    let written = gate_urls(&canon, &lowered, URL_PROPS.contains(&prop), ctx)?;

    // Nothing is written that the gate didn't write itself: a url() left over drops the declaration.
    if text::lower(&written).contains("url(") && text::lower(&CANONICAL_URL.replace_all(&written, "")).contains("url(")
    {
        return None;
    }
    if written.contains(['\\', '<', '>']) {
        return None;
    }
    let written = text::strip(text::strip(&written).trim_end_matches(';'));
    if written.is_empty() {
        return None;
    }
    Some(format!("{prop}:{written}"))
}

/// A raw value's canonical form, and the same lower-cased for the checks. In this order: comments taken out, escapes
/// decoded, controls and whitespace made spaces, each run of them one space, and the ends stripped.
fn canonical(raw: &str) -> (String, String) {
    let decoded = strip_comments(raw);
    let decoded = decode_escapes(&decoded);
    let mut canon = String::with_capacity(decoded.len());
    let mut gap = false;
    for c in decoded.chars() {
        if text::is_control(c) || text::is_space(c) {
            gap = true;
            continue;
        }
        if gap && !canon.is_empty() {
            canon.push(' ');
        }
        gap = false;
        canon.push(c);
    }
    let lowered = text::lower(&canon).into_owned();
    (canon, lowered)
}

/// `s` with every `/* ... */` taken out, and an unclosed `/*` with all after it.
fn strip_comments(s: &str) -> Cow<'_, str> {
    if !s.contains("/*") {
        return Cow::Borrowed(s);
    }
    let mut out = String::with_capacity(s.len());
    let mut rest = s;
    while let Some(open) = rest.find("/*") {
        out.push_str(&rest[..open]);
        match rest[open + 2..].find("*/") {
            Some(close) => rest = &rest[open + 2 + close + 2..],
            None => return Cow::Owned(out),
        }
    }
    out.push_str(rest);
    Cow::Owned(out)
}

/// `s` with its escapes decoded, once: hex escapes first, then escaped characters. A backslash left after this pass,
/// from an escape that decoded to one, is left for `BAD_VALUE` to drop. Decoding only shortens, and the result is cut
/// at twice the longest value kept.
fn decode_escapes(s: &str) -> Cow<'_, str> {
    if !s.contains('\\') {
        return Cow::Borrowed(s);
    }
    let hex = HEX_ESCAPE.replace_all(s, |caps: &Captures<'_>| {
        // NUL, a number past Unicode and a surrogate read as U+FFFD. Python wrote a surrogate as a lone one, which no
        // Rust string holds, and nothing the sanitizer asks of a character tells the two apart.
        let decoded = u32::from_str_radix(&caps[1], 16).ok().filter(|cp| *cp != 0).and_then(char::from_u32);
        String::from(decoded.unwrap_or(char::REPLACEMENT_CHARACTER))
    });
    let decoded = CHAR_ESCAPE.replace_all(&hex, "${1}");
    Cow::Owned(text::prefix(&decoded, DECL_VALUE_MAX * 2).to_owned())
}

/// Whether a decoded value, written as it reads, would end its declaration or its rule early: a `;` outside strings and
/// parentheses, a brace outside a string, or a string or parenthesis left open. An escaped quote decodes to a bare
/// one, so `"a\";position:fixed` would read as a string, then a declaration of its own.
fn breaks_out(value: &str) -> bool {
    let (mut quote, mut depth) = (None, 0usize);
    for c in value.chars() {
        if let Some(q) = quote {
            if c == q {
                quote = None;
            }
        } else if c == '"' || c == '\'' {
            quote = Some(c);
        } else if c == '(' {
            depth += 1;
        } else if c == ')' {
            if depth == 0 {
                return true;
            }
            depth -= 1;
        } else if c == '{' || c == '}' || (c == ';' && depth == 0) {
            return true;
        }
    }
    quote.is_some() || depth != 0
}

/// Whether a lower-cased value holds a viewport unit standing alone: `(?<![a-z])(vh|vw|...)(?![a-z])`, ignoring case.
/// The `regex` crate has no lookaround, so this is that pattern by hand.
fn has_viewport_unit(lowered: &str) -> bool {
    let chars: Vec<char> = lowered.chars().collect();
    (0..chars.len()).any(|at| {
        (at == 0 || !text::is_ascii_letter_ignoring_case(chars[at - 1]))
            && VIEWPORT_UNITS.iter().any(|unit| {
                let end = at + unit.len();
                end <= chars.len()
                    && chars[at..end]
                        .iter()
                        .zip(unit.chars())
                        .all(|(c, letter)| text::is_letter_ignoring_case(*c, letter))
                    && chars.get(end).is_none_or(|after| !text::is_ascii_letter_ignoring_case(*after))
            })
    })
}

/// What the url() gate makes of one url() token.
enum Url {
    /// Kept, written in the canonical url("...") form.
    Kept(String),
    /// A remote image while images are blocked.
    Blocked,
    /// Anything else the gate won't write.
    Dropped,
}

/// The canonical value with each url() token gated and rewritten, or None to drop the declaration. One url() the gate
/// won't write drops all of it, never part of a value.
fn gate_urls(canon: &str, lowered: &str, prop_takes_urls: bool, ctx: &mut CssContext) -> Option<String> {
    if !lowered.contains("url(") {
        return Some(canon.to_owned());
    }
    if !prop_takes_urls {
        return None;
    }
    let load_images = ctx.load_images;
    let (mut dropped, mut blocked) = (false, false);
    let written = URL_TOKEN.replace_all(canon, |caps: &Captures<'_>| match gate_url(&caps[1], load_images) {
        Url::Kept(url) => url,
        Url::Blocked => {
            blocked = true;
            dropped = true;
            String::new()
        }
        Url::Dropped => {
            dropped = true;
            String::new()
        }
    });
    if blocked {
        ctx.blocked_urls += 1;
    }
    if dropped { None } else { Some(written.into_owned()) }
}

/// One url() token's argument, gated: a raster data image is kept, a remote http(s) one only with images loaded, and
/// nothing else. After decoding, an argument holding a backslash, a control character, whitespace, a quote, a
/// parenthesis or another url() could be read again as something else, so it is dropped.
fn gate_url(argument: &str, load_images: bool) -> Url {
    let mut arg = text::strip(argument);
    if text::length(arg) >= 2 && arg.starts_with(['"', '\'']) && arg.ends_with(&arg[..1]) {
        arg = &arg[1..arg.len() - 1];
    }
    let arg = text::strip(arg);
    let clean: String = arg.chars().filter(|c| !text::is_control(*c)).collect();
    if clean.is_empty()
        || text::length(&clean) > URL_ARG_MAX
        || clean != arg
        || arg.contains(['\\', '"', '\'', '(', ')'])
        || arg.chars().any(text::is_space)
        || text::lower(arg).contains("url(")
    {
        return Url::Dropped;
    }
    if DATA_IMAGE.is_match(&text::i_folded(&clean)) {
        return Url::Kept(serialize_url(&clean));
    }
    let low = text::lower(&clean);
    if (low.starts_with("http:") || low.starts_with("https:")) && SAFE_URL.is_match(&text::i_folded(&clean)) {
        return if load_images { Url::Kept(serialize_url(&clean)) } else { Url::Blocked };
    }
    Url::Dropped
}

/// A vetted URL as `url("...")`, its quotes and backslashes escaped, so it can end neither the url() nor the
/// declaration.
fn serialize_url(url: &str) -> String {
    format!("url(\"{}\")", url.replace('\\', "\\\\").replace('"', "\\\""))
}

/// One top-level part of a sheet.
enum Unit<'a> {
    /// A rule: its selector, stripped, and its block.
    Rule { selector: &'a str, block: &'a str },
    /// An at-rule: its keyword lower-cased, what stands between the keyword and its block or `;`, stripped, and its
    /// block if it has one.
    At { keyword: String, prelude: &'a str, block: Option<&'a str> },
}

/// A sheet's top-level parts, read with strings and comments honoured, so a brace in either matches nothing. A
/// string, comment, at-rule or block that never ends ends the sheet there.
struct Units<'a> {
    css: &'a str,
    at: usize,
    start: usize,
    done: bool,
}

impl<'a> Units<'a> {
    fn new(css: &'a str) -> Self {
        Self { css, at: 0, start: 0, done: false }
    }
}

impl<'a> Iterator for Units<'a> {
    type Item = Unit<'a>;

    fn next(&mut self) -> Option<Unit<'a>> {
        // Every byte this reads by is ASCII, and a byte inside a longer character is never one of them, so reading
        // bytes is reading characters, and each place it cuts the sheet is between two.
        let css = self.css.as_bytes();
        let n = css.len();
        while !self.done && self.at < n {
            let i = self.at;
            match css[i] {
                b'/' if css.get(i + 1) == Some(&b'*') => match find(css, b"*/", i + 2) {
                    Some(close) => self.at = close + 2,
                    None => self.done = true,
                },
                q @ (b'"' | b'\'') => match string_end(css, i + 1, q) {
                    Some(end) => self.at = end + 1,
                    None => self.done = true,
                },
                b'@' => {
                    let mut j = i;
                    while j < n && css[j] != b'{' && css[j] != b';' {
                        if css[j] == b'"' || css[j] == b'\'' {
                            let q = css[j];
                            j += 1;
                            while j < n && css[j] != q {
                                j += if css[j] == b'\\' { 2 } else { 1 };
                            }
                        }
                        j += 1;
                    }
                    let prelude = &self.css[i..j.min(n)];
                    let named = prelude[1..].bytes().take_while(|b| b.is_ascii_alphabetic() || *b == b'-').count();
                    let keyword = prelude[1..1 + named].to_ascii_lowercase();
                    let prelude = text::strip(&prelude[1 + named..]);
                    if j < n && css[j] == b';' {
                        self.at = j + 1;
                        self.start = self.at;
                        return Some(Unit::At { keyword, prelude, block: None });
                    }
                    if j < n && css[j] == b'{' {
                        let Some((block, end)) = read_block(self.css, j) else {
                            self.done = true;
                            return None;
                        };
                        self.at = end;
                        self.start = end;
                        return Some(Unit::At { keyword, prelude, block: Some(block) });
                    }
                    self.done = true;
                }
                b'{' => {
                    let selector = text::strip(&self.css[self.start..i]);
                    let Some((block, end)) = read_block(self.css, i) else {
                        self.done = true;
                        return None;
                    };
                    self.at = end;
                    self.start = end;
                    return Some(Unit::Rule { selector, block });
                }
                b'}' => {
                    self.at = i + 1;
                    self.start = self.at;
                }
                _ => self.at = i + 1,
            }
        }
        None
    }
}

/// Where `needle` first stands in `haystack` at or after `from`.
fn find(haystack: &[u8], needle: &[u8], from: usize) -> Option<usize> {
    haystack.get(from..)?.windows(needle.len()).position(|w| w == needle).map(|at| at + from)
}

/// Where the string whose text starts at `from` ends: its closing quote `q`, past any escaped character. None if it
/// never does.
fn string_end(css: &[u8], from: usize, q: u8) -> Option<usize> {
    let mut j = from;
    while j < css.len() {
        if css[j] == b'\\' {
            j += 2;
            continue;
        }
        if css[j] == q {
            return Some(j);
        }
        j += 1;
    }
    None
}

/// The text of the block whose `{` stands at `open`, and where the sheet goes on after its `}`, with strings,
/// comments and nested braces honoured. None if it never closes.
fn read_block(css: &str, open: usize) -> Option<(&str, usize)> {
    let bytes = css.as_bytes();
    let (start, mut at, mut depth) = (open + 1, open + 1, 1usize);
    while at < bytes.len() {
        match bytes[at] {
            b'/' if bytes.get(at + 1) == Some(&b'*') => {
                at = find(bytes, b"*/", at + 2)? + 2;
                continue;
            }
            q @ (b'"' | b'\'') => {
                at = string_end(bytes, at + 1, q)? + 1;
                continue;
            }
            b'{' => depth += 1,
            b'}' => {
                depth -= 1;
                if depth == 0 {
                    return Some((&css[start..at], at + 1));
                }
            }
            _ => {}
        }
        at += 1;
    }
    None
}

/// A selector as written, or None to drop its rule.
fn sanitize_selector(selector: &str) -> Option<&str> {
    let selector = text::strip(selector);
    if selector.is_empty() || text::length(selector) > SELECTOR_MAX || BAD_SELECTOR.is_match(&text::i_folded(selector))
    {
        return None;
    }
    Some(selector)
}

/// A sheet's rules, sanitized. `counted` is how many rules have been read so far, nested ones included, and stops the
/// sheet at `SHEET_RULES`.
fn rules(css: &str, ctx: &mut CssContext, depth: usize, counted: &mut usize) -> String {
    let mut out = String::new();
    for unit in Units::new(css) {
        if *counted >= SHEET_RULES {
            break;
        }
        *counted += 1;
        match unit {
            Unit::Rule { selector, block } => {
                let Some(selector) = sanitize_selector(selector) else { continue };
                let declarations = declarations(block, ctx, SHEET_DECLS);
                if !declarations.is_empty() {
                    let _ = write!(out, "{selector}{{{declarations}}}");
                }
            }
            Unit::At { keyword, prelude, block } => {
                // Every other at-rule goes whole: @import, @font-face, @keyframes and @page among them.
                if !matches!(keyword.as_str(), "media" | "supports" | "layer") {
                    continue;
                }
                let Some(block) = block else { continue };
                if depth >= NEST_MAX {
                    continue;
                }
                let prelude = text::strip(prelude);
                if !prelude.is_empty() && (!PRELUDE.is_match(prelude) || text::lower(prelude).contains("url")) {
                    continue;
                }
                let inner = rules(block, ctx, depth + 1, counted);
                if !inner.is_empty() {
                    let _ = write!(out, "@{keyword} {prelude}{{{inner}}}");
                }
            }
        }
    }
    out
}

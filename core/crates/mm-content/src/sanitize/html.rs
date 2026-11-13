//! The HTML sanitizer: a message's HTML read as a browser reads it, by html5ever's tokenizer, and written again from
//! an allowlist.
//!
//! The output is safe whatever the input, by construction: only `Writer` writes, and it writes a tag only as an
//! `Allowed` (a name from `ALLOWED`), an attribute only by a name from `GLOBAL_ATTRS` or `own_attrs`, every attribute
//! value escaped and double-quoted, every text escaped, and the fold's two fixed strings. The one raw text it writes
//! is a `<style>` body from the CSS sanitizer, and it writes that with every `<` taken out, so it can't end its
//! element. What the allowlist keeps of an attribute's value (no handler, no scheme but those `SAFE_URL` names) is
//! decided in `Sanitizer::attributes`.
//!
//! The tokenizer reads a tag's name and attributes, character references and comments as a browser does, and the
//! sink switches its state on a start tag as a browser's tree builder does with scripting off (the frame runs none):
//! see `raw_state`. What it does with each token is the Python app's sanitizer's: keep, drop, unwrap (drop the tag,
//! keep its text), drop with all it holds, fold the quoted reply, count what it blocked. Where a browser's tree
//! builder would do more (close a `<p>`, move a table's text), the sanitizer doesn't: it skips a dropped element to
//! its end tag, and leaves unclosed what the message left unclosed, for the browser to close.

use std::borrow::Cow;
use std::cell::RefCell;
use std::panic;
use std::sync::LazyLock;

use html5ever::Attribute;
use html5ever::tendril::StrTendril;
use html5ever::tokenizer::states::RawKind;
use html5ever::tokenizer::{BufferQueue, Tag, TagKind, Token, TokenSink, TokenSinkResult, Tokenizer, TokenizerOpts};
use regex::Regex;

use super::css::{self, CssContext, DATA_IMAGE, SAFE_URL};
use super::text::{self, pattern, space};

/// Tags dropped with all they hold, to their end tag.
const DROP_WITH_CONTENT: &[&str] = &[
    "script", "head", "title", "iframe", "object", "applet", "form", "button", "select", "textarea", "noscript",
    "template", "svg", "math", "frameset", "xml",
];

/// Tags dropped that hold nothing, so they never start a skip that would swallow the rest of the message.
const DROP_VOID: &[&str] = &["meta", "link", "base", "input", "frame", "source", "param", "keygen", "embed"];

/// The tags written. Any other is unwrapped: the tag goes, its text stays.
#[rustfmt::skip]
const ALLOWED: &[&str] = &[
    "p", "br", "hr", "div", "span", "a", "b", "i", "u", "s", "strike", "em", "strong", "small", "sub", "sup", "mark",
    "abbr", "cite", "q", "blockquote", "pre", "code", "kbd", "samp", "var", "ul", "ol", "li", "dl", "dt", "dd",
    "table", "thead", "tbody", "tfoot", "tr", "td", "th", "caption", "colgroup", "col", "h1", "h2", "h3", "h4", "h5",
    "h6", "img", "figure", "figcaption", "center", "font", "address", "article", "section", "header", "footer",
    "main", "nav", "aside", "wbr", "time", "details", "summary",
];

/// The written tags that have no end tag, written `<br/>`.
const VOID: &[&str] = &["br", "hr", "img", "col", "wbr"];

/// The attributes kept on every written tag, besides a sanitized `style`.
const GLOBAL_ATTRS: &[&str] =
    &["dir", "title", "lang", "align", "colspan", "rowspan", "width", "height", "valign", "span", "start", "datetime"];

/// The attributes kept on one tag besides the global ones.
fn own_attrs(tag: &str) -> &'static [&'static str] {
    match tag {
        "a" => &["href"],
        "img" => &["src", "alt"],
        "td" | "th" => &["colspan", "rowspan"],
        "font" => &["color"],
        _ => &[],
    }
}

/// Attributes dropped whatever tag they stand on, besides every `on*` handler: each loads or acts on something.
const DROPPED_ATTRS: &[&str] = &["srcset", "background", "formaction", "xlink:href"];

/// A `style` attribute longer than this, in characters, is dropped. The CSS sanitizer keeps nothing of one past
/// `css::INLINE_MAX` either.
const STYLE_ATTR_MAX: usize = 8192;
/// Any other attribute whose value is longer than this, in characters, is dropped.
const ATTR_VALUE_MAX: usize = 2048;

/// What drops a plain attribute's value: CSS or script smuggled into `color=` or `width=`. Matched on
/// `text::i_folded`.
static BAD_ATTR_VALUE: LazyLock<Regex> = LazyLock::new(|| {
    pattern(concat!(
        r"(?i)expression",
        space!(),
        r"*\(|javascript",
        space!(),
        r"*:|vbscript",
        space!(),
        r"*:|behaviou?r",
        space!(),
        r"*:|-moz-binding|@import|&",
        space!(),
        r"*\{|/\*",
    ))
});

/// The fold's opening: the first quoted reply of a message with something before it, and all after it, go behind a
/// toggle the reader's stylesheet draws.
pub const FOLD_OPEN: &str = "<details class=\"mm-quote\"><summary>\u{b7}\u{b7}\u{b7} quoted text</summary>";
/// The fold's close, at the very end of what `sanitize` writes.
pub const FOLD_CLOSE: &str = "</details>";

/// What the sanitizer blocked of a message, for the reader to offer to load.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct Blocked {
    /// Remote and data images left out while images are blocked.
    pub images: usize,
    /// Links whose URL had a scheme the sanitizer doesn't keep.
    pub links: usize,
    /// CSS declarations dropped for a remote url() while images are blocked.
    pub css_urls: usize,
}

/// A message's HTML made safe to show, and what was blocked of it.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Sanitized {
    pub html: String,
    pub blocked: Blocked,
}

/// `html`, sanitized. `load_images` keeps remote images and CSS backgrounds; `fold` is for reading, and puts a quoted
/// reply behind a toggle, which mail going out must not carry.
pub fn sanitize(html: &str, load_images: bool, fold: bool) -> Sanitized {
    // html5ever asserts its own invariants as it reads. Were one ever to fail, the reader still gets the text, all of
    // it escaped, as the Python app gave it when its parser raised.
    panic::catch_unwind(|| {
        let sink = tokenize(html, Sink(RefCell::new(Sanitizer::new(load_images, fold))));
        sink.0.into_inner().finish()
    })
    .unwrap_or_else(|_| {
        let mut out = Writer::default();
        out.text(html);
        Sanitized { html: out.0, blocked: Blocked::default() }
    })
}

/// `html`, sanitized before replies stopped carrying the fold, without it: a draft's stored quote. `sanitize` closes
/// the fold at the very end of what it writes, so the last close is the fold's.
pub fn without_fold(html: &str) -> String {
    let Some(open) = html.find(FOLD_OPEN) else { return html.to_owned() };
    let (head, rest) = (&html[..open], &html[open + FOLD_OPEN.len()..]);
    match rest.rfind(FOLD_CLOSE) {
        Some(close) => [head, &rest[..close], &rest[close + FOLD_CLOSE.len()..]].concat(),
        None => [head, rest].concat(),
    }
}

/// A token of HTML as `read` gives it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Read {
    /// A start tag: its name and its attributes, in order, as a browser reads them.
    Start { name: String, attrs: Vec<(String, String)>, self_closing: bool },
    /// An end tag's name.
    End { name: String },
    /// Text, its character references decoded.
    Text(String),
    /// A comment's text.
    Comment(String),
}

/// What a browser's tokenizer reads of `html`, token by token, its state switched on start tags as `sanitize`
/// switches it outside `<svg>` and `<math>`. It is for reading back what `sanitize` wrote, which holds neither.
pub fn read(html: &str) -> Vec<Read> {
    tokenize(html, Recorder::default()).0.into_inner()
}

/// The state a browser's tree builder puts the tokenizer in after a start tag, with scripting off: the text of a
/// `<title>` or `<textarea>` is RCDATA (character references, no tags), of `<style>`, `<xmp>`, `<iframe>`,
/// `<noembed>` and `<noframes>` RAWTEXT, of `<script>` script data, and all after `<plaintext>` is text. A
/// `<noscript>` holds markup, as scripting is off.
fn raw_state(tag: &str) -> TokenSinkResult<()> {
    match tag {
        "title" | "textarea" => TokenSinkResult::RawData(RawKind::Rcdata),
        "style" | "xmp" | "iframe" | "noembed" | "noframes" => TokenSinkResult::RawData(RawKind::Rawtext),
        "script" => TokenSinkResult::RawData(RawKind::ScriptData),
        "plaintext" => TokenSinkResult::Plaintext,
        _ => TokenSinkResult::Continue,
    }
}

/// Runs html5ever's tokenizer over `html` into `sink`, to the end, and gives the sink back.
fn tokenize<S: TokenSink<Handle = ()>>(html: &str, sink: S) -> S {
    let input = BufferQueue::default();
    input.push_back(StrTendril::from_slice(html));
    // The input is text already decoded, so a byte order mark at its start is a character like any other.
    let tokenizer = Tokenizer::new(sink, TokenizerOpts { discard_bom: false, ..TokenizerOpts::default() });
    // A sink here never asks the tokenizer to stop for a script, so one feed reads all of it.
    let _ = tokenizer.feed(&input);
    tokenizer.end();
    tokenizer.sink
}

/// A tag the allowlist names: the only kind `Writer` writes.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct Allowed(&'static str);

impl Allowed {
    fn named(name: &str) -> Option<Self> {
        ALLOWED.iter().copied().find(|allowed| *allowed == name).map(Allowed)
    }

    fn is_void(self) -> bool {
        VOID.contains(&self.0)
    }

    /// The name of an attribute this tag keeps, as the allowlist writes it.
    fn attribute(self, name: &str) -> Option<&'static str> {
        GLOBAL_ATTRS.iter().chain(own_attrs(self.0)).find(|allowed| **allowed == name).copied()
    }

    /// The attribute of this tag that holds a URL.
    fn url_attribute(self) -> Option<&'static str> {
        match self.0 {
            "a" => Some("href"),
            "img" => Some("src"),
            _ => None,
        }
    }
}

/// What the sanitizer writes, and all it can write: allowlisted tags, attributes by allowlisted names with their
/// values escaped and double-quoted, escaped text, a style sheet with no `<` in it, and the fold.
#[derive(Debug, Default)]
struct Writer(String);

impl Writer {
    /// Text, its `&`, `<` and `>` escaped.
    fn text(&mut self, text: &str) {
        for c in text.chars() {
            match c {
                '&' => self.0.push_str("&amp;"),
                '<' => self.0.push_str("&lt;"),
                '>' => self.0.push_str("&gt;"),
                _ => self.0.push(c),
            }
        }
    }

    /// An attribute's value, its `&`, `<`, `>` and both quotes escaped.
    fn value(&mut self, value: &str) {
        for c in value.chars() {
            match c {
                '&' => self.0.push_str("&amp;"),
                '<' => self.0.push_str("&lt;"),
                '>' => self.0.push_str("&gt;"),
                '"' => self.0.push_str("&quot;"),
                '\'' => self.0.push_str("&#x27;"),
                _ => self.0.push(c),
            }
        }
    }

    /// A start tag with its attributes in the order given. A void one closes itself: `<img/>`.
    fn open(&mut self, tag: Allowed, attrs: &[(&'static str, Cow<'_, str>)]) {
        self.0.push('<');
        self.0.push_str(tag.0);
        for (name, value) in attrs {
            self.0.push(' ');
            self.0.push_str(name);
            self.0.push_str("=\"");
            self.value(value);
            self.0.push('"');
        }
        self.0.push_str(if tag.is_void() { "/>" } else { ">" });
    }

    fn close(&mut self, tag: Allowed) {
        self.0.push_str("</");
        self.0.push_str(tag.0);
        self.0.push('>');
    }

    /// A `<style>` element holding `sheet`, which the CSS sanitizer wrote. Its text is raw, as a browser reads a style
    /// sheet's, so any `<` goes: without one nothing can end the element early.
    fn style_sheet(&mut self, sheet: &str) {
        self.0.push_str("<style>");
        self.0.extend(sheet.chars().filter(|c| *c != '<'));
        self.0.push_str("</style>");
    }

    fn fold_open(&mut self) {
        self.0.push_str(FOLD_OPEN);
    }

    fn fold_close(&mut self) {
        self.0.push_str(FOLD_CLOSE);
    }
}

/// What the sanitizer has read so far, and what it has written.
#[derive(Debug)]
struct Sanitizer {
    out: Writer,
    fold: bool,
    css: CssContext,
    blocked_images: usize,
    blocked_links: usize,
    /// How deep inside the element being dropped with its content, counting the same tag nested in it.
    skip: usize,
    /// The tag being dropped with its content.
    skip_tag: Option<&'static str>,
    /// How deep inside `<blockquote>`s.
    quote_depth: usize,
    /// Whether the fold has opened.
    folded: bool,
    /// Whether any text but whitespace came before the first quote: only then is there something to fold under.
    has_content: bool,
    /// The text of the `<style>` being read, which is written once its end tag comes.
    style: Option<String>,
}

impl Sanitizer {
    fn new(load_images: bool, fold: bool) -> Self {
        Self {
            out: Writer::default(),
            fold,
            css: CssContext::new(load_images),
            blocked_images: 0,
            blocked_links: 0,
            skip: 0,
            skip_tag: None,
            quote_depth: 0,
            folded: false,
            has_content: false,
            style: None,
        }
    }

    /// Whether the tokenizer is inside an `<svg>` or `<math>`, which a browser reads as foreign content: no tag there
    /// switches it to raw text, a tag written to close itself does, and a CDATA section is one. All of it is dropped.
    fn in_foreign_content(&self) -> bool {
        self.skip > 0 && matches!(self.skip_tag, Some("svg" | "math"))
    }

    fn start(&mut self, tag: &Tag) -> TokenSinkResult<()> {
        let foreign = self.in_foreign_content();
        self.open(tag, foreign);
        if foreign { TokenSinkResult::Continue } else { raw_state(&tag.name) }
    }

    fn open(&mut self, tag: &Tag, foreign: bool) {
        let name = &*tag.name;
        if self.skip > 0 {
            if self.skip_tag == Some(name) && !(foreign && tag.self_closing) {
                self.skip += 1;
            }
            return;
        }
        if DROP_VOID.contains(&name) {
            return;
        }
        if name == "style" {
            self.style = Some(String::new());
            return;
        }
        if let Some(dropped) = DROP_WITH_CONTENT.iter().find(|dropped| **dropped == name) {
            // A browser closes an <svg/> or <math/> at once. On any other HTML element it ignores the slash.
            if !(tag.self_closing && matches!(name, "svg" | "math")) {
                self.skip = 1;
                self.skip_tag = Some(dropped);
            }
            return;
        }
        let Some(allowed) = Allowed::named(name) else { return };
        if allowed.0 == "blockquote" {
            // Replies put the quote last, so the first top-level quote and all after it fold, when something came
            // before it: a message that is all quote doesn't.
            if self.fold && self.quote_depth == 0 && !self.folded && self.has_content {
                self.out.fold_open();
                self.folded = true;
            }
            self.quote_depth += 1;
        }
        let attrs = self.attributes(allowed, &tag.attrs);
        self.out.open(allowed, &attrs);
    }

    fn end(&mut self, name: &str) {
        if name == "style" {
            if let Some(sheet) = self.style.take() {
                self.style_sheet(&sheet);
            }
            return;
        }
        if self.skip > 0 {
            if self.skip_tag == Some(name) {
                self.skip -= 1;
                if self.skip == 0 {
                    self.skip_tag = None;
                }
            }
            return;
        }
        if let Some(allowed) = Allowed::named(name)
            && !allowed.is_void()
        {
            self.out.close(allowed);
            if allowed.0 == "blockquote" && self.quote_depth > 0 {
                self.quote_depth -= 1;
            }
        }
    }

    fn text(&mut self, data: &str) {
        if let Some(sheet) = &mut self.style {
            sheet.push_str(data);
            return;
        }
        if self.skip > 0 {
            return;
        }
        if !text::strip(data).is_empty() && self.quote_depth == 0 && !self.folded {
            self.has_content = true;
        }
        self.out.text(data);
    }

    fn style_sheet(&mut self, css: &str) {
        let sheet = css::sanitize_css_stylesheet(css, &mut self.css);
        if !sheet.is_empty() {
            self.out.style_sheet(&sheet);
        }
    }

    /// The attributes an allowed tag keeps, in the order given, by their allowlisted names: a sanitized `style`, a URL
    /// with a kept scheme, and plain values with nothing smuggled in. A link that keeps its `href` opens apart from
    /// the reader, telling the page it leaves nothing.
    fn attributes<'a>(&mut self, tag: Allowed, attrs: &'a [Attribute]) -> Vec<(&'static str, Cow<'a, str>)> {
        let mut kept = Vec::new();
        for attr in attrs {
            let (name, value) = (&*attr.name.local, &*attr.value);
            if name == "style" {
                if text::length(value) <= STYLE_ATTR_MAX {
                    let style = css::sanitize_inline_style(value, &mut self.css);
                    if !style.is_empty() {
                        kept.push(("style", Cow::Owned(style)));
                    }
                }
                continue;
            }
            if name.starts_with("on") || DROPPED_ATTRS.contains(&name) {
                continue;
            }
            let Some(name) = tag.attribute(name) else { continue };
            if tag.url_attribute() == Some(name) {
                let url = if name == "src" { self.image_source(value) } else { self.link(value) };
                if let Some(url) = url {
                    kept.push((name, Cow::Borrowed(url)));
                }
                continue;
            }
            if text::length(value) > ATTR_VALUE_MAX || BAD_ATTR_VALUE.is_match(&text::i_folded(value)) {
                continue;
            }
            kept.push((name, Cow::Borrowed(value)));
        }
        if tag.0 == "a" && kept.iter().any(|(name, _)| *name == "href") {
            kept.push(("rel", Cow::Borrowed("noopener noreferrer nofollow")));
            kept.push(("target", Cow::Borrowed("_blank")));
        }
        kept
    }

    /// An image's `src` kept, or None. A raster data image, and a remote or `cid:` one, only while images load; each
    /// left out then counts as blocked.
    fn image_source<'a>(&mut self, value: &'a str) -> Option<&'a str> {
        if !value.is_empty() && DATA_IMAGE.is_match(&text::i_folded(value)) {
            if !self.css.load_images {
                self.blocked_images += 1;
                return None;
            }
            return Some(value);
        }
        // Python lower-cased the whole source to see how it starts. Lower-casing makes no space, and each character
        // gives at least one, so the first four past the spaces say as much.
        let start = text::lower(text::prefix(value.trim_start_matches(text::is_space), 4));
        let safe = if start.starts_with("http") || start.starts_with("cid") { safe_url(value) } else { None };
        if safe.is_some() && !self.css.load_images {
            self.blocked_images += 1;
            return None;
        }
        safe
    }

    /// A link's `href` kept, or None, which counts as a blocked link.
    fn link<'a>(&mut self, value: &'a str) -> Option<&'a str> {
        let safe = safe_url(value);
        if safe.is_none() {
            self.blocked_links += 1;
        }
        safe
    }

    fn finish(mut self) -> Sanitized {
        // A <style> never closed is written at the end, so the rest of the message isn't lost with it.
        if let Some(sheet) = self.style.take()
            && !sheet.is_empty()
        {
            self.style_sheet(&sheet);
        }
        if self.folded {
            self.out.fold_close();
        }
        Sanitized {
            html: self.out.0,
            blocked: Blocked {
                images: self.blocked_images,
                links: self.blocked_links,
                css_urls: self.css.blocked_urls,
            },
        }
    }
}

/// `value` if its scheme is one kept, read past the controls and spaces that could hide another (`java\tscript:`).
fn safe_url(value: &str) -> Option<&str> {
    if value.is_empty() {
        return None;
    }
    let bare: String = value.chars().filter(|c| !text::is_control(*c)).collect();
    SAFE_URL.is_match(&text::i_folded(&bare)).then_some(value)
}

/// The sanitizer as html5ever's sink. The tokenizer hands it tokens through a shared reference.
struct Sink(RefCell<Sanitizer>);

impl TokenSink for Sink {
    type Handle = ();

    fn process_token(&self, token: Token, _line: u64) -> TokenSinkResult<()> {
        let mut sanitizer = self.0.borrow_mut();
        match token {
            Token::TagToken(tag) if tag.kind == TagKind::StartTag => return sanitizer.start(&tag),
            Token::TagToken(tag) => sanitizer.end(&tag.name),
            Token::CharacterTokens(data) => sanitizer.text(&data),
            // A browser drops a NUL in text. The tokenizer has already made one U+FFFD everywhere else.
            Token::NullCharacterToken => {}
            // Comments, doctypes and parse errors show nothing.
            Token::CommentToken(_) | Token::DoctypeToken(_) | Token::ParseError(_) | Token::EOFToken => {}
        }
        TokenSinkResult::Continue
    }

    fn adjusted_current_node_present_but_not_in_html_namespace(&self) -> bool {
        self.0.borrow().in_foreign_content()
    }
}

/// A sink that records what it reads, for `read`.
#[derive(Default)]
struct Recorder(RefCell<Vec<Read>>);

impl TokenSink for Recorder {
    type Handle = ();

    fn process_token(&self, token: Token, _line: u64) -> TokenSinkResult<()> {
        let mut read = self.0.borrow_mut();
        match token {
            Token::TagToken(tag) if tag.kind == TagKind::StartTag => {
                let attrs = tag.attrs.iter().map(|a| (a.name.local.to_string(), a.value.to_string())).collect();
                read.push(Read::Start { name: tag.name.to_string(), attrs, self_closing: tag.self_closing });
                return raw_state(&tag.name);
            }
            Token::TagToken(tag) => read.push(Read::End { name: tag.name.to_string() }),
            Token::CharacterTokens(data) => match read.last_mut() {
                Some(Read::Text(text)) => text.push_str(&data),
                _ => read.push(Read::Text(data.to_string())),
            },
            Token::NullCharacterToken => {}
            Token::CommentToken(data) => read.push(Read::Comment(data.to_string())),
            Token::DoctypeToken(_) | Token::ParseError(_) | Token::EOFToken => {}
        }
        TokenSinkResult::Continue
    }
}

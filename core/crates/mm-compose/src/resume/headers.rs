//! A draft's headers as the Python app read them, with `email.policy.default` (`mailmind.content.headers`): the
//! Subject as its header registry decodes it, and To, Cc and Bcc as composer chips, split as its address parser
//! splits them, with `header_chips`'s fallbacks where that parser raised.
//!
//! The parser is `email._header_value_parser`'s, step for step: `get_unstructured` for the Subject, and
//! `get_address_list` with the tokens it builds and the properties read off them for an address list. A header's
//! bytes are read as UTF-8 here, where Python kept each 8-bit byte as a lone surrogate and read the text as UTF-8 only
//! at the end: the two part only where such bytes fall on either side of a break the parser makes, which no client
//! writes. An encoded word in a charset other than UTF-8, ASCII and Latin-1 decodes as a browser decodes that charset,
//! where Python used its own codec.

use std::rc::Rc;
use std::sync::LazyLock;

use mail_parser::Charset;
use regex::Regex;

use mm_kernel::addresses::getaddresses;
use mm_kernel::text::{is_space, strip};

use super::parts::Header;

/// How deep comments nest before Python's parser ran out of stack (`RecursionError`), as a script calling
/// `header_chips` measured it: 493 parentheses open at once. A deeper call stack ran out sooner.
const DEEPEST_COMMENT: usize = 492;

/// RFC 5322's specials, as `_header_value_parser.SPECIALS`.
const SPECIALS: &str = "()<>@,:;.\\\"[]";

/// `TSPECIALS`: what ends a MIME token besides a blank.
const TSPECIALS: &str = "()<>@,:;\\\"[]/?=";

fn pattern(source: &str) -> Regex {
    Regex::new(source).expect("a pattern written in this crate compiles")
}

/// `_header_value_parser.rfc2047_matcher`: an encoded word anywhere in a run of text.
static ENCODED_WORD: LazyLock<Regex> = LazyLock::new(|| pattern(r"=\?[^?]*\?[qQbB]\?.*?\?="));
/// `headers._CHIP_QUOTE`: what a chip's name is quoted for.
static CHIP_QUOTE: LazyLock<Regex> = LazyLock::new(|| pattern(r#"[()<>\[\]:;@\\,."]"#));
/// The addresses `header_chips` finds in a header no parser reads.
static WRITTEN_ADDRESS: LazyLock<Regex> =
    LazyLock::new(|| pattern(r#"[^\s\x1C-\x1F<>,;:"()\[\]]+@[^\s\x1C-\x1F<>,;:"()\[\]]+"#));

/// `headers.addr_chip`: a recipient the way the composer holds one, the bare address, or Name <address> with the name
/// quoted when it has a character an address list would split on.
pub fn addr_chip(name: &str, addr: &str) -> String {
    let name = strip(name);
    if name.is_empty() || name.to_lowercase() == addr.to_lowercase() {
        return addr.to_owned();
    }
    if CHIP_QUOTE.is_match(name) {
        return format!("\"{}\" <{addr}>", name.replace('\\', "\\\\").replace('"', "\\\""));
    }
    format!("{name} <{addr}>")
}

/// Every value of one header, as `header_source_parse` stored it.
pub(super) fn raw_values<'h>(headers: &'h [Header], name: &str) -> Vec<&'h [u8]> {
    headers
        .iter()
        .filter(|(key, _)| key.eq_ignore_ascii_case(name.as_bytes()))
        .map(|(_, value)| value.as_slice())
        .collect()
}

/// A stored value as the header registry parses it (`header_fetch_parse`): read as UTF-8, every line break gone.
pub(super) fn fetched(raw: &[u8]) -> String {
    String::from_utf8_lossy(raw).chars().filter(|c| *c != '\r' && *c != '\n').collect()
}

/// `headers.raw_header`: every value of one header as written, unfolded and unparsed.
fn raw_header(headers: &[Header], name: &str) -> Vec<String> {
    static FOLD: LazyLock<Regex> = LazyLock::new(|| pattern(r"\r?\n([ \t])"));
    raw_values(headers, name)
        .into_iter()
        .map(|raw| FOLD.replace_all(&String::from_utf8_lossy(raw), "$1").into_owned())
        .collect()
}

/// `headers.header_text` of an unstructured header, the Subject: its first value with its encoded words decoded,
/// or None when there is none.
pub(super) fn header_text(headers: &[Header], name: &str) -> Option<String> {
    raw_values(headers, name).first().map(|raw| unstructured(&fetched(raw)))
}

/// `headers.header_text` of In-Reply-To or References, which the header registry reads as message IDs
/// (`ReferencesHeader`): the first value as `parse_message_ids` writes it out, or as written where that raised.
pub(super) fn header_ids(headers: &[Header], name: &str) -> Option<String> {
    let first = raw_values(headers, name).into_iter().next()?;
    match message_ids(&fetched(first)) {
        Ok(text) => Some(text),
        Err(_) => raw_header(headers, name).into_iter().next(),
    }
}

/// `headers.header_chips`: one address header as composer chips, each address once whatever its case. A header the
/// parser raised on gives its bare addresses.
pub(super) fn header_chips(headers: &[Header], name: &str) -> Vec<String> {
    let parsed: Result<Vec<Vec<(String, String)>>, Fail> =
        raw_values(headers, name).into_iter().map(|raw| addresses(&fetched(raw))).collect();
    let pairs = match parsed {
        Ok(pairs) => pairs.into_iter().flatten().collect(),
        Err(_) => written_pairs(&raw_header(headers, name)),
    };
    let mut out = Vec::new();
    let mut seen = std::collections::HashSet::new();
    for (name, addr) in pairs {
        if !addr.contains('@') || !seen.insert(addr.to_lowercase()) {
            continue;
        }
        out.push(addr_chip(&name, &addr));
    }
    out
}

/// `header_chips`'s fallback: the addresses `email.utils.getaddresses` finds in the values as written, or failing
/// that, every word with an `@` in it. Comments nested past what Python's stack held raised there too.
fn written_pairs(raw: &[String]) -> Vec<(String, String)> {
    let values: Vec<&str> = raw.iter().map(String::as_str).collect();
    let found: Vec<(String, String)> = if values.iter().any(|value| nesting(value) > DEEPEST_COMMENT) {
        Vec::new()
    } else {
        getaddresses(&values).into_iter().map(|(_, addr)| (String::new(), addr)).collect()
    };
    if found.iter().any(|(_, addr)| addr.contains('@')) {
        return found;
    }
    raw.iter()
        .flat_map(|value| WRITTEN_ADDRESS.find_iter(value).map(|found| (String::new(), found.as_str().to_owned())))
        .collect()
}

/// How deep parentheses nest in a value, outside quotes and escapes.
fn nesting(value: &str) -> usize {
    let (mut depth, mut deepest, mut quoted, mut escaped) = (0usize, 0, false, false);
    for c in value.chars() {
        match c {
            _ if escaped => escaped = false,
            '\\' => escaped = true,
            '"' => quoted = !quoted,
            '(' if !quoted => {
                depth += 1;
                deepest = deepest.max(depth);
            }
            ')' if !quoted => depth = depth.saturating_sub(1),
            _ => {}
        }
    }
    deepest
}

/// Why a step of the parser gave up.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Fail {
    /// `HeaderParseError`: the step found something else, and its caller may try another.
    Parse,
    /// `_InvalidEwError`, a `HeaderParseError` of its own: what looked like an encoded word isn't one.
    InvalidWord,
    /// Any other exception: `header_chips` falls back to the values as written.
    Raised,
}

impl Fail {
    /// Whether `except errors.HeaderParseError` catches it.
    fn caught(self) -> bool {
        matches!(self, Fail::Parse | Fail::InvalidWord)
    }
}

/// What remains of a value: the parser's functions take a value and give back what they didn't read.
#[derive(Clone, Debug)]
struct Rest {
    text: Rc<str>,
    at: usize,
}

impl Rest {
    fn new(text: &str) -> Rest {
        Rest { text: Rc::from(text), at: 0 }
    }

    fn str(&self) -> &str {
        self.text.get(self.at..).unwrap_or_default()
    }

    fn is_empty(&self) -> bool {
        self.str().is_empty()
    }

    fn first(&self) -> Option<char> {
        self.str().chars().next()
    }

    /// Whether the value starts with a character `test` holds for: Python's `value and value[0] in ...`.
    fn starts(&self, test: impl Fn(char) -> bool) -> bool {
        self.first().is_some_and(test)
    }

    /// `value[0]`, which raises `IndexError` on an empty value.
    fn head(&self) -> Result<char, Fail> {
        self.first().ok_or(Fail::Raised)
    }

    /// `value[n:]`, by bytes: what follows a prefix already read.
    fn skip(&self, bytes: usize) -> Rest {
        Rest { text: Rc::clone(&self.text), at: (self.at + bytes).min(self.text.len()) }
    }

    /// `value[1:]`.
    fn skip_char(&self) -> Rest {
        self.skip(self.first().map_or(0, char::len_utf8))
    }
}

fn is_wsp(c: char) -> bool {
    c == ' ' || c == '\t'
}

fn is_special(c: char) -> bool {
    SPECIALS.contains(c)
}

/// `ATOM_ENDS`.
fn atom_end(c: char) -> bool {
    is_special(c) || is_wsp(c)
}

/// `PHRASE_ENDS`: the specials but `.`, `"` and `(`, which don't end a phrase.
fn phrase_end(c: char) -> bool {
    is_special(c) && !matches!(c, '.' | '"' | '(')
}

/// `CFWS_LEADER`.
fn cfws_leader(c: char) -> bool {
    is_wsp(c) || c == '('
}

/// What Python's `str.isspace` holds of a header's own text, where every byte past ASCII was a lone surrogate.
fn ascii_space(c: char) -> bool {
    c.is_ascii() && is_space(c)
}

/// What a token is: its class, and its `token_type` where the parser looks at it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Kind {
    /// A `ValueTerminal`.
    Value,
    /// The `DOT` terminal.
    Dot,
    /// A `WhiteSpaceTerminal`: blanks, or a comment's text.
    Space,
    /// An `EWWhiteSpaceTerminal`: blanks between two encoded words, which go.
    EwSpace,
    /// A `TokenList` with no properties of its own here.
    List,
    /// `CFWSList`.
    Cfws,
    Comment,
    BareQuoted,
    Quoted,
    EncodedWord,
    DotAtom,
    DisplayName,
    LocalPart,
    Domain,
    AddrSpec,
    AngleAddr,
    NameAddr,
    Mailbox,
    InvalidMailbox,
    Group,
    GroupList,
    MailboxList,
    Address,
}

/// A token of the parse tree: a terminal's text, or a list of tokens.
#[derive(Clone, Debug)]
struct Tok {
    kind: Kind,
    text: String,
    items: Vec<Tok>,
    /// Its `token_type` was set to `invalid-mailbox` after the fact, as a mailbox (or a group) with more after it is.
    marked: bool,
}

impl Tok {
    fn term(kind: Kind, text: impl Into<String>) -> Tok {
        Tok { kind, text: text.into(), items: Vec::new(), marked: false }
    }

    fn list(kind: Kind) -> Tok {
        Tok { kind, text: String::new(), items: Vec::new(), marked: false }
    }

    fn of(kind: Kind, items: Vec<Tok>) -> Tok {
        Tok { kind, text: String::new(), items, marked: false }
    }

    fn dot() -> Tok {
        Tok::term(Kind::Dot, ".")
    }

    fn is_list(&self) -> bool {
        !matches!(self.kind, Kind::Value | Kind::Dot | Kind::Space | Kind::EwSpace)
    }

    /// `token_type == 'fws'`.
    fn is_fws(&self) -> bool {
        matches!(self.kind, Kind::Space | Kind::EwSpace) && self.text.starts_with(is_wsp)
    }

    /// `token_type` is `mailbox` or `invalid-mailbox`.
    fn is_mailbox(&self) -> bool {
        matches!(self.kind, Kind::Mailbox | Kind::InvalidMailbox) || self.marked
    }

    /// `token[:0] = [leader]`.
    fn lead(&mut self, leader: Option<Tok>) {
        if let Some(leader) = leader {
            self.items.insert(0, leader);
        }
    }

    /// `str(token)`: the text as the parse tree writes it out.
    fn str(&self) -> String {
        match self.kind {
            Kind::Value | Kind::Dot | Kind::Space => self.text.clone(),
            Kind::EwSpace => String::new(),
            Kind::BareQuoted => quote_string(&self.items.iter().map(Tok::str).collect::<String>()),
            Kind::Comment => {
                let inner: String = self
                    .items
                    .iter()
                    .map(|item| match item.kind {
                        Kind::Comment => item.str(),
                        _ => item.str().replace('\\', "\\\\").replace('(', "\\(").replace(')', "\\)"),
                    })
                    .collect();
                format!("({inner})")
            }
            _ => self.items.iter().map(Tok::str).collect(),
        }
    }

    /// `token.value`: the text as an address's parts read it, a run of blanks or a comment as one space.
    fn value(&self) -> String {
        match self.kind {
            Kind::Value | Kind::Dot => self.text.clone(),
            Kind::Space | Kind::Cfws | Kind::Comment => " ".to_owned(),
            Kind::EwSpace => String::new(),
            Kind::BareQuoted => self.items.iter().map(Tok::str).collect(),
            _ => joined_values(&self.items),
        }
    }
}

/// `TokenList(items).value`.
fn joined_values(items: &[Tok]) -> String {
    items.iter().map(Tok::value).collect()
}

/// `quote_string`.
fn quote_string(value: &str) -> String {
    format!("\"{}\"", value.replace('\\', "\\\\").replace('"', "\\\""))
}

/// `_wsp_splitter(value, 1)`: the text up to the first blank, and the rest from there.
fn wsp_split(value: &str) -> (&str, &str) {
    value.split_at(value.find(is_wsp).unwrap_or(value.len()))
}

/// `get_unstructured`, as text: blanks as written, each encoded word decoded, and the blanks between two encoded
/// words gone.
pub(super) fn unstructured(value: &str) -> String {
    #[derive(PartialEq)]
    enum Piece {
        Blank,
        Word,
        Text,
    }
    let mut pieces: Vec<(Piece, String)> = Vec::new();
    let mut value = Rest::new(value);
    while !value.is_empty() {
        if value.starts(is_wsp) {
            let (blank, rest) = fws(&value, ascii_space);
            pieces.push((Piece::Blank, blank.text));
            value = rest;
            continue;
        }
        let mut valid = true;
        if value.str().starts_with("=?") {
            match encoded_word(&value) {
                Ok((word, rest)) => {
                    let after_blank = pieces.last().is_none_or(|(piece, _)| *piece == Piece::Blank);
                    if after_blank && pieces.len() > 1 && pieces[pieces.len() - 2].0 == Piece::Word {
                        let last = pieces.len() - 1;
                        pieces[last].1.clear();
                    }
                    pieces.push((Piece::Word, word.str()));
                    value = rest;
                    continue;
                }
                Err(Fail::InvalidWord) => valid = false,
                Err(_) => {}
            }
        }
        let text = value.str();
        let (mut token, _) = wsp_split(text);
        if valid && ENCODED_WORD.is_match(token) {
            let before = text.find("=?").unwrap_or(0);
            if before > 0 {
                token = &text[..before];
            }
        }
        let len = token.len().max(text.chars().next().map_or(0, char::len_utf8));
        pieces.push((Piece::Text, text[..len].to_owned()));
        value = value.skip(len);
    }
    pieces.into_iter().map(|(_, text)| text).collect()
}

/// `ContentTransferEncodingHeader.cte` (`parse_content_transfer_encoding_header`): the value's first token past the
/// comments and blanks before it, lowercase, or `7bit` where there is none.
pub(super) fn transfer_encoding(value: &str) -> String {
    const UNSAID: &str = "7bit";
    let mut parser = Parser { depth: 0 };
    let Ok((_, value)) = parser.leader(&Rest::new(value)) else { return UNSAID.to_owned() };
    let text = value.str();
    let token = &text[..text.find(|c: char| is_wsp(c) || TSPECIALS.contains(c)).unwrap_or(text.len())];
    if token.is_empty() { UNSAID.to_owned() } else { strip(token).to_lowercase() }
}

/// `get_fws`: the blanks a value starts with, and what Python's `lstrip()` takes with them. `space` says what
/// `lstrip()` takes: a header's own text held only ASCII; an encoded word's decoded text, any character.
fn fws(value: &Rest, space: fn(char) -> bool) -> (Tok, Rest) {
    let text = value.str();
    let rest = text.trim_start_matches(space);
    let len = text.len() - rest.len();
    (Tok::term(Kind::Space, &text[..len]), value.skip(len))
}

/// `get_encoded_word`: an encoded word, its decoded text split into blanks and words.
fn encoded_word(value: &Rest) -> Result<(Tok, Rest), Fail> {
    let text = value.str();
    let Some(inner) = text.strip_prefix("=?") else { return Err(Fail::Parse) };
    let Some(end) = inner.find("?=") else { return Err(Fail::Parse) };
    let (mut token, mut remainder) = (&inner[..end], &inner[end + 2..]);
    let hex = |c: Option<char>| c.is_some_and(|c| c.is_ascii_hexdigit());
    if hex(remainder.chars().next()) && hex(remainder.chars().nth(1)) && token.matches('?').count() < 2 {
        // The `?` after the encoding started an escape (`=3F`): the word ends at the next `?=`.
        let more = remainder.find("?=");
        token = &inner[..end + 2 + more.unwrap_or(remainder.len())];
        remainder = more.map_or("", |more| &remainder[more + 2..]);
    }
    let decoded = decode_word(token).ok_or(Fail::InvalidWord)?;
    let mut word = Tok::list(Kind::EncodedWord);
    let mut text = Rest::new(&decoded);
    while !text.is_empty() {
        if text.starts(is_wsp) {
            let (blank, rest) = fws(&text, is_space);
            word.items.push(blank);
            text = rest;
            continue;
        }
        let (chars, _) = wsp_split(text.str());
        word.items.push(Tok::term(Kind::Value, chars));
        text = text.skip(chars.len());
    }
    Ok((word, value.skip(text_len(value.str(), remainder))))
}

/// How much of `whole` comes before `tail`, which ends it.
fn text_len(whole: &str, tail: &str) -> usize {
    whole.len() - tail.len()
}

/// `_encoded_words.decode` of `charset?encoding?text`: None where Python raised (`ValueError`, `KeyError`).
fn decode_word(token: &str) -> Option<String> {
    let mut parts = token.split('?');
    let (Some(charset), Some(encoding), Some(text), None) = (parts.next(), parts.next(), parts.next(), parts.next())
    else {
        return None;
    };
    let charset = charset.split('*').next().unwrap_or_default();
    let bytes = match encoding.to_ascii_lowercase().as_str() {
        "q" => decode_q(text.as_bytes()),
        "b" => decode_b(text.as_bytes()),
        _ => return None,
    };
    Some(in_charset(charset, &bytes))
}

/// `_encoded_words.decode_q`: `_` a space, `=` and two hex digits a byte.
fn decode_q(text: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(text.len());
    let mut at = 0;
    while let Some(&byte) = text.get(at) {
        let pair = text.get(at + 1..at + 3).and_then(|pair| std::str::from_utf8(pair).ok());
        match (byte, pair.and_then(|pair| u8::from_str_radix(pair, 16).ok())) {
            (b'=', Some(decoded)) if pair.is_some_and(|pair| pair.bytes().all(|b| b.is_ascii_hexdigit())) => {
                out.push(decoded);
                at += 3;
            }
            (b'_', _) => {
                out.push(b' ');
                at += 1;
            }
            _ => {
                out.push(byte);
                at += 1;
            }
        }
    }
    out
}

/// `_encoded_words.decode_b`: strictly with the padding mended, else leniently, else with padding added, else the
/// text itself.
pub(super) fn decode_b(text: &[u8]) -> Vec<u8> {
    let missing = (4 - text.len() % 4) % 4;
    let padded = [text, &b"==="[..missing]].concat();
    a2b_base64(&padded, true)
        .or_else(|| a2b_base64(text, false))
        .or_else(|| a2b_base64(&[text, b"=="].concat(), false))
        .unwrap_or_else(|| text.to_vec())
}

/// `binascii.a2b_base64`, in its strict mode or not: None where it raised.
fn a2b_base64(text: &[u8], strict: bool) -> Option<Vec<u8>> {
    let sextet = |byte: u8| match byte {
        b'A'..=b'Z' => Some(byte - b'A'),
        b'a'..=b'z' => Some(byte - b'a' + 26),
        b'0'..=b'9' => Some(byte - b'0' + 52),
        b'+' => Some(62),
        b'/' => Some(63),
        _ => None,
    };
    let mut out = Vec::with_capacity(text.len() / 4 * 3);
    let (mut quad, mut pads, mut left, mut padding) = (0u8, 0u8, 0u8, false);
    for (at, &byte) in text.iter().enumerate() {
        if byte == b'=' {
            padding = true;
            if strict && quad == 0 {
                return None;
            }
            if quad >= 2 {
                pads += 1;
                if quad + pads >= 4 {
                    if strict && at + 1 < text.len() {
                        return None;
                    }
                    return Some(out);
                }
            }
            continue;
        }
        let Some(bits) = sextet(byte) else {
            if strict {
                return None;
            }
            continue;
        };
        if strict && padding {
            return None;
        }
        pads = 0;
        match quad {
            0 => {
                quad = 1;
                left = bits;
            }
            1 => {
                quad = 2;
                out.push(left << 2 | bits >> 4);
                left = bits & 0x0f;
            }
            2 => {
                quad = 3;
                out.push(left << 4 | bits >> 2);
                left = bits & 0x03;
            }
            _ => {
                quad = 0;
                out.push(left << 6 | bits);
                left = 0;
            }
        }
    }
    (quad == 0).then_some(out)
}

/// Bytes decoded in a charset as Python's `bytes.decode(charset)` read them and the header registry then mended them:
/// a byte that isn't the charset's, and every byte of a charset Python didn't know, read as UTF-8 at the end. So
/// ASCII reads as UTF-8 does.
pub(super) fn in_charset(label: &str, bytes: &[u8]) -> String {
    in_known_charset(label, bytes).unwrap_or_else(|| String::from_utf8_lossy(bytes).into_owned())
}

/// Bytes decoded in a charset as `in_charset` reads them, or None for a charset Python didn't know (`LookupError`).
pub(super) fn in_known_charset(label: &str, bytes: &[u8]) -> Option<String> {
    Some(match codec_name(label).as_str() {
        "latin_1" | "latin1" | "latin" | "l1" | "iso8859" | "iso8859_1" | "iso_8859_1" | "iso_8859_1_1987" | "8859"
        | "cp819" | "ibm819" | "iso_ir_100" | "csisolatin1" => bytes.iter().map(|&byte| char::from(byte)).collect(),
        "ascii" | "us_ascii" | "us" | "646" | "cp367" | "ibm367" | "csascii" | "iso646_us" | "iso_ir_6"
        | "ansi_x3.4_1968" | "ansi_x3_4_1968" | "ansi_x3.4_1986" | "iso_646.irv_1991" | "utf_8" | "utf8" | "u8"
        | "utf" | "cp65001" => String::from_utf8_lossy(bytes).into_owned(),
        _ => Charset::from_label(label.trim().as_bytes())?.decode(bytes).into_owned(),
    })
}

/// `encodings.normalize_encoding` of a lowered label: each run of other characters between letters, digits and
/// dots one `_`.
fn codec_name(label: &str) -> String {
    let mut name = String::new();
    let mut gap = false;
    for c in label.chars() {
        if c.is_alphanumeric() || c == '.' {
            if gap && !name.is_empty() {
                name.push('_');
            }
            if c.is_ascii() {
                name.push(c.to_ascii_lowercase());
            }
            gap = false;
        } else {
            gap = true;
        }
    }
    name
}

/// `AddressHeader.parse` and `.addresses` of one value: each address's (display name, addr-spec). Err where Python
/// raised: an address whose parts hold a line break once decoded (`ValueError`), comments nested past the stack, and
/// the parse tree's own `IndexError` and `AttributeError`.
fn addresses(value: &str) -> Result<Vec<(String, String)>, Fail> {
    let mut parser = Parser { depth: 0 };
    let (list, _) = parser.address_list(Rest::new(value))?;
    let mut out = Vec::new();
    for address in list.items.iter().filter(|item| item.kind == Kind::Address) {
        let first = address.items.first().ok_or(Fail::Raised)?;
        if first.kind == Kind::Group && !first.marked {
            display_name(first.items.first().ok_or(Fail::Raised)?)?;
        }
        for mailbox in all_mailboxes(address)? {
            let (name, local, domain) = mailbox_parts(mailbox)?;
            let (name, local, domain) =
                (name.unwrap_or_default(), local.unwrap_or_default(), domain.unwrap_or_default());
            if [&name, &local, &domain].iter().any(|part| part.contains(['\r', '\n'])) {
                return Err(Fail::Raised);
            }
            out.push((name, addr_spec(&local, &domain)));
        }
    }
    Ok(out)
}

/// `parse_message_ids` of a value, as text: each message ID as its token writes it out (with the `>` it may have
/// lacked), a comma as a space, and from the first that isn't one, the rest as `get_unstructured` reads it.
fn message_ids(value: &str) -> Result<String, Fail> {
    let mut parser = Parser { depth: 0 };
    let mut out = String::new();
    let mut value = Rest::new(value);
    while !value.is_empty() {
        if value.first() == Some(',') {
            out.push(' ');
            value = value.skip_char();
            continue;
        }
        match parser.msg_id(&value) {
            Ok((id, rest)) => {
                out.push_str(&id.str());
                value = rest;
            }
            Err(fail) if fail.caught() => {
                out.push_str(&unstructured(value.str()));
                break;
            }
            Err(fail) => return Err(fail),
        }
    }
    Ok(out)
}

/// `headerregistry.Address.addr_spec`.
fn addr_spec(local: &str, domain: &str) -> String {
    let local = if local.contains(|c: char| atom_end(c) && c != '.') { quote_string(local) } else { local.to_owned() };
    if !domain.is_empty() {
        return format!("{local}@{domain}");
    }
    if local.is_empty() { "<>".to_owned() } else { local }
}

/// `Address.all_mailboxes` of an address token.
fn all_mailboxes(address: &Tok) -> Result<Vec<&Tok>, Fail> {
    let first = address.items.first().ok_or(Fail::Raised)?;
    if first.is_mailbox() {
        return Ok(vec![first]);
    }
    if first.kind != Kind::Group {
        return Ok(Vec::new());
    }
    let members = first.items.get(2).ok_or(Fail::Raised)?;
    match members.items.first() {
        Some(list) if members.kind == Kind::GroupList && list.kind == Kind::MailboxList => {
            Ok(list.items.iter().filter(|item| item.is_mailbox()).collect())
        }
        _ => Ok(Vec::new()),
    }
}

/// A mailbox's (`display_name`, `local_part`, `domain`), each None where Python's was. A group read as a mailbox has
/// no local part: Python raised `AttributeError`.
type Parts = (Option<String>, Option<String>, Option<String>);

fn mailbox_parts(mailbox: &Tok) -> Result<Parts, Fail> {
    match mailbox.kind {
        Kind::InvalidMailbox => Ok((None, None, None)),
        Kind::Mailbox => {
            let first = mailbox.items.first().ok_or(Fail::Raised)?;
            let (name, spec) = match first.kind {
                Kind::NameAddr => {
                    let name = match first.items.len() {
                        1 => None,
                        _ => Some(display_name(first.items.first().ok_or(Fail::Raised)?)?),
                    };
                    let angle = first.items.last().ok_or(Fail::Raised)?;
                    (name, angle.items.iter().find(|item| item.kind == Kind::AddrSpec))
                }
                Kind::AddrSpec => (None, Some(first)),
                _ => (None, None),
            };
            let Some(spec) = spec else { return Ok((name, None, None)) };
            let local = local_part(spec.items.first().ok_or(Fail::Raised)?)?;
            let domain = match spec.items.len() {
                0..=2 => None,
                _ => spec.items.last().map(domain_of),
            };
            Ok((name, Some(local), domain))
        }
        _ => Err(Fail::Raised),
    }
}

/// `DisplayName.display_name`: its words' values, a comment or blanks at either end left out.
fn display_name(name: &Tok) -> Result<String, Fail> {
    let mut res = name.items.clone();
    if res.is_empty() {
        return Ok(String::new());
    }
    if res[0].kind == Kind::Cfws {
        res.remove(0);
    } else if res[0].is_list() && res[0].items.first().ok_or(Fail::Raised)?.kind == Kind::Cfws {
        res[0] = Tok::of(Kind::List, res[0].items[1..].to_vec());
    }
    let last = res.last().ok_or(Fail::Raised)?;
    if last.kind == Kind::Cfws {
        res.pop();
    } else if last.is_list() && last.items.last().ok_or(Fail::Raised)?.kind == Kind::Cfws {
        let kept = last.items[..last.items.len() - 1].to_vec();
        let end = res.len() - 1;
        res[end] = Tok::of(Kind::List, kept);
    }
    Ok(joined_values(&res))
}

/// `LocalPart.local_part`: its words and dots, blanks and comments gone from its ends and around its dots.
fn local_part(part: &Tok) -> Result<String, Fail> {
    let token = part.items.first().ok_or(Fail::Raised)?;
    let mut res = vec![Tok::dot()];
    let mut last_is_list = false;
    for tok in token.items.iter().cloned().chain(std::iter::once(Tok::dot())) {
        if tok.kind == Kind::Cfws {
            continue;
        }
        let last = res.last().ok_or(Fail::Raised)?;
        let last_is_dot = last.kind == Kind::Dot;
        if last_is_list && tok.kind == Kind::Dot && last.items.last().ok_or(Fail::Raised)?.kind == Kind::Cfws {
            let kept = last.items[..last.items.len() - 1].to_vec();
            let end = res.len() - 1;
            res[end] = Tok::of(Kind::List, kept);
        }
        let is_list = tok.is_list();
        if is_list && last_is_dot && tok.items.first().ok_or(Fail::Raised)?.kind == Kind::Cfws {
            res.push(Tok::of(Kind::List, tok.items[1..].to_vec()));
        } else {
            res.push(tok);
        }
        last_is_list = is_list;
    }
    Ok(joined_values(&res[1..res.len() - 1]))
}

/// `Domain.domain`: its value without blanks.
fn domain_of(domain: &Tok) -> String {
    domain.value().chars().filter(|c| !is_space(*c)).collect()
}

/// `email._header_value_parser`'s address-list functions, each giving its token and the rest of the value.
struct Parser {
    /// How deep comments nest where the parser is.
    depth: usize,
}

type Got = Result<(Tok, Rest), Fail>;

impl Parser {
    /// `_get_ptext_to_endchars`: the text up to a blank or one of `ends`, its quoted pairs read.
    fn ptext(value: &Rest, ends: &str) -> (String, Rest) {
        let text = value.str();
        let (fragment, _) = wsp_split(text);
        let mut chars = String::new();
        let mut escape = false;
        let mut stop = fragment.len();
        for (at, c) in fragment.char_indices() {
            if c == '\\' {
                if escape {
                    escape = false;
                } else {
                    escape = true;
                    continue;
                }
            }
            if escape {
                escape = false;
            } else if ends.contains(c) {
                stop = at;
                break;
            }
            chars.push(c);
        }
        (chars, value.skip(stop))
    }

    /// `get_qcontent`.
    fn qcontent(value: &Rest) -> (Tok, Rest) {
        let (text, rest) = Parser::ptext(value, "\"");
        (Tok::term(Kind::Value, text), rest)
    }

    /// `get_atext`.
    fn atext(value: &Rest) -> Got {
        let text = value.str();
        let len = text.find(atom_end).unwrap_or(text.len());
        if len == 0 {
            return Err(Fail::Parse);
        }
        Ok((Tok::term(Kind::Value, &text[..len]), value.skip(len)))
    }

    /// `get_bare_quoted_string`.
    fn bare_quoted_string(&mut self, value: &Rest) -> Got {
        if value.first() != Some('"') {
            return Err(Fail::Parse);
        }
        let mut quoted = Tok::list(Kind::BareQuoted);
        let mut value = value.skip_char();
        if value.first() == Some('"') {
            let (token, rest) = Parser::qcontent(&value);
            quoted.items.push(token);
            value = rest;
        }
        while value.starts(|c| c != '"') {
            let token;
            if value.starts(is_wsp) {
                (token, value) = fws(&value, ascii_space);
            } else if value.str().starts_with("=?") {
                match encoded_word(&value) {
                    Ok((word, rest)) => {
                        let n = quoted.items.len();
                        if n > 1 && quoted.items[n - 1].is_fws() && quoted.items[n - 2].kind == Kind::EncodedWord {
                            quoted.items[n - 1].kind = Kind::EwSpace;
                        }
                        token = word;
                        value = rest;
                    }
                    Err(fail) if fail.caught() => (token, value) = Parser::qcontent(&value),
                    Err(fail) => return Err(fail),
                }
            } else {
                (token, value) = Parser::qcontent(&value);
            }
            quoted.items.push(token);
        }
        if value.is_empty() {
            return Ok((quoted, value));
        }
        Ok((quoted, value.skip_char()))
    }

    /// `get_comment`, which recursed, as Python's stack allowed.
    fn comment(&mut self, value: &Rest) -> Got {
        if value.starts(|c| c != '(') {
            return Err(Fail::Parse);
        }
        self.depth += 1;
        if self.depth > DEEPEST_COMMENT {
            return Err(Fail::Raised);
        }
        let mut comment = Tok::list(Kind::Comment);
        let mut value = value.skip_char();
        while value.starts(|c| c != ')') {
            let token;
            if value.starts(is_wsp) {
                (token, value) = fws(&value, ascii_space);
            } else if value.first() == Some('(') {
                (token, value) = self.comment(&value)?;
            } else {
                let (text, rest) = Parser::ptext(&value, "()");
                (token, value) = (Tok::term(Kind::Space, text), rest);
            }
            comment.items.push(token);
        }
        self.depth -= 1;
        if value.is_empty() {
            return Ok((comment, value));
        }
        Ok((comment, value.skip_char()))
    }

    /// `get_cfws`.
    fn cfws(&mut self, value: &Rest) -> Got {
        let mut cfws = Tok::list(Kind::Cfws);
        let mut value = value.clone();
        while value.starts(cfws_leader) {
            let token;
            if value.starts(is_wsp) {
                (token, value) = fws(&value, ascii_space);
            } else {
                (token, value) = self.comment(&value)?;
            }
            cfws.items.push(token);
        }
        Ok((cfws, value))
    }

    /// `[CFWS]`: the comments and blanks a value starts with, if it starts with any.
    fn leader(&mut self, value: &Rest) -> Result<(Option<Tok>, Rest), Fail> {
        if value.starts(cfws_leader) {
            let (cfws, rest) = self.cfws(value)?;
            return Ok((Some(cfws), rest));
        }
        Ok((None, value.clone()))
    }

    /// `get_quoted_string`.
    fn quoted_string(&mut self, value: &Rest) -> Got {
        let mut quoted = Tok::list(Kind::Quoted);
        let (leader, value) = self.leader(value)?;
        quoted.lead(leader);
        let (bare, value) = self.bare_quoted_string(&value)?;
        quoted.items.push(bare);
        let (trailer, value) = self.leader(&value)?;
        quoted.items.extend(trailer);
        Ok((quoted, value))
    }

    /// An encoded word where one may stand, else what `fallback` reads (`get_atom`, `get_dot_atom`).
    fn word_or(&mut self, value: &Rest, fallback: fn(&mut Parser, &Rest) -> Got) -> Got {
        if value.str().starts_with("=?") {
            match encoded_word(value) {
                Ok(found) => return Ok(found),
                Err(fail) if fail.caught() => {}
                Err(fail) => return Err(fail),
            }
        }
        fallback(self, value)
    }

    /// `get_atom`.
    fn atom(&mut self, value: &Rest) -> Got {
        let mut atom = Tok::list(Kind::List);
        let (leader, value) = self.leader(value)?;
        atom.lead(leader);
        if value.starts(atom_end) {
            return Err(Fail::Parse);
        }
        let (token, value) = self.word_or(&value, |_, value| Parser::atext(value))?;
        atom.items.push(token);
        let (trailer, value) = self.leader(&value)?;
        atom.items.extend(trailer);
        Ok((atom, value))
    }

    /// `get_dot_atom_text`.
    fn dot_atom_text(value: &Rest) -> Got {
        let mut text = Tok::list(Kind::List);
        if !value.starts(|c| !atom_end(c)) {
            return Err(Fail::Parse);
        }
        let mut value = value.clone();
        while value.starts(|c| !atom_end(c)) {
            let (token, rest) = Parser::atext(&value)?;
            text.items.push(token);
            value = rest;
            if value.first() == Some('.') {
                text.items.push(Tok::dot());
                value = value.skip_char();
            }
        }
        if text.items.last().is_some_and(|last| last.kind == Kind::Dot) {
            return Err(Fail::Parse);
        }
        Ok((text, value))
    }

    /// `get_dot_atom`.
    fn dot_atom(&mut self, value: &Rest) -> Got {
        let mut atom = Tok::list(Kind::DotAtom);
        value.head()?;
        let (leader, value) = self.leader(value)?;
        atom.lead(leader);
        let (token, value) = self.word_or(&value, |_, value| Parser::dot_atom_text(value))?;
        atom.items.push(token);
        let (trailer, value) = self.leader(&value)?;
        atom.items.extend(trailer);
        Ok((atom, value))
    }

    /// `get_word`: an atom or a quoted string, with what comes before it.
    fn word(&mut self, value: &Rest) -> Got {
        value.head()?;
        let (leader, value) = self.leader(value)?;
        let Some(first) = value.first() else { return Err(Fail::Parse) };
        let (mut token, value) = if first == '"' {
            self.quoted_string(&value)?
        } else if is_special(first) {
            return Err(Fail::Parse);
        } else {
            self.atom(&value)?
        };
        token.lead(leader);
        Ok((token, value))
    }

    /// `get_phrase`.
    fn phrase(&mut self, value: &Rest) -> Got {
        let mut phrase = Tok::list(Kind::List);
        let mut value = value.clone();
        match self.word(&value) {
            Ok((token, rest)) => {
                phrase.items.push(token);
                value = rest;
            }
            Err(fail) if fail.caught() => {}
            Err(fail) => return Err(fail),
        }
        while value.starts(|c| !phrase_end(c)) {
            if value.first() == Some('.') {
                phrase.items.push(Tok::dot());
                value = value.skip_char();
                continue;
            }
            let token;
            match self.word(&value) {
                Ok(found) => (token, value) = found,
                Err(fail) if fail.caught() => {
                    if !value.starts(cfws_leader) {
                        return Err(fail);
                    }
                    (token, value) = self.cfws(&value)?;
                }
                Err(fail) => return Err(fail),
            }
            phrase.items.push(token);
        }
        Ok((phrase, value))
    }

    /// `get_local_part`.
    fn local_part(&mut self, value: &Rest) -> Got {
        let mut local = Tok::list(Kind::LocalPart);
        let (leader, value) = self.leader(value)?;
        let Some(first) = value.first() else { return Err(Fail::Parse) };
        let (mut token, mut value) = match self.dot_atom(&value) {
            Ok(found) => found,
            Err(fail) if fail.caught() => match self.word(&value) {
                Ok(found) => found,
                Err(fail) if fail.caught() => {
                    if first != '\\' && phrase_end(first) {
                        return Err(fail);
                    }
                    (Tok::list(Kind::List), value)
                }
                Err(fail) => return Err(fail),
            },
            Err(fail) => return Err(fail),
        };
        token.lead(leader);
        local.items.push(token);
        if value.starts(|c| c == '\\' || !phrase_end(c)) {
            let (obs, rest) = self.obs_local_part(&Rest::new(&(local.str() + value.str())))?;
            local.items[0] = obs;
            value = rest;
        }
        Ok((local, value))
    }

    /// `get_obs_local_part`.
    fn obs_local_part(&mut self, value: &Rest) -> Got {
        let mut obs = Tok::list(Kind::List);
        let mut value = value.clone();
        while value.starts(|c| c == '\\' || !phrase_end(c)) {
            match value.first() {
                Some('.') => {
                    obs.items.push(Tok::dot());
                    value = value.skip_char();
                    continue;
                }
                Some('\\') => {
                    obs.items.push(Tok::term(Kind::Value, "\\"));
                    value = value.skip_char();
                    continue;
                }
                _ => {}
            }
            let token;
            match self.word(&value) {
                Ok(found) => (token, value) = found,
                Err(fail) if fail.caught() => {
                    if !value.starts(cfws_leader) {
                        return Err(fail);
                    }
                    (token, value) = self.cfws(&value)?;
                }
                Err(fail) => return Err(fail),
            }
            obs.items.push(token);
        }
        if obs.items.is_empty() {
            return Err(Fail::Parse);
        }
        Ok((obs, value))
    }

    /// `get_domain_literal`.
    fn domain_literal(&mut self, value: &Rest) -> Got {
        let mut literal = Tok::list(Kind::List);
        value.head()?;
        let (leader, value) = self.leader(value)?;
        literal.lead(leader);
        if value.first() != Some('[') {
            return Err(Fail::Parse);
        }
        let mut value = value.skip_char();
        literal.items.push(Tok::term(Kind::Value, "["));
        let end = |literal: &mut Tok, value: &Rest| {
            if value.is_empty() {
                literal.items.push(Tok::term(Kind::Value, "]"));
            }
            value.is_empty()
        };
        if end(&mut literal, &value) {
            return Ok((literal, value));
        }
        if value.starts(is_wsp) {
            let (blank, rest) = fws(&value, ascii_space);
            literal.items.push(blank);
            value = rest;
        }
        let (text, rest) = Parser::ptext(&value, "[]");
        literal.items.push(Tok::term(Kind::Value, text));
        value = rest;
        if end(&mut literal, &value) {
            return Ok((literal, value));
        }
        if value.starts(is_wsp) {
            let (blank, rest) = fws(&value, ascii_space);
            literal.items.push(blank);
            value = rest;
        }
        if end(&mut literal, &value) {
            return Ok((literal, value));
        }
        if value.first() != Some(']') {
            return Err(Fail::Parse);
        }
        literal.items.push(Tok::term(Kind::Value, "]"));
        let (trailer, value) = self.leader(&value.skip_char())?;
        literal.items.extend(trailer);
        Ok((literal, value))
    }

    /// `get_domain`.
    fn domain(&mut self, value: &Rest) -> Got {
        let mut domain = Tok::list(Kind::Domain);
        let (leader, value) = self.leader(value)?;
        let Some(first) = value.first() else { return Err(Fail::Parse) };
        if first == '[' {
            let (mut token, value) = self.domain_literal(&value)?;
            token.lead(leader);
            domain.items.push(token);
            return Ok((domain, value));
        }
        let (mut token, mut value) = match self.dot_atom(&value) {
            Ok(found) => found,
            Err(fail) if fail.caught() => self.atom(&value)?,
            Err(fail) => return Err(fail),
        };
        if value.first() == Some('@') {
            return Err(Fail::Parse);
        }
        token.lead(leader);
        domain.items.push(token);
        if value.first() == Some('.') {
            if domain.items[0].kind == Kind::DotAtom {
                domain.items = domain.items.remove(0).items;
            }
            while value.first() == Some('.') {
                domain.items.push(Tok::dot());
                let (atom, rest) = self.atom(&value.skip_char())?;
                domain.items.push(atom);
                value = rest;
            }
        }
        Ok((domain, value))
    }

    /// `get_no_fold_literal`.
    fn no_fold_literal(value: &Rest) -> Got {
        if value.first() != Some('[') {
            return Err(Fail::Parse);
        }
        let mut literal = Tok::list(Kind::List);
        literal.items.push(Tok::term(Kind::Value, "["));
        let (text, value) = Parser::ptext(&value.skip_char(), "[]");
        literal.items.push(Tok::term(Kind::Value, text));
        if value.first() != Some(']') {
            return Err(Fail::Parse);
        }
        literal.items.push(Tok::term(Kind::Value, "]"));
        Ok((literal, value.skip_char()))
    }

    /// `get_msg_id`: `<`, the id's left, `@` and right, and `>`, which it writes out whether or not the value had it.
    fn msg_id(&mut self, value: &Rest) -> Got {
        let mut id = Tok::list(Kind::List);
        let (leader, value) = self.leader(value)?;
        id.lead(leader);
        if value.first() != Some('<') {
            return Err(Fail::Parse);
        }
        id.items.push(Tok::term(Kind::Value, "<"));
        let value = value.skip_char();
        let (left, value) = match Parser::dot_atom_text(&value) {
            Ok(found) => found,
            Err(fail) if fail.caught() => match self.obs_local_part(&value) {
                Ok(found) => found,
                Err(fail) if fail.caught() => return Err(Fail::Parse),
                Err(fail) => return Err(fail),
            },
            Err(fail) => return Err(fail),
        };
        id.items.push(left);
        if value.first() != Some('@') {
            if value.first() == Some('>') {
                id.items.push(Tok::term(Kind::Value, ">"));
                return Ok((id, value.skip_char()));
            }
            return Ok((id, value));
        }
        id.items.push(Tok::term(Kind::Value, "@"));
        let value = value.skip_char();
        let (right, mut value) = match Parser::dot_atom_text(&value) {
            Ok(found) => found,
            Err(fail) if fail.caught() => match Parser::no_fold_literal(&value) {
                Ok(found) => found,
                Err(fail) if fail.caught() => match self.domain(&value) {
                    Ok(found) => found,
                    Err(fail) if fail.caught() => return Err(Fail::Parse),
                    Err(fail) => return Err(fail),
                },
                Err(fail) => return Err(fail),
            },
            Err(fail) => return Err(fail),
        };
        id.items.push(right);
        if value.first() == Some('>') {
            value = value.skip_char();
        }
        id.items.push(Tok::term(Kind::Value, ">"));
        let (trailer, value) = self.leader(&value)?;
        id.items.extend(trailer);
        Ok((id, value))
    }

    /// `get_addr_spec`.
    fn addr_spec(&mut self, value: &Rest) -> Got {
        let mut spec = Tok::list(Kind::AddrSpec);
        let (local, value) = self.local_part(value)?;
        spec.items.push(local);
        if value.first() != Some('@') {
            return Ok((spec, value));
        }
        spec.items.push(Tok::term(Kind::Value, "@"));
        let (domain, value) = self.domain(&value.skip_char())?;
        spec.items.push(domain);
        Ok((spec, value))
    }

    /// `get_obs_route`.
    fn obs_route(&mut self, value: &Rest) -> Got {
        let mut route = Tok::list(Kind::List);
        let mut value = value.clone();
        while value.starts(|c| c == ',' || cfws_leader(c)) {
            if value.starts(cfws_leader) {
                let (cfws, rest) = self.cfws(&value)?;
                route.items.push(cfws);
                value = rest;
            } else {
                route.items.push(Tok::term(Kind::Value, ","));
                value = value.skip_char();
            }
        }
        if value.first() != Some('@') {
            return Err(Fail::Parse);
        }
        route.items.push(Tok::term(Kind::Value, "@"));
        let (domain, rest) = self.domain(&value.skip_char())?;
        route.items.push(domain);
        value = rest;
        while value.first() == Some(',') {
            route.items.push(Tok::term(Kind::Value, ","));
            value = value.skip_char();
            if value.is_empty() {
                break;
            }
            if value.starts(cfws_leader) {
                let (cfws, rest) = self.cfws(&value)?;
                route.items.push(cfws);
                value = rest;
            }
            if value.is_empty() {
                break;
            }
            if value.first() == Some('@') {
                route.items.push(Tok::term(Kind::Value, "@"));
                let (domain, rest) = self.domain(&value.skip_char())?;
                route.items.push(domain);
                value = rest;
            }
        }
        if value.first() != Some(':') {
            return Err(Fail::Parse);
        }
        route.items.push(Tok::term(Kind::Value, ":"));
        Ok((route, value.skip_char()))
    }

    /// `get_angle_addr`.
    fn angle_addr(&mut self, value: &Rest) -> Got {
        let mut angle = Tok::list(Kind::AngleAddr);
        let (leader, value) = self.leader(value)?;
        angle.lead(leader);
        if value.first() != Some('<') {
            return Err(Fail::Parse);
        }
        angle.items.push(Tok::term(Kind::Value, "<"));
        let value = value.skip_char();
        if value.first() == Some('>') {
            angle.items.push(Tok::term(Kind::Value, ">"));
            return Ok((angle, value.skip_char()));
        }
        let (spec, mut value) = match self.addr_spec(&value) {
            Ok(found) => found,
            Err(fail) if fail.caught() => {
                let (route, rest) = match self.obs_route(&value) {
                    Ok(found) => found,
                    Err(fail) if fail.caught() => return Err(Fail::Parse),
                    Err(fail) => return Err(fail),
                };
                angle.items.push(route);
                self.addr_spec(&rest)?
            }
            Err(fail) => return Err(fail),
        };
        angle.items.push(spec);
        if value.first() == Some('>') {
            value = value.skip_char();
        }
        angle.items.push(Tok::term(Kind::Value, ">"));
        let (trailer, value) = self.leader(&value)?;
        angle.items.extend(trailer);
        Ok((angle, value))
    }

    /// `get_display_name`.
    fn display_name(&mut self, value: &Rest) -> Got {
        let (phrase, value) = self.phrase(value)?;
        Ok((Tok::of(Kind::DisplayName, phrase.items), value))
    }

    /// `get_name_addr`.
    fn name_addr(&mut self, value: &Rest) -> Got {
        let mut name_addr = Tok::list(Kind::NameAddr);
        if value.is_empty() {
            return Err(Fail::Parse);
        }
        let (mut leader, mut value) = self.leader(value)?;
        let Some(first) = value.first() else { return Err(Fail::Parse) };
        if first != '<' {
            if phrase_end(first) {
                return Err(Fail::Parse);
            }
            let (mut name, rest) = self.display_name(&value)?;
            if rest.is_empty() {
                return Err(Fail::Parse);
            }
            if let Some(leader) = leader.take() {
                let head = name.items.first_mut().ok_or(Fail::Raised)?;
                if head.is_list() {
                    head.items.insert(0, leader);
                } else {
                    name.items.insert(0, leader);
                }
            }
            name_addr.items.push(name);
            value = rest;
        }
        let (mut angle, value) = self.angle_addr(&value)?;
        angle.lead(leader);
        name_addr.items.push(angle);
        Ok((name_addr, value))
    }

    /// `get_mailbox`.
    fn mailbox(&mut self, value: &Rest) -> Got {
        let (token, value) = match self.name_addr(value) {
            Ok(found) => found,
            Err(fail) if fail.caught() => match self.addr_spec(value) {
                Ok(found) => found,
                Err(fail) if fail.caught() => return Err(Fail::Parse),
                Err(fail) => return Err(fail),
            },
            Err(fail) => return Err(fail),
        };
        Ok((Tok::of(Kind::Mailbox, vec![token]), value))
    }

    /// `get_invalid_mailbox`: everything up to one of `ends`.
    fn invalid_mailbox(&mut self, value: &Rest, ends: &str) -> Got {
        let mut invalid = Tok::list(Kind::InvalidMailbox);
        let mut value = value.clone();
        while value.starts(|c| !ends.contains(c)) {
            let first = value.head()?;
            if phrase_end(first) {
                invalid.items.push(Tok::term(Kind::Value, first));
                value = value.skip_char();
            } else {
                let (phrase, rest) = self.phrase(&value)?;
                if rest.at == value.at && Rc::ptr_eq(&rest.text, &value.text) {
                    return Err(Fail::Raised);
                }
                invalid.items.push(phrase);
                value = rest;
            }
        }
        Ok((invalid, value))
    }

    /// A list's entry that isn't an address (`get_mailbox_list`'s and `get_address_list`'s `except`): a leader alone,
    /// nothing, or an invalid mailbox, which `wrap` puts in the list.
    fn not_an_entry(&mut self, list: &mut Tok, value: Rest, ends: &str, wrap: fn(Tok) -> Tok) -> Result<Rest, Fail> {
        let first = value.head()?;
        if cfws_leader(first) {
            let (leader, value) = self.cfws(&value)?;
            if value.starts(|c| !ends.contains(c)) {
                let (mut invalid, rest) = self.invalid_mailbox(&value, ends)?;
                invalid.lead(Some(leader));
                list.items.push(wrap(invalid));
                return Ok(rest);
            }
            list.items.push(leader);
            return Ok(value);
        }
        if first == ',' {
            return Ok(value);
        }
        let (invalid, rest) = self.invalid_mailbox(&value, ends)?;
        list.items.push(wrap(invalid));
        Ok(rest)
    }

    /// After an entry: anything before the next of `ends` joins the last entry, which is marked invalid
    /// (`mailbox.token_type = 'invalid-mailbox'` and `mailbox.extend(token)`).
    fn crap_after(&mut self, last: Option<&mut Tok>, value: Rest, ends: &str) -> Result<Rest, Fail> {
        if !value.starts(|c| !ends.contains(c)) {
            return Ok(value);
        }
        let last = last.ok_or(Fail::Raised)?;
        if !last.is_list() {
            return Err(Fail::Raised);
        }
        last.marked = true;
        let (invalid, rest) = self.invalid_mailbox(&value, ends)?;
        last.items.extend(invalid.items);
        Ok(rest)
    }

    /// `get_mailbox_list`, which ends at a `;` or at the end.
    fn mailbox_list(&mut self, value: &Rest) -> Got {
        let mut list = Tok::list(Kind::MailboxList);
        let mut value = value.clone();
        while value.starts(|c| c != ';') {
            value = match self.mailbox(&value) {
                Ok((mailbox, rest)) => {
                    list.items.push(mailbox);
                    rest
                }
                Err(fail) if fail.caught() => self.not_an_entry(&mut list, value, ",;", |invalid| invalid)?,
                Err(fail) => return Err(fail),
            };
            value = self.crap_after(list.items.last_mut(), value, ",;")?;
            if value.first() == Some(',') {
                list.items.push(Tok::term(Kind::Value, ","));
                value = value.skip_char();
            }
        }
        Ok((list, value))
    }

    /// `get_group_list`.
    fn group_list(&mut self, value: &Rest) -> Got {
        let mut group_list = Tok::list(Kind::GroupList);
        if value.is_empty() {
            return Ok((group_list, value.clone()));
        }
        let (leader, value) = self.leader(value)?;
        if leader.is_some() && (value.is_empty() || value.first() == Some(';')) {
            group_list.lead(leader);
            return Ok((group_list, value));
        }
        let (mut list, value) = self.mailbox_list(&value)?;
        if !list.items.iter().any(Tok::is_mailbox) {
            group_list.items.extend(leader);
            group_list.items.extend(list.items);
            return Ok((group_list, value));
        }
        list.lead(leader);
        group_list.items.push(list);
        Ok((group_list, value))
    }

    /// `get_group`.
    fn group(&mut self, value: &Rest) -> Got {
        let mut group = Tok::list(Kind::Group);
        let (name, value) = self.display_name(value)?;
        if value.first() != Some(':') {
            return Err(Fail::Parse);
        }
        group.items.push(name);
        group.items.push(Tok::term(Kind::Value, ":"));
        let value = value.skip_char();
        if value.first() == Some(';') {
            group.items.push(Tok::term(Kind::Value, ";"));
            return Ok((group, value.skip_char()));
        }
        let (list, value) = self.group_list(&value)?;
        group.items.push(list);
        if value.starts(|c| c != ';') {
            return Err(Fail::Parse);
        }
        group.items.push(Tok::term(Kind::Value, ";"));
        let (trailer, value) = self.leader(&value.skip_char())?;
        group.items.extend(trailer);
        Ok((group, value))
    }

    /// `get_address`.
    fn address(&mut self, value: &Rest) -> Got {
        let (token, value) = match self.group(value) {
            Ok(found) => found,
            Err(fail) if fail.caught() => match self.mailbox(value) {
                Ok(found) => found,
                Err(fail) if fail.caught() => return Err(Fail::Parse),
                Err(fail) => return Err(fail),
            },
            Err(fail) => return Err(fail),
        };
        Ok((Tok::of(Kind::Address, vec![token]), value))
    }

    /// `get_address_list`, to the end of the value.
    fn address_list(&mut self, value: Rest) -> Got {
        let mut list = Tok::list(Kind::List);
        let mut value = value;
        while !value.is_empty() {
            value = match self.address(&value) {
                Ok((address, rest)) => {
                    list.items.push(address);
                    rest
                }
                Err(fail) if fail.caught() => {
                    self.not_an_entry(&mut list, value, ",", |invalid| Tok::of(Kind::Address, vec![invalid]))?
                }
                Err(fail) => return Err(fail),
            };
            let last = list.items.last_mut().and_then(|address| address.items.first_mut());
            value = self.crap_after(last, value, ",")?;
            if !value.is_empty() {
                list.items.push(Tok::term(Kind::Value, ","));
                value = value.skip_char();
            }
        }
        Ok((list, value))
    }
}

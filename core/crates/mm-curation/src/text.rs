//! Text as the Python app read it. The classifier's tables were written against Python's `str` and `re`, and the
//! buckets they gave are stored, so what's here answers as Python 3.14 does, character for character.
//!
//! Python 3.14 knows Unicode 16.0, and so do the `regex` crate's tables, which answer every question here about what
//! a character is. Rust's own `char::to_lowercase` knows a later Unicode, so a character Python doesn't know yet keeps
//! its case, as it does there.

use std::borrow::Cow;
use std::sync::LazyLock;

use regex::Regex;

/// Where Python's `\b` stands before a word: at the start, or after a character Python's `\w` doesn't match. Python's
/// `\w` is a letter, a number or `_`; the `regex` crate's own `\w` and `\b` count marks and connector punctuation
/// too. Case is never ignored in it, which would add the marks that fold to a letter.
macro_rules! word_starts {
    () => {
        r"(?-i:\A|[^\p{L}\p{N}_])"
    };
}

/// Where Python's `\b` stands after a word: at the end, or before a character Python's `\w` doesn't match.
macro_rules! word_ends {
    () => {
        r"(?-i:\z|[^\p{L}\p{N}_])"
    };
}

/// Python's `\w`: a letter, a number or `_`.
macro_rules! word {
    () => {
        r"(?-i:[\p{L}\p{N}_])"
    };
}

/// Python's `\s`: Unicode's White_Space, and the four information separators U+001C to U+001F, which Python counts
/// as well.
macro_rules! space {
    () => {
        r"(?-i:[\s\x1C-\x1F])"
    };
}

pub(crate) use {space, word, word_ends, word_starts};

/// A pattern written in this crate, which compiles.
pub(crate) fn pattern(source: &str) -> Regex {
    Regex::new(source).expect("a pattern written in this crate compiles")
}

/// Whether Python's `str.isspace` holds of `c`, which `strip` goes by: Unicode's White_Space, which Rust's
/// `is_whitespace` is, and the four information separators U+001C to U+001F, which Python counts as well.
pub(crate) fn is_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// Python's `s.strip()`.
pub(crate) fn strip(s: &str) -> &str {
    s.trim_matches(is_space)
}

/// Python's `s.lower()`: each character's full lowercase mapping, and a capital sigma as Python reads where it
/// stands. A character Python's Unicode doesn't know stays as it is.
pub(crate) fn lower(s: &str) -> Cow<'_, str> {
    if s.is_ascii() {
        return if s.bytes().any(|b| b.is_ascii_uppercase()) {
            Cow::Owned(s.to_ascii_lowercase())
        } else {
            Cow::Borrowed(s)
        };
    }
    let mut out = String::with_capacity(s.len());
    for (at, c) in s.char_indices() {
        if c == 'Σ' {
            out.push(if final_sigma(s, at) { 'ς' } else { 'σ' });
        } else if c.to_lowercase().eq([c]) || !KNOWN_TO_PYTHON.is_match(c.encode_utf8(&mut [0; 4])) {
            out.push(c);
        } else {
            out.extend(c.to_lowercase());
        }
    }
    Cow::Owned(out)
}

/// Whether the capital sigma at byte `at` ends a word, as Python decides it: a cased letter comes before it, past any
/// case-ignorable characters, and none comes after it.
fn final_sigma(s: &str, at: usize) -> bool {
    CASED_BEFORE.is_match(&s[..at]) && !CASED_AFTER.is_match(&s[at + 'Σ'.len_utf8()..])
}

static CASED_BEFORE: LazyLock<Regex> =
    LazyLock::new(|| pattern(r"[\p{Cased}--\p{Case_Ignorable}]\p{Case_Ignorable}*\z"));
static CASED_AFTER: LazyLock<Regex> =
    LazyLock::new(|| pattern(r"\A\p{Case_Ignorable}*[\p{Cased}--\p{Case_Ignorable}]"));

/// A character assigned by Unicode 16.0, the one Python 3.14 knows.
static KNOWN_TO_PYTHON: LazyLock<Regex> = LazyLock::new(|| pattern(r"\A\p{Age=16.0}"));

/// How many characters of `s` Python's `str.isdigit` holds of: a decimal digit (Nd), or one Unicode gives a digit
/// value without making it decimal, as `²` and `①` are. The list is Python 3.14's, which no `regex` class names.
pub(crate) fn digits(s: &str) -> usize {
    if s.is_ascii() { s.bytes().filter(u8::is_ascii_digit).count() } else { DIGIT.find_iter(s).count() }
}

static DIGIT: LazyLock<Regex> = LazyLock::new(|| {
    pattern(concat!(
        r"[\p{Nd}\x{B2}\x{B3}\x{B9}\x{1369}-\x{1371}\x{19DA}\x{2070}\x{2074}-\x{2079}\x{2080}-\x{2089}",
        r"\x{2460}-\x{2468}\x{2474}-\x{247C}\x{2488}-\x{2490}\x{24EA}\x{24F5}-\x{24FD}\x{24FF}",
        r"\x{2776}-\x{277E}\x{2780}-\x{2788}\x{278A}-\x{2792}\x{10A40}-\x{10A43}\x{10E60}-\x{10E68}",
        r"\x{11052}-\x{1105A}\x{1F100}-\x{1F10A}]",
    ))
});

/// `s` as a pattern that ignores case reads it. Python's `re.IGNORECASE` matches `i` with `İ` and `ı` too, as their
/// simple case mappings lead there, and the `regex` crate's case folding doesn't. So each is written `i`, which
/// changes nothing else a pattern here asks of it: all three are letters, one character each.
pub(crate) fn i_folded(s: &str) -> Cow<'_, str> {
    if s.contains(['\u{130}', '\u{131}']) {
        Cow::Owned(s.replace(['\u{130}', '\u{131}'], "i"))
    } else {
        Cow::Borrowed(s)
    }
}

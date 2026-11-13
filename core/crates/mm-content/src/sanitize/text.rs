//! Text as the Python app read it. The sanitizer's checks were written against Python's `str` and `re`, and the
//! core answers as they did, character for character, so a message renders as it did before the move.
//!
//! Python 3.14 knows Unicode 16.0, and so do the `regex` crate's tables, which answer every question here about what
//! a character is. Rust's own `char::to_lowercase` knows a later Unicode, so a character Python doesn't know yet keeps
//! its case, as it does there. This is mm-curation's reading of Python, which the context crates can't share.

use std::borrow::Cow;
use std::sync::LazyLock;

use regex::Regex;

/// Python's `\s`: Unicode's White_Space, and the four information separators U+001C to U+001F, which Python counts
/// as well. Case is never ignored in it.
macro_rules! space {
    () => {
        r"(?-i:[\s\x1C-\x1F])"
    };
}

pub(crate) use space;

/// A pattern written in this crate, which compiles.
pub(crate) fn pattern(source: &str) -> Regex {
    Regex::new(source).expect("a pattern written in this crate compiles")
}

/// Whether Python's `str.isspace` holds of `c`, which `strip` and `\s` go by: Unicode's White_Space, which Rust's
/// `is_whitespace` is, and the four information separators U+001C to U+001F, which Python counts as well.
pub(crate) fn is_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// Python's `s.strip()`.
pub(crate) fn strip(s: &str) -> &str {
    s.trim_matches(is_space)
}

/// Whether `c` is one the sanitizer strips or reads as a space wherever a scheme could hide behind it: the C0
/// controls, the space, a no-break space and the line and paragraph separators (`[\x00-\x20\xa0  ]`).
pub(crate) fn is_control(c: char) -> bool {
    matches!(c, '\0'..=' ' | '\u{a0}' | '\u{2028}' | '\u{2029}')
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

/// `s` as a pattern that ignores case reads it. Python's `re.IGNORECASE` matches `i` with `İ` and `ı` too, as their
/// simple case mappings lead there, and the `regex` crate's case folding doesn't. So each is written `i`, which
/// changes nothing else a pattern here asks of it: all three are letters, one character each. The two crates agree
/// on the other two Python names, `ſ` for `s` and the Kelvin sign for `k`.
pub(crate) fn i_folded(s: &str) -> Cow<'_, str> {
    if s.contains(['\u{130}', '\u{131}']) {
        Cow::Owned(s.replace(['\u{130}', '\u{131}'], "i"))
    } else {
        Cow::Borrowed(s)
    }
}

/// Whether `c` matches the ASCII letter `letter` (written small) where Python's `re.IGNORECASE` reads it: in either
/// case, and as the four other characters Python's documentation names (`İ` and `ı` for `i`, `ſ` for `s`, the Kelvin
/// sign for `k`).
pub(crate) fn is_letter_ignoring_case(c: char, letter: char) -> bool {
    c.to_ascii_lowercase() == letter
        || match letter {
            'i' => matches!(c, '\u{130}' | '\u{131}'),
            's' => c == '\u{17f}',
            'k' => c == '\u{212a}',
            _ => false,
        }
}

/// Whether Python's `[a-z]` matches `c` when case is ignored: the 52 ASCII letters and those four.
pub(crate) fn is_ascii_letter_ignoring_case(c: char) -> bool {
    c.is_ascii_alphabetic() || matches!(c, '\u{130}' | '\u{131}' | '\u{17f}' | '\u{212a}')
}

/// The first `n` characters of `s`, as Python's `s[:n]` counts them.
pub(crate) fn prefix(s: &str, n: usize) -> &str {
    s.char_indices().nth(n).map_or(s, |(at, _)| &s[..at])
}

/// How many characters Python's `len` counts in `s`.
pub(crate) fn length(s: &str) -> usize {
    s.chars().count()
}

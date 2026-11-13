//! Text as Python's `str` read it: what `strip()` and `split()` took for space, and what `lower()` made of a word.
//!
//! The grammar was the Python app's, and a saved search or a filter written then has to read the same now. Rust
//! differs from Python in two places. It doesn't count the information separators U+001C to U+001F as space. And
//! its standard library knows Unicode 17, where Python 3.14 knows Unicode 16: a character Unicode 17 added has no
//! case in Python, and U+0295 was still a lowercase letter.

/// Whether Python's `str.split()` splits at `c`: Unicode's White_Space, which Rust's `is_whitespace` is, and the
/// four information separators U+001C to U+001F, which Python counts as well.
pub fn is_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// Python's `str.lower()`: each character's full lowercase mapping, and a capital sigma that ends a word as a final
/// sigma.
pub(crate) fn lower(text: &str) -> String {
    if text.is_ascii() {
        return text.to_ascii_lowercase();
    }
    let chars: Vec<char> = text.chars().collect();
    let mut out = String::with_capacity(text.len());
    for (i, &c) in chars.iter().enumerate() {
        if c == CAPITAL_SIGMA {
            out.push(if ends_a_word(&chars, i) { '\u{3c2}' } else { '\u{3c3}' });
        } else if UNICODE_17_LOWERCASE.iter().any(|range| range.contains(&c)) {
            out.push(c);
        } else {
            out.extend(c.to_lowercase());
        }
    }
    out
}

const CAPITAL_SIGMA: char = '\u{3a3}';

/// Python's Final_Sigma: a cased character before the sigma and none after it, past any case-ignorable ones.
fn ends_a_word(chars: &[char], at: usize) -> bool {
    let before = chars[..at].iter().rev().map(|&c| casing(c)).find(|&k| k != Casing::Ignorable);
    let after = chars[at + 1..].iter().map(|&c| casing(c)).find(|&k| k != Casing::Ignorable);
    before == Some(Casing::Cased) && after != Some(Casing::Cased)
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Casing {
    Cased,
    Ignorable,
    Neither,
}

/// Which of Unicode's Cased and Case_Ignorable a character is, as Python 3.14 has them. Rust keeps both properties
/// to itself, but its own lowercasing of a capital sigma after the character shows them.
fn casing(c: char) -> Casing {
    // A lowercase letter to Python 3.14, which Unicode 17 made a letter with no case.
    if c == '\u{295}' {
        return Casing::Cased;
    }
    if UNICODE_17_CASING.iter().any(|range| range.contains(&c)) {
        return Casing::Neither;
    }
    let mut probe = String::with_capacity(8);
    probe.push(c);
    probe.push(CAPITAL_SIGMA);
    if probe.to_lowercase().ends_with('\u{3c2}') {
        return Casing::Cased;
    }
    probe.insert(0, 'A');
    if probe.to_lowercase().ends_with('\u{3c2}') { Casing::Ignorable } else { Casing::Neither }
}

/// Characters Unicode 17 added with a lowercase mapping. Python 3.14 has no such characters, so it leaves them be.
const UNICODE_17_LOWERCASE: [std::ops::RangeInclusive<char>; 4] =
    ['\u{a7ce}'..='\u{a7ce}', '\u{a7d2}'..='\u{a7d2}', '\u{a7d4}'..='\u{a7d4}', '\u{16ea0}'..='\u{16eb8}'];

/// Characters Unicode 17 added that are Cased or Case_Ignorable. To Python 3.14 they are neither.
const UNICODE_17_CASING: [std::ops::RangeInclusive<char>; 20] = [
    '\u{1acf}'..='\u{1add}',
    '\u{1ae0}'..='\u{1aeb}',
    '\u{a7ce}'..='\u{a7cf}',
    '\u{a7d2}'..='\u{a7d2}',
    '\u{a7d4}'..='\u{a7d4}',
    '\u{a7f1}'..='\u{a7f1}',
    '\u{10ec5}'..='\u{10ec5}',
    '\u{10efa}'..='\u{10efb}',
    '\u{11b60}'..='\u{11b60}',
    '\u{11b62}'..='\u{11b64}',
    '\u{11b66}'..='\u{11b66}',
    '\u{11dd9}'..='\u{11dd9}',
    '\u{16ea0}'..='\u{16eb8}',
    '\u{16ebb}'..='\u{16ed3}',
    '\u{16ff2}'..='\u{16ff3}',
    '\u{1e6e3}'..='\u{1e6e3}',
    '\u{1e6e6}'..='\u{1e6e6}',
    '\u{1e6ee}'..='\u{1e6ef}',
    '\u{1e6f5}'..='\u{1e6f5}',
    '\u{1e6ff}'..='\u{1e6ff}',
];

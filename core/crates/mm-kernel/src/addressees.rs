//! Whom a stored message went to, as a row of a list names them (`mailmind.kernel.addressees`): the first people its
//! To and Cc name, and how many more.
//!
//! The store keeps To and Cc as one decoded text with a space between the two, which no header's parser reads whole:
//! two bare addresses run together, and a name decoded from an encoded word may hold a comma no quote guards. So this
//! reads it a mailbox at a time: a name's words, then an address in angle brackets, or a bare address standing alone,
//! and a comma between a name's words keeps them one name ("Acme, Inc."). The Python app reads it the same, held to
//! the same cases (tests/fixtures/addressees.json).

use serde_json::{Value, json};

use crate::text::{is_space, strip};

/// The people a row names; the rest it counts.
pub const FIRST: usize = 2;

#[derive(Debug, PartialEq)]
enum Token {
    Word(String),
    Quoted(String),
    Angle(String),
    Comma,
    /// A semicolon or a colon: a group's end, or its name's.
    Group,
}

/// The text as a header's parts. A comment goes, and a quote or a bracket left open runs to the end.
fn tokens(text: &str) -> Vec<Token> {
    let chars: Vec<char> = text.chars().collect();
    let n = chars.len();
    let mut out = Vec::new();
    let mut i = 0;
    while i < n {
        let c = chars[i];
        if is_space(c) {
            i += 1;
        } else if c == '"' {
            i += 1;
            let mut quoted = String::new();
            while i < n && chars[i] != '"' {
                if chars[i] == '\\' && i + 1 < n {
                    i += 1;
                }
                quoted.push(chars[i]);
                i += 1;
            }
            out.push(Token::Quoted(quoted));
            i += 1;
        } else if c == '(' {
            let mut depth = 0;
            while i < n {
                match chars[i] {
                    '\\' => {
                        i += 2;
                        continue;
                    }
                    '(' => depth += 1,
                    ')' => {
                        depth -= 1;
                        if depth == 0 {
                            i += 1;
                            break;
                        }
                    }
                    _ => {}
                }
                i += 1;
            }
        } else if c == '<' {
            let end = chars[i + 1..].iter().position(|&ch| ch == '>').map_or(n, |at| i + 1 + at);
            out.push(Token::Angle(chars[i + 1..end].iter().collect()));
            i = end + 1;
        } else if c == ',' {
            out.push(Token::Comma);
            i += 1;
        } else if c == ';' || c == ':' {
            out.push(Token::Group);
            i += 1;
        } else {
            let start = i;
            while i < n && !is_space(chars[i]) && !"\"(<,;:".contains(chars[i]) {
                i += 1;
            }
            out.push(Token::Word(chars[start..i].iter().collect()));
        }
    }
    out
}

/// Each address To and Cc name, once, in their order, with the name it was written with, or None.
pub fn people(text: Option<&str>) -> Vec<(Option<String>, String)> {
    let mut found: Vec<(Option<String>, String)> = Vec::new();
    let mut seen = std::collections::HashSet::new();
    let mut words: Vec<String> = Vec::new();
    let mut add = |words: &[String], addr: &str| {
        let addr = strip(addr);
        if addr.contains('@') && seen.insert(addr.to_lowercase()) {
            let name = words.iter().map(|w| strip(w)).filter(|w| !w.is_empty()).collect::<Vec<_>>().join(" ");
            found.push(((!name.is_empty()).then_some(name), addr.to_owned()));
        }
    };
    let tokens = tokens(text.unwrap_or_default());
    for (at, token) in tokens.iter().enumerate() {
        match token {
            Token::Angle(addr) => {
                add(&words, addr);
                words.clear();
            }
            Token::Word(word) if word.contains('@') && !matches!(tokens.get(at + 1), Some(Token::Angle(_))) => {
                words.clear();
                add(&words, word);
            }
            Token::Word(word) | Token::Quoted(word) => words.push(word.clone()),
            // A name's comma, when no address came before it: "Acme, Inc. <billing@...>".
            Token::Comma => {
                if let Some(last) = words.last_mut() {
                    last.push(',');
                }
            }
            Token::Group => words.clear(),
        }
    }
    found
}

/// What a row carries of whom its message went to: the first `FIRST`, each with the name it was written with, and how
/// many more.
pub fn listed(text: Option<&str>) -> [(&'static str, Value); 2] {
    let found = people(text);
    let first: Vec<Value> = found.iter().take(FIRST).map(|(name, addr)| json!({"name": name, "addr": addr})).collect();
    [("sent_to", Value::Array(first)), ("sent_to_more", json!(found.len().saturating_sub(FIRST)))]
}

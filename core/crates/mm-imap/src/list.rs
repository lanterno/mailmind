//! The folders a LIST answer names, and a folder name as a command's argument (`imap_list`, `imap_names`).
//!
//! Each LIST line is `(attributes) delimiter name`: the delimiter quoted or NIL, the name an atom, a quoted string
//! or a literal. A name keeps the form the server gave it, a quoted one with its escapes in place, so `mailbox_arg`
//! can send it back as it came; a literal's quotes and backslashes are escaped to that form, and one holding a line
//! break is left out, since it can't be sent back.

use std::collections::BTreeSet;
use std::sync::LazyLock;

use mm_mailbox::roles::Folder;
use regex::Regex;

use crate::wire::Piece;

static HEAD: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r#"(?s)^\(([^)]*)\)\s+("(?:[^"\\]|\\.)*"|NIL)\s*(.*)$"#).expect("the LIST pattern compiles")
});
static MAILBOX_BODY: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r#"^(?:[^"\\\r\n\x00]|\\["\\])*$"#).expect("the name pattern compiles"));

fn delimiter(token: &str) -> Option<String> {
    if token == "NIL" {
        return None;
    }
    Some(token[1..token.len() - 1].replace("\\\"", "\"").replace("\\\\", "\\"))
}

/// The folders in LIST's untagged responses (each without its leading `* LIST `), skipping anything that isn't one.
pub fn parse_list(responses: &[(String, Option<Vec<u8>>)]) -> Vec<Folder> {
    let mut out = Vec::new();
    for (head, literal) in responses {
        let Some(m) = HEAD.captures(head) else {
            continue;
        };
        let attributes: BTreeSet<String> = m[1].split_whitespace().map(str::to_lowercase).collect();
        let rest = m[3].trim();
        let name = match literal {
            Some(bytes) => {
                let name = String::from_utf8_lossy(bytes);
                if name.contains(['\r', '\n', '\0']) {
                    continue;
                }
                name.replace('\\', "\\\\").replace('"', "\\\"")
            }
            None if rest.len() >= 2 && rest.starts_with('"') && rest.ends_with('"') => {
                rest[1..rest.len() - 1].to_owned()
            }
            None => rest.to_owned(),
        };
        if matches!(name.as_str(), "" | "." | "/") {
            continue;
        }
        out.push(Folder { name, attributes, delimiter: delimiter(&m[2]) });
    }
    out
}

/// A folder name in LIST's form as an IMAP command argument: only wrapped, and refused when it isn't a valid
/// quoted-string body, since a CR or LF would end the command line and a bare quote or a trailing backslash the
/// string.
pub fn mailbox_arg(name: &str) -> Option<String> {
    (!name.is_empty() && MAILBOX_BODY.is_match(name)).then(|| format!("\"{name}\""))
}

/// The pieces of one untagged response as (text, literal): its first line, and the literal that line announced.
pub(crate) fn head_and_literal(pieces: Vec<Piece>, prefix: &str) -> Option<(String, Option<Vec<u8>>)> {
    let mut pieces = pieces.into_iter();
    let Some(Piece::Text(first)) = pieces.next() else {
        return None;
    };
    let head = strip_prefix_ignoring_case(&first, prefix)?.to_owned();
    let literal = match pieces.next() {
        Some(Piece::Literal(bytes)) => Some(bytes),
        _ => None,
    };
    Some((head, literal))
}

pub(crate) fn strip_prefix_ignoring_case<'a>(line: &'a str, prefix: &str) -> Option<&'a str> {
    let head = line.get(..prefix.len())?;
    head.eq_ignore_ascii_case(prefix).then(|| &line[prefix.len()..])
}

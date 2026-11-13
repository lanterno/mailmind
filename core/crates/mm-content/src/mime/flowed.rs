//! format=flowed (RFC 3676): a text part as its writer typed it, not as it was wrapped for the wire.

use mail_parser::MessagePart;

const SIGNATURE: &str = "-- ";

/// Whether a part is flowed text, and if so whether DelSp=yes: the space before each soft break goes too.
pub(super) fn of(part: &MessagePart<'_>) -> Option<bool> {
    let content_type = part.content_type()?;
    let flowed = content_type.attribute("format").is_some_and(|format| format.eq_ignore_ascii_case("flowed"));
    flowed.then(|| content_type.attribute("delsp").is_some_and(|delsp| delsp.eq_ignore_ascii_case("yes")))
}

/// A line's quote depth marked again, and its text after one space.
fn marked(out: &mut Vec<String>, depth: usize, text: String) {
    if depth == 0 {
        out.push(text);
    } else if text.is_empty() {
        out.push(">".repeat(depth));
    } else {
        out.push(format!("{} {}", ">".repeat(depth), text));
    }
}

/// A flowed text as it was written. Of each line: its quote depth, then one stuffing space off, and one still ending
/// in a space flows on to the next at its depth. `-- ` above a signature ends in one and is neither, and another
/// depth or an empty line ends a paragraph all the same.
pub(super) fn unflowed(text: &str, delsp: bool) -> String {
    let joint = if delsp { "" } else { " " };
    let text = text.replace("\r\n", "\n");
    let mut out: Vec<String> = Vec::new();
    let mut paragraph: Vec<&str> = Vec::new();
    let mut depth = 0;
    for line in text.split('\n') {
        let piece = line.trim_start_matches('>');
        let at = line.len() - piece.len();
        let piece = piece.strip_prefix(' ').unwrap_or(piece);
        if !paragraph.is_empty() && (at != depth || piece.is_empty() || piece == SIGNATURE) {
            marked(&mut out, depth, paragraph.join(joint));
            paragraph.clear();
        }
        depth = at;
        match piece.strip_suffix(' ') {
            Some(soft) if piece != SIGNATURE => paragraph.push(soft),
            _ => {
                paragraph.push(piece);
                marked(&mut out, at, paragraph.join(joint));
                paragraph.clear();
            }
        }
    }
    if !paragraph.is_empty() {
        marked(&mut out, depth, paragraph.join(joint));
    }
    out.join("\n")
}

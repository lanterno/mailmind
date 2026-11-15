//! Header values as the reader shows them: address lists written out as Python's `email` wrote them, the address a
//! reply goes to, the date, a part's Content-ID and the type of its Content-Disposition.
//!
//! Python's `email` read the first of a header given twice, and so does everything here.

use std::borrow::Cow;
use std::ops::Range;

use mail_parser::{Address, HeaderForm, HeaderName, Headers, Mailbox};

use super::date;

/// Characters that make a display name need quotes (RFC 5322 specials).
const SPECIALS: &[char] = &['(', ')', '<', '>', '@', ',', ':', ';', '.', '\\', '"', '[', ']'];

/// Python's `str.strip()`: what Python counts as white space is Unicode's, and the four information separators too.
pub(super) fn py_strip(text: &str) -> &str {
    text.trim_matches(|c: char| c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c))
}

/// A raw header value as one line: what follows the colon, leading blanks and every line break gone, 8-bit bytes
/// read as UTF-8.
fn unfolded(raw: &[u8]) -> String {
    let text = String::from_utf8_lossy(raw);
    let text = text.trim_start_matches([' ', '\t']);
    text.chars().filter(|c| *c != '\r' && *c != '\n').collect()
}

/// A header value's text with its encoded words decoded, line breaks as spaces, white space off its ends.
fn decoded_text(raw: &[u8]) -> String {
    let line = unfolded(raw);
    let text = if line.contains("=?") {
        let parsed = HeaderForm::Text.parse(raw);
        parsed.value().as_text().map_or(line.clone(), str::to_owned)
    } else {
        line
    };
    py_strip(&text.replace(['\r', '\n'], " ")).to_owned()
}

/// A parameter written as RFC 2047 encoded words, decoded: the blanks between two encoded words go, as RFC 2047 says
/// and as Python's `decode_header` read a file name. None when the parameter isn't written so, and mail-parser's
/// reading of it stands (RFC 2231 among it).
pub(super) fn encoded_param(raw: Option<&[u8]>, name: &str) -> Option<String> {
    let written = param(&unfolded(raw?), name)?;
    if !written.contains("=?") {
        return None;
    }
    let parsed = HeaderForm::Text.parse(written.as_bytes());
    Some(parsed.value().as_text().map_or(written.as_str(), |text| text).trim().to_owned())
}

/// A parameter's value as a header line writes it, `name=token` or `name="quoted string"`, quotes undone.
fn param(line: &str, name: &str) -> Option<String> {
    let mut segments = Vec::new();
    let (mut start, mut quoted, mut escaped) = (0, false, false);
    for (at, c) in line.char_indices() {
        match c {
            _ if escaped => escaped = false,
            '\\' if quoted => escaped = true,
            '"' => quoted = !quoted,
            ';' if !quoted => {
                segments.push(&line[start..at]);
                start = at + 1;
            }
            _ => {}
        }
    }
    segments.push(&line[start..]);
    segments.into_iter().skip(1).find_map(|segment| {
        let (key, value) = segment.split_once('=')?;
        if !key.trim().eq_ignore_ascii_case(name) {
            return None;
        }
        let value = value.trim();
        let Some(inner) = value.strip_prefix('"') else { return Some(value.to_owned()) };
        let mut text = String::new();
        let mut chars = inner.chars();
        while let Some(c) = chars.next() {
            match c {
                '"' => break,
                '\\' => text.extend(chars.next()),
                c => text.push(c),
            }
        }
        Some(text)
    })
}

/// Where a parameter Python read as an empty value lies in a raw header value: `name=""`, at its closing quote
/// ([`EmptyParam::Quoted`]), `name="` with only blanks after its quote to the end of the parameter, just after the
/// quote ([`EmptyParam::Unclosed`]), or a bare `name` with no `=` at all, just after the name ([`EmptyParam::Bare`]).
/// Python read a bare `name=` as no value, so that is none of them, and an unclosed quote with more after it as that
/// value.
pub(super) fn empty_param(raw: &[u8], name: &str) -> Option<EmptyParam> {
    params(raw).find_map(|segment| {
        let text = raw.get(segment.clone())?;
        let Some(equals) = text.iter().position(|&byte| byte == b'=') else {
            if !text.trim_ascii().eq_ignore_ascii_case(name.as_bytes()) {
                return None;
            }
            return Some(EmptyParam::Bare(segment.start + text.trim_ascii_end().len()));
        };
        let (key, value) = (text.get(..equals)?, text.get(equals + 1..)?);
        if !key.trim_ascii().eq_ignore_ascii_case(name.as_bytes()) {
            return None;
        }
        let blanks = value.len() - value.trim_ascii_start().len();
        let after_quote = segment.start + equals + 1 + blanks + 1;
        match value.trim_ascii() {
            b"\"\"" => Some(EmptyParam::Quoted(after_quote)),
            b"\"" => Some(EmptyParam::Unclosed(after_quote)),
            _ => None,
        }
    })
}

/// The bytes of a raw Content-Type value to replace, and what with, for mail-parser to read its parameters as Python
/// did. Python took a vertical tab, a form feed or one of the separators `\x1c` to `\x1f` for white space, and
/// mail-parser keeps them: one at either edge of a parameter's name, or after a boundary, which Python's
/// `get_boundary` stripped on the right, becomes a space. That strip took blanks inside a boundary's quotes too, where
/// mail-parser keeps them: the closing quote moves back over them. Python kept a blank at the start of a value, at the
/// end of any other or inside either, and so does the core, but for a value of such blanks alone that a quote or a
/// special cut short ([`cut_short`]).
pub(super) fn param_replacements(raw: &[u8]) -> Vec<(usize, u8)> {
    let mut replaced = Vec::new();
    for param in params(raw) {
        let Some(text) = raw.get(param.clone()) else { continue };
        let equals = text.iter().position(|&byte| byte == b'=').map_or(param.end, |equals| param.start + equals);
        let name = raw.get(param.start..equals).unwrap_or_default();
        let lead = name.iter().take_while(|&&byte| python_blank(byte)).count();
        let trail = name.iter().rev().take_while(|&&byte| python_blank(byte)).count().min(name.len() - lead);
        let mut edges: Vec<usize> = (param.start..param.start + lead).chain(equals - trail..equals).collect();
        let bare_name = name.get(lead..name.len() - trail).unwrap_or_default();
        let value = raw.get(equals + 1..param.end).unwrap_or_default();
        let blanks = value.iter().rev().take_while(|&&byte| python_blank(byte)).count();
        let cut = cut_short(raw, equals + 1..param.end);
        if !cut.is_empty() {
            replaced.extend(cut);
        } else if bare_name.eq_ignore_ascii_case(b"boundary") && blanks < value.len() {
            edges.extend(param.end - blanks..param.end);
            replaced.extend(closing_quote_moved(raw, equals + 1..param.end));
        }
        let spaces = edges.into_iter().filter(|&at| raw.get(at).copied().is_some_and(kept_by_mail_parser));
        replaced.extend(spaces.map(|at| (at, b' ')));
    }
    replaced
}

/// A value that starts with blanks Python took for white space and mail-parser keeps, right after its `=`, then a
/// byte that ended Python's token and not mail-parser's ([`ends_a_token`]), as `name=\x0b"a.pdf"`: as replacements
/// that make spaces of what follows those blanks to the parameter's end. Python's token ended at that byte, so the
/// value was the blanks alone, which `get_filename` stripped to no name at all, and what came after was junk it
/// dropped. mail-parser read on through the quote and named the part `"a.pdf`, a file, where Python showed it as the
/// body its type makes it. A line break is left as it is. Empty when the value doesn't start so.
fn cut_short(raw: &[u8], value: Range<usize>) -> Vec<(usize, u8)> {
    let bytes = raw.get(value.clone()).unwrap_or_default();
    let blanks = bytes.iter().take_while(|&&byte| kept_by_mail_parser(byte)).count();
    if blanks == 0 || !bytes.get(blanks).is_some_and(|&byte| ends_a_token(byte)) {
        return Vec::new();
    }
    (value.start + blanks..value.end)
        .filter(|&at| raw.get(at).is_some_and(|&byte| !matches!(byte, b' ' | b'\r' | b'\n')))
        .map(|at| (at, b' '))
        .collect()
}

/// Whether a byte ended the token Python read as a parameter's value where mail-parser reads on: a quote, a space, a
/// tab, one of RFC 2045's other tspecials or a `*`, at which Python's `get_extended_attribute` stopped. A `;` ends the
/// parameter for both and a `(` opens a comment for both, and Python read on past a `'` as the end of an RFC 2231
/// charset.
fn ends_a_token(byte: u8) -> bool {
    matches!(
        byte,
        b'"' | b' ' | b'\t' | b')' | b'<' | b'>' | b'@' | b',' | b':' | b'\\' | b'/' | b'[' | b']' | b'?' | b'=' | b'*'
    )
}

/// A quoted value's blanks before its closing quote, as replacements that move the quote back over them, the bytes
/// after it to the old quote becoming spaces: `"b \x0b"` reads `"b"  `. None for a value not in quotes, with no such
/// blanks, or with a fold among them, whose leading blank the quote must not take.
fn closing_quote_moved(raw: &[u8], value: Range<usize>) -> Vec<(usize, u8)> {
    let bytes = raw.get(value.clone()).unwrap_or_default();
    let (Some(open), Some(close)) =
        (bytes.iter().position(|&byte| !python_blank(byte)), bytes.iter().rposition(|&byte| !python_blank(byte)))
    else {
        return Vec::new();
    };
    if open >= close || bytes.get(open) != Some(&b'"') || bytes.get(close) != Some(&b'"') {
        return Vec::new();
    }
    let inside = bytes.get(open + 1..close).unwrap_or_default();
    let blanks = inside.iter().rev().take_while(|&&byte| python_blank(byte)).count();
    let trailing = inside.get(inside.len() - blanks..).unwrap_or_default();
    if blanks == 0 || trailing.iter().any(|&byte| matches!(byte, b'\r' | b'\n')) {
        return Vec::new();
    }
    let quote = value.start + close - blanks;
    std::iter::once((quote, b'"')).chain((quote + 1..=value.start + close).map(|at| (at, b' '))).collect()
}

/// Whether Python's `str.strip` took a byte of a header for white space: an ASCII blank, a line end, a vertical tab, a
/// form feed or a separator from `\x1c` to `\x1f`.
fn python_blank(byte: u8) -> bool {
    byte.is_ascii_whitespace() || matches!(byte, 0x0b | 0x1c..=0x1f)
}

/// Whether mail-parser keeps a byte Python's `str.strip` took for white space at a parameter's edge.
fn kept_by_mail_parser(byte: u8) -> bool {
    matches!(byte, 0x0b | 0x0c | 0x1c..=0x1f)
}

/// Where each parameter of a raw header value lies, as Python's parser split them: at each `;` outside quotes, the
/// value before the first left out.
fn params(raw: &[u8]) -> impl Iterator<Item = Range<usize>> {
    let (mut start, mut quoted) = (0, false);
    let mut segments = Vec::new();
    for (at, &byte) in raw.iter().enumerate() {
        match byte {
            b'"' => quoted = !quoted,
            b';' if !quoted => {
                segments.push(start..at);
                start = at + 1;
            }
            _ => {}
        }
    }
    segments.push(start..raw.len());
    segments.into_iter().skip(1)
}

/// How a parameter Python read as empty is written, and where: see [`empty_param`].
pub(super) enum EmptyParam {
    /// `name=""`: where its closing quote lies.
    Quoted(usize),
    /// `name="` with nothing after the quote: where the quote ends.
    Unclosed(usize),
    /// A bare `name`: where the name ends.
    Bare(usize),
}

/// The first header with this name.
fn first<'m>(headers: &Headers<'m>, name: HeaderName<'static>) -> Option<mail_parser::Header<'m>> {
    headers.all(name).next()
}

/// What comes before a header value's first semicolon, lowercase: a Content-Disposition's type, or a Content-Type
/// as written.
pub(super) fn first_word(raw: &[u8]) -> String {
    let line = unfolded(raw);
    let kind = line.split(';').next().unwrap_or_default();
    py_strip(kind).to_lowercase()
}

/// The first Content-ID, without the angle brackets around it. None when there is no Content-ID.
pub(super) fn content_id(headers: &Headers<'_>) -> Option<String> {
    let found = headers.all(HeaderName::ContentId).next()?;
    Some(decoded_text(found.raw_value()).trim_matches(['<', '>']).to_owned())
}

/// The Date header, written the RFC 5322 way when it reads as a date, else as it came.
pub(super) fn date(headers: &Headers<'_>) -> String {
    let Some(found) = first(headers, HeaderName::Date) else { return String::new() };
    let line = unfolded(found.raw_value());
    match date::normalized(&line) {
        Some(written) => written,
        None => decoded_text(found.raw_value()),
    }
}

/// The address a reply goes to: the first address of Reply-To, bare, as the header wrote it, or empty. An address a
/// display name holds is a name.
pub(super) fn reply_to(headers: &Headers<'_>) -> String {
    headers
        .all(HeaderName::ReplyTo)
        .find_map(|found| {
            let written = unfolded(found.raw_value());
            let read = found.value().as_address().and_then(|list| {
                // mail-parser joins what follows a broken `<...>` onto its address; an address the header doesn't
                // hold is no one's.
                list.mailboxes()
                    .filter_map(|mailbox| mailbox.address())
                    .find(|address| address.contains('@') && written.contains(*address))
                    .map(str::to_owned)
            });
            read.or_else(|| written_address(&written))
        })
        .map(|address| address.trim_matches(['.', '\'']).to_owned())
        .unwrap_or_default()
}

/// The first word of a header line that holds an `@`, as Python's lenient address reading found one where the
/// header's syntax broke.
fn written_address(line: &str) -> Option<String> {
    line.split(|c: char| c.is_whitespace() || "<>,;:()\"".contains(c))
        .find(|word| word.contains('@'))
        .map(str::to_owned)
}

/// An address list header written out on one line: `Name <local@domain>`, a name with specials in quotes, a group as
/// `Name: members;`. Empty when the message has no such header.
pub(super) fn addresses(headers: &Headers<'_>, name: HeaderName<'static>) -> String {
    let Some(found) = first(headers, name) else { return String::new() };
    let Some(list) = found.value().as_address().map(|list| list.iter().collect::<Vec<_>>()) else {
        return decoded_text(found.raw_value());
    };
    if list.is_empty() {
        return decoded_text(found.raw_value());
    }
    let line = unfolded(found.raw_value());
    let written: Vec<String> = list
        .iter()
        .map(|address| match address {
            Address::Mailbox(mailbox) => mailbox_text(mailbox, &line),
            Address::Group(group) => {
                let members: Vec<String> = group.mailboxes().map(|mailbox| mailbox_text(&mailbox, &line)).collect();
                let name = group.name().unwrap_or_default();
                let members = if members.is_empty() { String::new() } else { format!(" {}", members.join(", ")) };
                format!("{}:{};", quoted(name), members)
            }
        })
        .collect();
    let text = written.join(", ");
    py_strip(&text.replace(['\r', '\n'], " ")).to_owned()
}

/// A display name, in quotes when it holds a special, with its quotes and backslashes escaped.
fn quoted(name: &str) -> Cow<'_, str> {
    if name.contains(SPECIALS) { Cow::Owned(quoted_always(name)) } else { Cow::Borrowed(name) }
}

/// One mailbox as Python wrote it: `Name <address>`, the bare address, or a name with no address alone. `line` is
/// the header as written, which says what mail-parser's reading can't: whether a name was quoted.
fn mailbox_text(mailbox: &Mailbox<'_>, line: &str) -> String {
    let name = mailbox.name().map(str::trim).filter(|name| !name.is_empty());
    let Some(address) = mailbox.address() else {
        // `Bo <>` names an empty address, which Python kept; a lone word `bo` is a name and no address.
        let empty_named = |name: &str| {
            line.match_indices(name).any(|(at, _)| {
                let after = line.get(at + name.len()..).unwrap_or_default();
                after.trim_start_matches('"').trim_start().starts_with("<>")
            })
        };
        return match name {
            Some(name) if empty_named(name) => format!("{} <>", quoted(name)),
            Some(name) => name.to_owned(),
            None => "<>".to_owned(),
        };
    };
    // Python wrote no source route (`<@relay:local@domain>`, obsolete since RFC 2822), and no blanks around the `@`.
    let mut address = address.trim();
    if address.starts_with('@')
        && let Some((_, rest)) = address.rsplit_once(':')
    {
        address = rest;
    }
    let address = bare(address);
    let Some(name) = name else {
        return if address.is_empty() { "<>".to_owned() } else { address };
    };
    // A quoted local part (`"quoted local"@example.test`) reads to mail-parser as a name with the domain alone.
    if address.starts_with('@') && line.contains(&format!("\"{name}\"@")) {
        return format!("{}{address}", quoted_always(name));
    }
    // Python made one blank of the blanks between a name's words, unless the name was quoted.
    let name = if line.contains(name) && !line.contains(&format!("\"{name}\"")) {
        Cow::Owned(name.split_whitespace().collect::<Vec<_>>().join(" "))
    } else {
        Cow::Borrowed(name)
    };
    format!("{} <{address}>", quoted(&name))
}

/// An address as Python wrote it: the domain without blanks, a local part with blanks inside it in quotes, one blank
/// between its words. `Bo Lin bo@example.test` is `"Bo Lin bo"@example.test`, not an address no one wrote.
fn bare(address: &str) -> String {
    let (local, domain) = match address.rsplit_once('@') {
        Some((local, domain)) => (local.trim(), Some(domain)),
        None => (address, None),
    };
    let local = if local.contains(char::is_whitespace) {
        quoted_always(&local.split_whitespace().collect::<Vec<_>>().join(" "))
    } else {
        local.to_owned()
    };
    match domain {
        Some(domain) => format!("{local}@{}", domain.chars().filter(|c| !c.is_whitespace()).collect::<String>()),
        None => local,
    }
}

/// Text in quotes, its quotes and backslashes escaped.
fn quoted_always(text: &str) -> String {
    format!("\"{}\"", text.replace('\\', "\\\\").replace('"', "\\\""))
}

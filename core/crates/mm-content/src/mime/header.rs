//! Header values as the reader shows them: address lists written out as Python's `email` wrote them, the address a
//! reply goes to, the date, a part's Content-ID and the type of its Content-Disposition.
//!
//! Python's `email` read the first of a header given twice, and so does everything here.

use std::borrow::Cow;

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
/// ([`EmptyParam::Quoted`]), or a bare `name` with no `=` at all, just after the name ([`EmptyParam::Bare`]). Python
/// read a bare `name=` as no value, so that is neither.
pub(super) fn empty_param(raw: &[u8], name: &str) -> Option<EmptyParam> {
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
    segments.into_iter().skip(1).find_map(|segment| {
        let text = raw.get(segment.clone())?;
        let Some(equals) = text.iter().position(|&byte| byte == b'=') else {
            if !text.trim_ascii().eq_ignore_ascii_case(name.as_bytes()) {
                return None;
            }
            return Some(EmptyParam::Bare(segment.start + text.trim_ascii_end().len()));
        };
        let (key, value) = (text.get(..equals)?, text.get(equals + 1..)?);
        if !key.trim_ascii().eq_ignore_ascii_case(name.as_bytes()) || value.trim_ascii() != b"\"\"" {
            return None;
        }
        let blanks = value.len() - value.trim_ascii_start().len();
        Some(EmptyParam::Quoted(segment.start + equals + 1 + blanks + 1))
    })
}

/// How a parameter Python read as empty is written, and where: see [`empty_param`].
pub(super) enum EmptyParam {
    /// `name=""`: where its closing quote lies.
    Quoted(usize),
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

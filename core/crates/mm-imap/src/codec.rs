//! A server's answers read into values, by imap-proto: RFC 3501 and the extensions MailMind asks for (CONDSTORE's
//! mod-sequences, UIDPLUS's COPYUID and APPENDUID, ENABLE, Gmail's X-GM-THRID and X-GM-LABELS). Only what the
//! session acts on is kept; any other answer is `Said::Other`, as one imap-proto can't read is, since a server may
//! send what nothing here asked for.

use imap_proto::rfc4315::UidSetMember;
use imap_proto::{AttributeValue, Capability, MailboxDatum, Response, ResponseCode};

use crate::names::label_name;
use crate::wire::Piece;

/// What one message's FETCH answer said, of what was asked.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct Fetched {
    pub(crate) uid: Option<u32>,
    pub(crate) flags: Option<Vec<String>>,
    /// The body section asked for: a header's fields, or the whole message.
    pub(crate) section: Option<Vec<u8>>,
    pub(crate) gmail_thread: Option<u64>,
    pub(crate) gmail_labels: Option<Vec<String>>,
    /// INTERNALDATE, when the message arrived, in Unix seconds: None where it wasn't asked for or doesn't read.
    pub(crate) arrived: Option<i64>,
}

/// A response code (`[UIDVALIDITY 7]`), of those the session reads.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) enum Code {
    UidValidity(u32),
    UidNext(u32),
    HighestModSeq(u64),
    AppendUid(u32),
    Capabilities(Vec<String>),
}

/// One response, as far as the session reads it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) enum Said {
    Exists(u32),
    Search(Vec<u32>),
    Fetch(Fetched),
    Code(Code),
    /// CAPABILITY's list, or ENABLED's.
    Capabilities(Vec<String>),
    Other,
}

/// A response's bytes as they came: each line with its CRLF, each literal where it stood.
pub(crate) fn raw(pieces: &[Piece]) -> Vec<u8> {
    let mut out = Vec::new();
    for piece in pieces {
        match piece {
            Piece::Text(text) => {
                out.extend_from_slice(text.as_bytes());
                out.extend_from_slice(b"\r\n");
            }
            Piece::Literal(bytes) => out.extend_from_slice(bytes),
        }
    }
    out
}

fn capability(c: &Capability<'_>) -> String {
    match c {
        Capability::Imap4rev1 => "IMAP4REV1".to_owned(),
        Capability::Auth(how) => format!("AUTH={}", how.to_ascii_uppercase()),
        Capability::Atom(name) => name.to_ascii_uppercase(),
    }
}

fn code(code: &ResponseCode<'_>) -> Option<Code> {
    Some(match code {
        ResponseCode::UidValidity(n) => Code::UidValidity(*n),
        ResponseCode::UidNext(n) => Code::UidNext(*n),
        ResponseCode::HighestModSeq(n) => Code::HighestModSeq(*n),
        ResponseCode::AppendUid(_, uids) => match uids.first() {
            Some(UidSetMember::Uid(uid)) => Code::AppendUid(*uid),
            Some(UidSetMember::UidRange(range)) => Code::AppendUid(*range.start()),
            None => return None,
        },
        ResponseCode::Capabilities(list) => Code::Capabilities(list.iter().map(capability).collect()),
        _ => return None,
    })
}

fn fetched(attributes: &[AttributeValue<'_>]) -> Fetched {
    let mut out = Fetched::default();
    for attribute in attributes {
        match attribute {
            AttributeValue::Uid(uid) => out.uid = Some(*uid),
            AttributeValue::Flags(flags) => out.flags = Some(flags.iter().map(|f| f.to_string()).collect()),
            AttributeValue::BodySection { data: Some(data), .. } => out.section = Some(data.to_vec()),
            AttributeValue::Rfc822(Some(data)) => out.section = Some(data.to_vec()),
            AttributeValue::GmailThrId(id) => out.gmail_thread = Some(*id),
            AttributeValue::GmailLabels(labels) => {
                out.gmail_labels = Some(labels.iter().map(|l| label_name(l)).collect());
            }
            AttributeValue::InternalDate(date) => out.arrived = internal_date(date),
            _ => {}
        }
    }
    out
}

/// An INTERNALDATE (`17-Jul-1996 02:44:25 -0700`, its day perhaps led by a space) in Unix seconds, as
/// `imaplib.Internaldate2tuple` reads one: None where it doesn't read as one.
pub(crate) fn internal_date(text: &str) -> Option<i64> {
    const MONTHS: [&str; 12] = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"];
    let number = |part: &str, width: usize| -> Option<i64> {
        (part.len() == width && part.bytes().all(|b| b.is_ascii_digit())).then(|| part.parse().ok()).flatten()
    };
    // A day below ten may be led by a space.
    let (date, rest) = text.strip_prefix(' ').unwrap_or(text).split_once(' ')?;
    let mut parts = date.split('-');
    let (day, month, year) = (parts.next()?, parts.next()?, parts.next()?);
    let day = number(day, 2).or_else(|| number(day, 1))?;
    let month = MONTHS.iter().position(|m| m.eq_ignore_ascii_case(month))? as u32 + 1;
    let year = number(year, 4)?;
    let (time, zone) = rest.split_once(' ')?;
    let mut clock = time.split(':');
    let (hour, minute, second) = (number(clock.next()?, 2)?, number(clock.next()?, 2)?, number(clock.next()?, 2)?);
    let (sign, digits) = match zone.as_bytes().first()? {
        b'+' => (1, &zone[1..]),
        b'-' => (-1, &zone[1..]),
        _ => return None,
    };
    let (zone_hours, zone_minutes) = (number(digits.get(..2)?, 2)?, number(digits.get(2..)?, 2)?);
    // imaplib's pattern takes a day's first digit from ` 0123`, and a day past its month's end runs on.
    if parts.next().is_some() || clock.next().is_some() || day > 39 {
        return None;
    }
    let days = mm_kernel::when::days_from_civil(year, month, 1) + day - 1;
    let local = days * 86_400 + hour * 3_600 + minute * 60 + second;
    Some(local - sign * (zone_hours * 3_600 + zone_minutes * 60))
}

/// What a response said. `raw` is the whole of it, its last CRLF included.
pub(crate) fn read(raw: &[u8]) -> Said {
    let response = match Response::parse(raw) {
        Ok((_, response)) => response,
        Err(_) => return search_with_modseq(raw).unwrap_or(Said::Other),
    };
    match response {
        Response::MailboxData(MailboxDatum::Exists(n)) => Said::Exists(n),
        Response::MailboxData(MailboxDatum::Search(uids)) => Said::Search(uids),
        Response::Fetch(_, attributes) => Said::Fetch(fetched(&attributes)),
        Response::Capabilities(list) => Said::Capabilities(list.iter().map(capability).collect()),
        Response::Data { outcome, .. } | Response::Done { outcome, .. } => {
            outcome.code.as_ref().and_then(code).map_or(Said::Other, Said::Code)
        }
        _ => Said::Other,
    }
}

/// `* SEARCH 1 2 (MODSEQ 9)`: RFC 7162 says after a SEARCH's numbers which mod-sequence they reach, which imap-proto
/// doesn't read. The numbers are read without it.
fn search_with_modseq(raw: &[u8]) -> Option<Said> {
    let line = std::str::from_utf8(raw).ok()?.trim_end_matches("\r\n");
    let head = line.get(..9)?;
    if !head.eq_ignore_ascii_case("* SEARCH ") || !line.ends_with(')') {
        return None;
    }
    let numbers = &line[9..line.rfind('(')?];
    let uids: Option<Vec<u32>> = numbers.split_whitespace().map(|n| n.parse().ok()).collect();
    uids.map(Said::Search)
}

#[cfg(test)]
mod tests;

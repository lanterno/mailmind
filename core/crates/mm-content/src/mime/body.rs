//! A part's body: its bytes with the transfer encoding undone, and its text in its charset.

use std::borrow::Cow;

use mail_parser::{Charset, Encoding, HeaderName, MessagePart, PartFlags};

/// The body as written, still transfer-encoded, where Python's parser would have found it: the last part of a
/// multipart that never closed ends before the line break its close delimiter would have taken.
fn body_of<'m>(part: &MessagePart<'m>) -> &'m [u8] {
    let body = part.raw_body();
    let at_end = part.offset_end() as usize == part.message().source_bytes().len();
    let unclosed = part.parent().is_some_and(|parent| parent.flags().contains(PartFlags::UNTERMINATED));
    if at_end && (unclosed || part.parent().is_some() && part.flags().contains(PartFlags::MISSING_DELIMITER)) {
        return without_line_break(body);
    }
    body
}

/// Bytes without the one line break they end in.
fn without_line_break(bytes: &[u8]) -> &[u8] {
    bytes
        .strip_suffix(b"\r\n")
        .or_else(|| bytes.strip_suffix(b"\n"))
        .or_else(|| bytes.strip_suffix(b"\r"))
        .unwrap_or(bytes)
}

/// The body with its transfer encoding undone: base64 as mail-parser decodes it, leniently, and quoted-printable and
/// uuencode as Python decoded them.
fn transfer_decoded<'m>(part: &MessagePart<'m>) -> Cow<'m, [u8]> {
    let body = body_of(part);
    match part.encoding() {
        Encoding::QuotedPrintable => Cow::Owned(quoted_printable(body)),
        Encoding::Base64 => Encoding::Base64.decode(body),
        Encoding::None => match uuencoded(part) {
            true => uudecode(body).map_or(Cow::Borrowed(body), Cow::Owned),
            false => Cow::Borrowed(body),
        },
    }
}

/// What a part holds, transfer encoding undone. An attached email holds no body of its own here, as in Python's
/// `decoded`: its bytes are `bytes`.
pub(super) fn decoded<'m>(part: &MessagePart<'m>) -> Cow<'m, [u8]> {
    if is_message(part) { Cow::Borrowed(&[]) } else { transfer_decoded(part) }
}

/// How many bytes `decoded` gives, without decoding when the encoding allows it.
pub(super) fn decoded_len(part: &MessagePart<'_>) -> usize {
    if is_message(part) {
        return 0;
    }
    match part.encoding() {
        Encoding::Base64 => Encoding::Base64.decoded_len(body_of(part)),
        _ => transfer_decoded(part).len(),
    }
}

/// A part's bytes as a download gives them: an attached email as it was sent, anything else as `decoded`.
pub(super) fn bytes<'m>(part: &MessagePart<'m>) -> Cow<'m, [u8]> {
    transfer_decoded(part)
}

/// Bytes as UTF-8 text, each sequence that isn't UTF-8 one replacement character.
pub(super) fn utf8(bytes: &[u8]) -> Cow<'_, str> {
    String::from_utf8_lossy(bytes)
}

/// A text part's text, in the charset it names: UTF-8 when it names none, or one no reader knows.
pub(super) fn text<'m>(part: &MessagePart<'m>) -> Cow<'m, str> {
    in_charset(part, decoded(part))
}

/// What a multipart holding no parts holds, as text. A multipart whose first delimiter closes it holds what came
/// before that delimiter, with the line break before it, as Python kept it; one with no delimiter at all holds its
/// whole body.
pub(super) fn unparted_text<'m>(part: &MessagePart<'m>) -> Cow<'m, str> {
    let body = part.raw_body();
    let boundary = part.content_type().and_then(|content_type| content_type.attribute("boundary"));
    let body = match boundary.filter(|boundary| !boundary.is_empty()) {
        Some(boundary) => before_close(body, boundary),
        None => body,
    };
    let decoded = match part.encoding() {
        Encoding::QuotedPrintable => Cow::Owned(quoted_printable(body)),
        encoding => encoding.decode(body),
    };
    in_charset(part, decoded)
}

/// A body up to its first line that is a delimiter of `boundary`, kept whole when that line opens a part rather
/// than closing the multipart: the preamble Python took for the text of a multipart its first delimiter closed.
fn before_close<'b>(body: &'b [u8], boundary: &str) -> &'b [u8] {
    let dashed = [b"--", boundary.as_bytes()].concat();
    let mut at = 0;
    for line in body.split_inclusive(|&byte| byte == b'\n') {
        let bare = line.strip_suffix(b"\n").unwrap_or(line);
        let bare = bare.strip_suffix(b"\r").unwrap_or(bare);
        if let Some(rest) = bare.strip_prefix(dashed.as_slice()) {
            let (closes, rest) = match rest.strip_prefix(b"--") {
                Some(rest) => (true, rest),
                None => (false, rest),
            };
            if rest.iter().all(|&byte| byte == b' ' || byte == b'\t') {
                return if closes { body.get(..at).unwrap_or(body) } else { body };
            }
        }
        at += line.len();
    }
    body
}

/// Bytes read as text in the charset a part names.
fn in_charset<'m>(part: &MessagePart<'m>, bytes: Cow<'m, [u8]>) -> Cow<'m, str> {
    let label = part.content_type().and_then(|content_type| content_type.attribute("charset")).unwrap_or_default();
    let charset = charset(label).unwrap_or(Charset::Utf8);
    let text = match bytes {
        Cow::Borrowed(bytes) => charset.decode(bytes),
        Cow::Owned(bytes) => Cow::Owned(charset.decode_owned(bytes)),
    };
    // utf-8-sig is UTF-8 that starts with a byte order mark, which isn't text.
    if label.trim().eq_ignore_ascii_case("utf-8-sig") && text.starts_with('\u{feff}') {
        return match text {
            Cow::Borrowed(text) => Cow::Borrowed(&text['\u{feff}'.len_utf8()..]),
            Cow::Owned(text) => Cow::Owned(text['\u{feff}'.len_utf8()..].to_owned()),
        };
    }
    text
}

/// The charset a label names, by its WHATWG or MIME name, or as Python spells it (`latin_1`, `utf_8`).
fn charset(label: &str) -> Option<Charset> {
    let label = label.trim();
    if !label.is_ascii() {
        return None;
    }
    Charset::from_label(label.as_bytes()).or_else(|| {
        let spelled = label.to_ascii_lowercase().replace(['_', ' '], "-");
        let known = match spelled.as_str() {
            "latin-1" | "iso8859-1" | "8859" | "cp819" | "l1" => "iso-8859-1",
            "utf" | "u8" | "utf-8-sig" | "cp65001" => "utf-8",
            "latin-2" | "iso8859-2" | "l2" => "iso-8859-2",
            "latin-9" | "iso8859-15" | "l9" => "iso-8859-15",
            "mac-roman" | "macroman" => "macintosh",
            "ks-c-5601-1987" | "ks-c-5601" | "ksc5601" | "uhc" | "ms949" => "euc-kr",
            "sjis" | "s-jis" | "ms-kanji" | "cp932" | "ms932" | "mskanji" => "shift_jis",
            "eucjp" | "ujis" | "u-jis" => "euc-jp",
            "euckr" => "euc-kr",
            "big5-tw" | "cp950" => "big5",
            "eucgb2312-cn" | "euc-cn" | "euccn" => "gb2312",
            "utf16" | "u16" => "utf-16",
            "utf7" | "u7" => "utf-7",
            other => other,
        };
        Charset::from_label(known.as_bytes())
    })
}

/// Whether the part is a message/* part: Python's parser read what it holds as an email, so it has no body of its own.
fn is_message(part: &MessagePart<'_>) -> bool {
    part.is_message()
        || part
            .content_type()
            .is_some_and(|content_type| content_type.ctype() == "message" && content_type.subtype().is_some())
}

/// Quoted-printable decoded as Python's `binascii.a2b_qp` decodes a body: `=` and two hex digits of either case is
/// a byte, `=` at the end of a line joins it to the next, and an `=` that is neither stays. White space at the end of
/// a line stays too, where RFC 2045 would drop it: a sender's flowed text ends its soft breaks in a space. An `=`
/// that white space alone follows to the end of its line is a soft break with the transport's blanks after it.
pub(super) fn quoted_printable(body: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(body.len());
    let mut at = 0;
    while let Some(&byte) = body.get(at) {
        at += 1;
        if byte != b'=' {
            out.push(byte);
            continue;
        }
        let Some(&next) = body.get(at) else { break };
        let blanks = body.get(at..).map_or(0, |rest| rest.iter().take_while(|&&b| b == b' ' || b == b'\t').count());
        match (next, body.get(at + blanks)) {
            (b'\r' | b'\n', _) | (b' ' | b'\t', Some(b'\r' | b'\n')) => {
                at += blanks;
                while body.get(at).is_some_and(|&b| b != b'\n') {
                    at += 1;
                }
                at += 1;
            }
            (b'=', _) => {
                out.push(b'=');
                at += 1;
            }
            _ => match (body.get(at).copied().and_then(hex), body.get(at + 1).copied().and_then(hex)) {
                (Some(high), Some(low)) => {
                    out.push(high << 4 | low);
                    at += 2;
                }
                _ => out.push(b'='),
            },
        }
    }
    out
}

fn hex(byte: u8) -> Option<u8> {
    (byte as char).to_digit(16).map(|digit| digit as u8)
}

/// Whether the first Content-Transfer-Encoding says the body is uuencoded.
fn uuencoded(part: &MessagePart<'_>) -> bool {
    part.headers().all(HeaderName::ContentTransferEncoding).next().is_some_and(|named| {
        let value = String::from_utf8_lossy(named.raw_value()).trim().to_ascii_lowercase();
        matches!(value.as_str(), "x-uuencode" | "uuencode" | "uue" | "x-uue")
    })
}

/// Python's `email.message._decode_uu`: the lines after a `begin` line with an octal mode, up to `end`. None when
/// there is no such line, or a line is empty before `end`: the body then stays as it came.
fn uudecode(body: &[u8]) -> Option<Vec<u8>> {
    let mut lines = body.split(|&byte| byte == b'\n').map(|line| line.strip_suffix(b"\r").unwrap_or(line));
    loop {
        let line = lines.next()?;
        if let Some(rest) = line.strip_prefix(b"begin ") {
            let mode = rest.split(|&byte| byte == b' ').next().unwrap_or_default();
            if !mode.is_empty() && mode.iter().all(|byte| (b'0'..=b'7').contains(byte)) {
                break;
            }
        }
    }
    let mut out = Vec::new();
    for line in lines {
        if line.is_empty() {
            return None;
        }
        if line.trim_ascii() == b"end" {
            break;
        }
        uu_line(line, &mut out);
    }
    Some(out)
}

/// One uuencoded line: its first character says how many bytes it holds, and each four characters after hold three.
fn uu_line(line: &[u8], out: &mut Vec<u8>) {
    let Some((&first, rest)) = line.split_first() else { return };
    let want = usize::from(first.wrapping_sub(32) & 63);
    let mut bits = 0u32;
    let mut held = 0u32;
    let mut got = 0;
    for &byte in rest {
        if got == want {
            break;
        }
        bits = (bits << 6) | u32::from(byte.wrapping_sub(32) & 63);
        held += 6;
        if held >= 8 {
            held -= 8;
            out.push(((bits >> held) & 0xff) as u8);
            got += 1;
        }
    }
    out.extend(std::iter::repeat_n(0, want - got));
}

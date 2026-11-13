//! Header values parsed into trees as Python's `email._header_value_parser` parses them (`get_unstructured`,
//! `get_address_list`, `parse_message_id`, `parse_message_ids` and the pieces under them), for `fold` to fold.
//!
//! A step that fails is `Err(())`, where Python raised `HeaderParseError` and a caller tried something else. The
//! defects Python notes on the way change nothing a message's bytes say, so none are kept. An RFC 2047 encoded word
//! is decoded where Python decodes one when its charset is UTF-8, ASCII or Latin-1; one in another charset, or whose
//! bytes don't decode, is read as text.

use super::fold::{Kind, SPECIALS, Tok};
use mm_kernel::text::is_space;

type Parsed<'a> = Result<(Tok, &'a str), ()>;

fn first(v: &str) -> Option<char> {
    v.chars().next()
}

fn after_first(v: &str) -> &str {
    let mut chars = v.chars();
    chars.next();
    chars.as_str()
}

fn starts(v: &str, set: fn(char) -> bool) -> bool {
    first(v).is_some_and(set)
}

fn is_wsp(c: char) -> bool {
    c == ' ' || c == '\t'
}

fn is_special(c: char) -> bool {
    SPECIALS.contains(c)
}

fn is_cfws_leader(c: char) -> bool {
    is_wsp(c) || c == '('
}

/// `ATOM_ENDS`.
fn is_atom_end(c: char) -> bool {
    is_special(c) || is_wsp(c)
}

/// `PHRASE_ENDS`: `.`, `"` and `(` don't end a phrase.
fn is_phrase_end(c: char) -> bool {
    is_special(c) && !matches!(c, '.' | '"' | '(')
}

/// `_wsp_splitter(value, 1)`: (what comes before the first run of blanks, the rest from it on).
fn wsp_split(value: &str) -> (&str, &str) {
    let at = value.find([' ', '\t']).unwrap_or(value.len());
    value.split_at(at)
}

/// `get_fws`: the blanks, and whatever whitespace follows them, as `str.lstrip` takes it.
fn get_fws(value: &str) -> (Tok, &str) {
    let rest = value.trim_start_matches(is_space);
    (Tok::space(&value[..value.len() - rest.len()], "fws"), rest)
}

/// `_get_ptext_to_endchars`: the text up to one of `endchars` or a blank, quoted pairs unquoted.
fn ptext_to_endchars<'a>(value: &'a str, endchars: &str) -> (String, &'a str) {
    if value.is_empty() {
        return (String::new(), "");
    }
    let (fragment, _) = wsp_split(value);
    let mut vchars = String::new();
    let mut escape = false;
    let mut stop = fragment.len();
    for (at, c) in fragment.char_indices() {
        if c == '\\' {
            if escape {
                escape = false;
            } else {
                escape = true;
                continue;
            }
        }
        if escape {
            escape = false;
        } else if endchars.contains(c) {
            stop = at;
            break;
        }
        vchars.push(c);
    }
    (vchars, &value[stop..])
}

/// Why `get_encoded_word` refused: `_InvalidEwError`, or another `HeaderParseError`.
enum EwRefusal {
    Invalid,
    NotOne,
}

/// `get_encoded_word`.
fn get_encoded_word<'a>(value: &'a str, terminal_type: &'static str) -> Result<(Tok, &'a str), EwRefusal> {
    let Some(body) = value.strip_prefix("=?") else {
        return Err(EwRefusal::NotOne);
    };
    let Some(end) = body.find("?=") else {
        return Err(EwRefusal::NotOne);
    };
    let mut tok = body[..end].to_owned();
    let mut rest = &body[end + 2..];
    let hex = |b: Option<&u8>| b.is_some_and(u8::is_ascii_hexdigit);
    if hex(rest.as_bytes().first()) && hex(rest.as_bytes().get(1)) && tok.matches('?').count() < 2 {
        match rest.find("?=") {
            Some(at) => {
                tok = format!("{tok}?={}", &rest[..at]);
                rest = &rest[at + 2..];
            }
            None => {
                tok = format!("{tok}?={rest}");
                rest = "";
            }
        }
    }
    let text = decode_ew(&tok).ok_or(EwRefusal::Invalid)?;
    let mut parts = Vec::new();
    let mut text = text.as_str();
    while !text.is_empty() {
        if starts(text, is_wsp) {
            let (fws, more) = get_fws(text);
            parts.push(fws);
            text = more;
            continue;
        }
        let (chars, more) = wsp_split(text);
        parts.push(Tok::value(chars, terminal_type));
        text = more;
    }
    Ok((Tok::list(Kind::EncodedWord, parts), rest))
}

/// `_ew.decode` of `=?{tok}?=`, for the charsets this module reads.
fn decode_ew(tok: &str) -> Option<String> {
    let pieces: Vec<&str> = tok.split('?').collect();
    let [charset, cte, encoded] = pieces[..] else {
        return None;
    };
    let charset = charset.split('*').next().unwrap_or_default();
    if !encoded.is_ascii() {
        return None;
    }
    let bytes = match cte.to_ascii_lowercase().as_str() {
        "q" => decode_q(encoded.as_bytes()),
        "b" => decode_b(encoded.as_bytes())?,
        _ => return None,
    };
    let name: String = charset
        .to_ascii_lowercase()
        .chars()
        .map(|c| if c.is_ascii_alphanumeric() || c == '.' { c } else { '_' })
        .collect();
    match name.as_str() {
        "utf_8" | "utf8" | "u8" | "utf" => String::from_utf8(bytes).ok(),
        "ascii" | "us_ascii" | "646" | "us" => bytes.is_ascii().then(|| bytes.iter().map(|b| char::from(*b)).collect()),
        "latin_1" | "latin1" | "l1" | "iso8859_1" | "iso_8859_1" | "8859" | "cp819" | "latin" => {
            Some(bytes.iter().map(|b| char::from(*b)).collect())
        }
        _ => None,
    }
}

/// `_ew.decode_q`.
fn decode_q(encoded: &[u8]) -> Vec<u8> {
    let mut out = Vec::new();
    let mut at = 0;
    while at < encoded.len() {
        let b = encoded[at];
        if b == b'='
            && at + 2 < encoded.len()
            && encoded[at + 1].is_ascii_hexdigit()
            && encoded[at + 2].is_ascii_hexdigit()
        {
            let hex = std::str::from_utf8(&encoded[at + 1..at + 3]).unwrap_or("00");
            out.push(u8::from_str_radix(hex, 16).unwrap_or_default());
            at += 3;
            continue;
        }
        out.push(if b == b'_' { b' ' } else { b });
        at += 1;
    }
    out
}

/// `_ew.decode_b`, where the text is well-formed base64 once padded.
fn decode_b(encoded: &[u8]) -> Option<Vec<u8>> {
    let mut text = encoded.to_vec();
    while !text.len().is_multiple_of(4) {
        text.push(b'=');
    }
    let value = |c: u8| -> Option<u32> {
        Some(match c {
            b'A'..=b'Z' => u32::from(c - b'A'),
            b'a'..=b'z' => u32::from(c - b'a') + 26,
            b'0'..=b'9' => u32::from(c - b'0') + 52,
            b'+' => 62,
            b'/' => 63,
            _ => return None,
        })
    };
    let mut out = Vec::new();
    for quad in text.chunks(4) {
        let pad = quad.iter().rev().take_while(|c| **c == b'=').count();
        if pad > 2 {
            return None;
        }
        let mut n = 0u32;
        for &c in &quad[..4 - pad] {
            n = n << 6 | value(c)?;
        }
        n <<= 6 * pad as u32;
        let bytes = n.to_be_bytes();
        out.extend_from_slice(&bytes[1..4 - pad]);
    }
    Some(out)
}

/// `rfc2047_matcher`.
fn looks_encoded(tok: &str) -> bool {
    static MATCHER: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    MATCHER
        .get_or_init(|| regex::Regex::new(r"=\?[^?]*\?[qQbB]\?.*?\?=").unwrap_or_else(|_| unreachable!()))
        .is_match(tok)
}

/// `get_unstructured`.
pub(super) fn get_unstructured(value: &str) -> Tok {
    let mut parts: Vec<Tok> = Vec::new();
    let mut value = value;
    while !value.is_empty() {
        if starts(value, is_wsp) {
            let (fws, rest) = get_fws(value);
            parts.push(fws);
            value = rest;
            continue;
        }
        let mut valid_ew = true;
        if value.starts_with("=?") {
            match get_encoded_word(value, "utext") {
                Err(EwRefusal::Invalid) => valid_ew = false,
                Err(EwRefusal::NotOne) => {}
                Ok((ew, rest)) => {
                    let have_ws = parts.last().is_none_or(|p| p.token_type() == "fws");
                    let n = parts.len();
                    if have_ws && n > 1 && parts[n - 2].token_type() == "encoded-word" {
                        parts[n - 1].kind = Kind::EwSpace;
                    }
                    parts.push(ew);
                    value = rest;
                    continue;
                }
            }
        }
        let (mut tok, mut rest) = wsp_split(value);
        if valid_ew && looks_encoded(tok) {
            let at = value.find("=?").unwrap_or(0);
            if at > 0 {
                (tok, rest) = value.split_at(at);
            }
        }
        parts.push(Tok::value(tok, "utext"));
        value = rest;
    }
    Tok::list(Kind::Unstructured, parts)
}

/// `get_qp_ctext`.
fn get_qp_ctext(value: &str) -> (Tok, &str) {
    let (ptext, rest) = ptext_to_endchars(value, "()");
    (Tok::space(ptext, "ptext"), rest)
}

/// `get_qcontent`.
fn get_qcontent(value: &str) -> (Tok, &str) {
    let (ptext, rest) = ptext_to_endchars(value, "\"");
    (Tok::value(ptext, "ptext"), rest)
}

/// `get_atext`.
fn get_atext(value: &str) -> Parsed<'_> {
    let at = value.find(is_atom_end).unwrap_or(value.len());
    if at == 0 {
        return Err(());
    }
    Ok((Tok::value(&value[..at], "atext"), &value[at..]))
}

/// `get_bare_quoted_string`.
fn get_bare_quoted_string(value: &str) -> Parsed<'_> {
    if first(value) != Some('"') {
        return Err(());
    }
    let mut parts: Vec<Tok> = Vec::new();
    let mut value = after_first(value);
    if first(value) == Some('"') {
        let (tok, rest) = get_qcontent(value);
        parts.push(tok);
        value = rest;
    }
    while !value.is_empty() && first(value) != Some('"') {
        let (tok, rest) = if starts(value, is_wsp) {
            get_fws(value)
        } else if value.starts_with("=?") {
            match get_encoded_word(value, "vtext") {
                Ok((ew, rest)) => {
                    let n = parts.len();
                    if n > 1 && parts[n - 1].token_type() == "fws" && parts[n - 2].token_type() == "encoded-word" {
                        parts[n - 1].kind = Kind::EwSpace;
                    }
                    (ew, rest)
                }
                Err(_) => get_qcontent(value),
            }
        } else {
            get_qcontent(value)
        };
        parts.push(tok);
        value = rest;
    }
    let bare = Tok::list(Kind::BareQuotedString, parts);
    if value.is_empty() {
        return Ok((bare, value));
    }
    Ok((bare, after_first(value)))
}

/// `get_comment`.
fn get_comment(value: &str) -> Parsed<'_> {
    if first(value) != Some('(') {
        return Err(());
    }
    let mut parts: Vec<Tok> = Vec::new();
    let mut value = after_first(value);
    while !value.is_empty() && first(value) != Some(')') {
        let (tok, rest) = if starts(value, is_wsp) {
            get_fws(value)
        } else if first(value) == Some('(') {
            get_comment(value)?
        } else {
            get_qp_ctext(value)
        };
        parts.push(tok);
        value = rest;
    }
    let comment = Tok::list(Kind::Comment, parts);
    if value.is_empty() {
        return Ok((comment, value));
    }
    Ok((comment, after_first(value)))
}

/// `get_cfws`.
fn get_cfws(value: &str) -> Parsed<'_> {
    let mut parts: Vec<Tok> = Vec::new();
    let mut value = value;
    while starts(value, is_cfws_leader) {
        let (tok, rest) = if starts(value, is_wsp) { get_fws(value) } else { get_comment(value)? };
        parts.push(tok);
        value = rest;
    }
    Ok((Tok::list(Kind::Cfws, parts), value))
}

/// `get_quoted_string`.
fn get_quoted_string(value: &str) -> Parsed<'_> {
    let mut parts: Vec<Tok> = Vec::new();
    let mut value = value;
    if starts(value, is_cfws_leader) {
        let (cfws, rest) = get_cfws(value)?;
        parts.push(cfws);
        value = rest;
    }
    let (bare, rest) = get_bare_quoted_string(value)?;
    parts.push(bare);
    value = rest;
    if starts(value, is_cfws_leader) {
        let (cfws, rest) = get_cfws(value)?;
        parts.push(cfws);
        value = rest;
    }
    Ok((Tok::list(Kind::QuotedString, parts), value))
}

/// `get_atom`.
fn get_atom(value: &str) -> Parsed<'_> {
    let mut parts: Vec<Tok> = Vec::new();
    let mut value = value;
    if starts(value, is_cfws_leader) {
        let (cfws, rest) = get_cfws(value)?;
        parts.push(cfws);
        value = rest;
    }
    if starts(value, is_atom_end) {
        return Err(());
    }
    let (tok, rest) = if value.starts_with("=?") {
        match get_encoded_word(value, "vtext") {
            Ok(found) => found,
            Err(_) => get_atext(value)?,
        }
    } else {
        get_atext(value)?
    };
    parts.push(tok);
    value = rest;
    if starts(value, is_cfws_leader) {
        let (cfws, rest) = get_cfws(value)?;
        parts.push(cfws);
        value = rest;
    }
    Ok((Tok::list(Kind::Atom, parts), value))
}

/// `get_dot_atom_text`.
fn get_dot_atom_text(value: &str) -> Parsed<'_> {
    if value.is_empty() || starts(value, is_atom_end) {
        return Err(());
    }
    let mut parts: Vec<Tok> = Vec::new();
    let mut value = value;
    while !value.is_empty() && !starts(value, is_atom_end) {
        let (atext, rest) = get_atext(value)?;
        parts.push(atext);
        value = rest;
        if first(value) == Some('.') {
            parts.push(Tok::dot());
            value = after_first(value);
        }
    }
    if parts.last().is_some_and(|p| p.ty == "dot") {
        return Err(());
    }
    Ok((Tok::list(Kind::DotAtomText, parts), value))
}

/// `get_dot_atom`.
fn get_dot_atom(value: &str) -> Parsed<'_> {
    let mut parts: Vec<Tok> = Vec::new();
    let mut value = value;
    if starts(value, is_cfws_leader) {
        let (cfws, rest) = get_cfws(value)?;
        parts.push(cfws);
        value = rest;
    }
    let (tok, rest) = if value.starts_with("=?") {
        match get_encoded_word(value, "vtext") {
            Ok(found) => found,
            Err(_) => get_dot_atom_text(value)?,
        }
    } else {
        get_dot_atom_text(value)?
    };
    parts.push(tok);
    value = rest;
    if starts(value, is_cfws_leader) {
        let (cfws, rest) = get_cfws(value)?;
        parts.push(cfws);
        value = rest;
    }
    Ok((Tok::list(Kind::DotAtom, parts), value))
}

/// `get_word`: an atom or a quoted string, with the blanks before it inside.
fn get_word(value: &str) -> Parsed<'_> {
    let mut value = value;
    let mut leader = None;
    if starts(value, is_cfws_leader) {
        let (cfws, rest) = get_cfws(value)?;
        leader = Some(cfws);
        value = rest;
    }
    let (mut tok, rest) = match first(value) {
        None => return Err(()),
        Some('"') => get_quoted_string(value)?,
        Some(c) if is_special(c) => return Err(()),
        Some(_) => get_atom(value)?,
    };
    if let Some(leader) = leader {
        tok.parts.insert(0, leader);
    }
    Ok((tok, rest))
}

/// `get_phrase`.
fn get_phrase(value: &str) -> Parsed<'_> {
    let mut parts: Vec<Tok> = Vec::new();
    let mut value = value;
    if let Ok((word, rest)) = get_word(value) {
        parts.push(word);
        value = rest;
    }
    while !value.is_empty() && !starts(value, is_phrase_end) {
        if first(value) == Some('.') {
            parts.push(Tok::dot());
            value = after_first(value);
            continue;
        }
        match get_word(value) {
            Ok((word, rest)) => {
                parts.push(word);
                value = rest;
            }
            Err(()) if starts(value, is_cfws_leader) => {
                let (cfws, rest) = get_cfws(value)?;
                parts.push(cfws);
                value = rest;
            }
            Err(()) => return Err(()),
        }
    }
    Ok((Tok::list(Kind::Phrase, parts), value))
}

/// `get_local_part`.
fn get_local_part(value: &str) -> Parsed<'_> {
    let mut value = value;
    let mut leader = None;
    if starts(value, is_cfws_leader) {
        let (cfws, rest) = get_cfws(value)?;
        leader = Some(cfws);
        value = rest;
    }
    if value.is_empty() {
        return Err(());
    }
    let mut tok = match get_dot_atom(value) {
        Ok((tok, rest)) => {
            value = rest;
            tok
        }
        Err(()) => match get_word(value) {
            Ok((tok, rest)) => {
                value = rest;
                tok
            }
            Err(()) => {
                if first(value) != Some('\\') && starts(value, is_phrase_end) {
                    return Err(());
                }
                Tok::list(Kind::List, Vec::new())
            }
        },
    };
    if let Some(leader) = leader {
        tok.parts.insert(0, leader);
    }
    let mut local_part = Tok::list(Kind::LocalPart, vec![tok]);
    if !value.is_empty() && (first(value) == Some('\\') || !starts(value, is_phrase_end)) {
        let joined = local_part.render() + value;
        let (obs, rest) = get_obs_local_part(&joined)?;
        if rest.len() > value.len() || !value.ends_with(rest) {
            return Err(());
        }
        value = &value[value.len() - rest.len()..];
        local_part.parts[0] = obs;
    }
    Ok((local_part, value))
}

/// `get_obs_local_part`.
fn get_obs_local_part(value: &str) -> Parsed<'_> {
    let mut parts: Vec<Tok> = Vec::new();
    let mut value = value;
    while !value.is_empty() && (first(value) == Some('\\') || !starts(value, is_phrase_end)) {
        if first(value) == Some('.') {
            parts.push(Tok::dot());
            value = after_first(value);
            continue;
        }
        if first(value) == Some('\\') {
            parts.push(Tok::value("\\", "misplaced-special"));
            value = after_first(value);
            continue;
        }
        match get_word(value) {
            Ok((word, rest)) => {
                parts.push(word);
                value = rest;
            }
            Err(()) if starts(value, is_cfws_leader) => {
                let (cfws, rest) = get_cfws(value)?;
                parts.push(cfws);
                value = rest;
            }
            Err(()) => return Err(()),
        }
    }
    if parts.is_empty() {
        return Err(());
    }
    Ok((Tok::list(Kind::ObsLocalPart, parts), value))
}

/// `get_dtext`.
fn get_dtext(value: &str) -> (Tok, &str) {
    let (ptext, rest) = ptext_to_endchars(value, "[]");
    (Tok::value(ptext, "ptext"), rest)
}

/// `get_domain_literal`.
fn get_domain_literal(value: &str) -> Parsed<'_> {
    let mut parts: Vec<Tok> = Vec::new();
    let mut value = value;
    if starts(value, is_cfws_leader) {
        let (cfws, rest) = get_cfws(value)?;
        parts.push(cfws);
        value = rest;
    }
    if first(value) != Some('[') {
        return Err(());
    }
    value = after_first(value);
    parts.push(Tok::value("[", "domain-literal-start"));
    let end = |parts: &mut Vec<Tok>| parts.push(Tok::value("]", "domain-literal-end"));
    if value.is_empty() {
        end(&mut parts);
        return Ok((Tok::list(Kind::DomainLiteral, parts), value));
    }
    if starts(value, is_wsp) {
        let (fws, rest) = get_fws(value);
        parts.push(fws);
        value = rest;
    }
    let (dtext, rest) = get_dtext(value);
    parts.push(dtext);
    value = rest;
    if value.is_empty() {
        end(&mut parts);
        return Ok((Tok::list(Kind::DomainLiteral, parts), value));
    }
    if starts(value, is_wsp) {
        let (fws, rest) = get_fws(value);
        parts.push(fws);
        value = rest;
    }
    if value.is_empty() {
        end(&mut parts);
        return Ok((Tok::list(Kind::DomainLiteral, parts), value));
    }
    if first(value) != Some(']') {
        return Err(());
    }
    end(&mut parts);
    value = after_first(value);
    if starts(value, is_cfws_leader) {
        let (cfws, rest) = get_cfws(value)?;
        parts.push(cfws);
        value = rest;
    }
    Ok((Tok::list(Kind::DomainLiteral, parts), value))
}

/// `get_domain`.
fn get_domain(value: &str) -> Parsed<'_> {
    let mut value = value;
    let mut leader = None;
    if starts(value, is_cfws_leader) {
        let (cfws, rest) = get_cfws(value)?;
        leader = Some(cfws);
        value = rest;
    }
    if value.is_empty() {
        return Err(());
    }
    if first(value) == Some('[') {
        let (mut literal, rest) = get_domain_literal(value)?;
        if let Some(leader) = leader {
            literal.parts.insert(0, leader);
        }
        return Ok((Tok::list(Kind::Domain, vec![literal]), rest));
    }
    let (mut tok, rest) = match get_dot_atom(value) {
        Ok(found) => found,
        Err(()) => get_atom(value)?,
    };
    value = rest;
    if first(value) == Some('@') {
        return Err(());
    }
    if let Some(leader) = leader {
        tok.parts.insert(0, leader);
    }
    let mut parts = vec![tok];
    if first(value) == Some('.') {
        if parts[0].kind == Kind::DotAtom {
            parts = parts.remove(0).parts;
        }
        while first(value) == Some('.') {
            parts.push(Tok::dot());
            let (atom, rest) = get_atom(after_first(value))?;
            parts.push(atom);
            value = rest;
        }
    }
    Ok((Tok::list(Kind::Domain, parts), value))
}

/// `get_addr_spec`.
fn get_addr_spec(value: &str) -> Parsed<'_> {
    let (local, value) = get_local_part(value)?;
    let mut parts = vec![local];
    if first(value) != Some('@') {
        return Ok((Tok::list(Kind::AddrSpec, parts), value));
    }
    parts.push(Tok::value("@", "address-at-symbol"));
    let (domain, value) = get_domain(after_first(value))?;
    parts.push(domain);
    Ok((Tok::list(Kind::AddrSpec, parts), value))
}

/// `get_angle_addr`, but for an obsolete route, which no address this module writes holds.
fn get_angle_addr(value: &str) -> Parsed<'_> {
    let mut parts: Vec<Tok> = Vec::new();
    let mut value = value;
    if starts(value, is_cfws_leader) {
        let (cfws, rest) = get_cfws(value)?;
        parts.push(cfws);
        value = rest;
    }
    if first(value) != Some('<') {
        return Err(());
    }
    parts.push(Tok::value("<", "angle-addr-start"));
    value = after_first(value);
    if first(value) == Some('>') {
        parts.push(Tok::value(">", "angle-addr-end"));
        return Ok((Tok::list(Kind::AngleAddr, parts), after_first(value)));
    }
    let (spec, rest) = get_addr_spec(value)?;
    parts.push(spec);
    value = rest;
    if first(value) == Some('>') {
        value = after_first(value);
    }
    parts.push(Tok::value(">", "angle-addr-end"));
    if starts(value, is_cfws_leader) {
        let (cfws, rest) = get_cfws(value)?;
        parts.push(cfws);
        value = rest;
    }
    Ok((Tok::list(Kind::AngleAddr, parts), value))
}

/// `get_display_name`.
fn get_display_name(value: &str) -> Parsed<'_> {
    let (phrase, rest) = get_phrase(value)?;
    Ok((Tok::list(Kind::DisplayName, phrase.parts), rest))
}

/// `get_name_addr`.
fn get_name_addr(value: &str) -> Parsed<'_> {
    let mut value = value;
    if value.is_empty() {
        return Err(());
    }
    let mut leader = None;
    if starts(value, is_cfws_leader) {
        let (cfws, rest) = get_cfws(value)?;
        if rest.is_empty() {
            return Err(());
        }
        leader = Some(cfws);
        value = rest;
    }
    let mut parts: Vec<Tok> = Vec::new();
    if first(value) != Some('<') {
        if starts(value, is_phrase_end) {
            return Err(());
        }
        let (mut name, rest) = get_display_name(value)?;
        if rest.is_empty() {
            return Err(());
        }
        value = rest;
        if let Some(leader) = leader.take() {
            match name.parts.first_mut() {
                Some(word) if !word.is_terminal() => word.parts.insert(0, leader),
                _ => name.parts.insert(0, leader),
            }
        }
        parts.push(name);
    }
    let (mut angle, rest) = get_angle_addr(value)?;
    if let Some(leader) = leader {
        angle.parts.insert(0, leader);
    }
    parts.push(angle);
    Ok((Tok::list(Kind::NameAddr, parts), rest))
}

/// `get_mailbox`.
fn get_mailbox(value: &str) -> Parsed<'_> {
    let (tok, rest) = match get_name_addr(value) {
        Ok(found) => found,
        Err(()) => get_addr_spec(value)?,
    };
    Ok((Tok::list(Kind::Mailbox, vec![tok]), rest))
}

/// `get_invalid_mailbox`.
fn get_invalid_mailbox(value: &str) -> Parsed<'_> {
    let mut parts: Vec<Tok> = Vec::new();
    let mut value = value;
    while !value.is_empty() && first(value) != Some(',') {
        if let Some(c) = first(value).filter(|c| is_phrase_end(*c)) {
            parts.push(Tok::value(c.to_string(), "misplaced-special"));
            value = after_first(value);
        } else {
            let (phrase, rest) = get_phrase(value)?;
            parts.push(phrase);
            value = rest;
        }
    }
    Ok((Tok::list(Kind::InvalidMailbox, parts), value))
}

/// `get_address`: a group needs a `:` after its name, which only a quoted name holds in an address this module
/// writes, so each is a mailbox.
fn get_address(value: &str) -> Parsed<'_> {
    let (mailbox, rest) = get_mailbox(value)?;
    Ok((Tok::list(Kind::Address, vec![mailbox]), rest))
}

/// `get_address_list`.
pub(super) fn get_address_list(value: &str) -> Result<Tok, ()> {
    let mut parts: Vec<Tok> = Vec::new();
    let mut value = value;
    while !value.is_empty() {
        match get_address(value) {
            Ok((address, rest)) => {
                parts.push(address);
                value = rest;
            }
            Err(()) if starts(value, is_cfws_leader) => {
                let (leader, rest) = get_cfws(value)?;
                value = rest;
                if value.is_empty() || first(value) == Some(',') {
                    parts.push(leader);
                } else {
                    let (mut invalid, rest) = get_invalid_mailbox(value)?;
                    invalid.parts.insert(0, leader);
                    parts.push(Tok::list(Kind::Address, vec![invalid]));
                    value = rest;
                }
            }
            Err(()) if first(value) == Some(',') => {}
            Err(()) => {
                let (invalid, rest) = get_invalid_mailbox(value)?;
                parts.push(Tok::list(Kind::Address, vec![invalid]));
                value = rest;
            }
        }
        if !value.is_empty() && first(value) != Some(',') {
            let (invalid, rest) = get_invalid_mailbox(value)?;
            match parts.last_mut().and_then(|a| a.parts.first_mut()) {
                Some(mailbox) => mailbox.parts.extend(invalid.parts),
                None => return Err(()),
            }
            value = rest;
        }
        if !value.is_empty() {
            parts.push(Tok::list_separator());
            value = after_first(value);
        }
    }
    Ok(Tok::list(Kind::AddressList, parts))
}

/// `get_no_fold_literal`.
fn get_no_fold_literal(value: &str) -> Parsed<'_> {
    if first(value) != Some('[') {
        return Err(());
    }
    let (dtext, rest) = get_dtext(after_first(value));
    if first(rest) != Some(']') {
        return Err(());
    }
    let parts = vec![Tok::value("[", "no-fold-literal-start"), dtext, Tok::value("]", "no-fold-literal-end")];
    Ok((Tok::list(Kind::NoFoldLiteral, parts), after_first(rest)))
}

/// `get_msg_id`.
fn get_msg_id(value: &str) -> Parsed<'_> {
    let mut parts: Vec<Tok> = Vec::new();
    let mut value = value;
    if starts(value, is_cfws_leader) {
        let (cfws, rest) = get_cfws(value)?;
        parts.push(cfws);
        value = rest;
    }
    if first(value) != Some('<') {
        return Err(());
    }
    parts.push(Tok::value("<", "msg-id-start"));
    value = after_first(value);
    let (left, rest) = match get_dot_atom_text(value) {
        Ok(found) => found,
        Err(()) => get_obs_local_part(value)?,
    };
    parts.push(left);
    value = rest;
    if first(value) != Some('@') {
        if first(value) == Some('>') {
            parts.push(Tok::value(">", "msg-id-end"));
            value = after_first(value);
        }
        return Ok((Tok::list(Kind::MsgId, parts), value));
    }
    parts.push(Tok::value("@", "address-at-symbol"));
    value = after_first(value);
    let (right, rest) = match get_dot_atom_text(value) {
        Ok(found) => found,
        Err(()) => match get_no_fold_literal(value) {
            Ok(found) => found,
            Err(()) => get_domain(value)?,
        },
    };
    parts.push(right);
    value = rest;
    if first(value) == Some('>') {
        value = after_first(value);
    }
    parts.push(Tok::value(">", "msg-id-end"));
    if starts(value, is_cfws_leader) {
        let (cfws, rest) = get_cfws(value)?;
        parts.push(cfws);
        value = rest;
    }
    Ok((Tok::list(Kind::MsgId, parts), value))
}

/// `parse_message_id`.
pub(super) fn parse_message_id(value: &str) -> Tok {
    match get_msg_id(value) {
        Ok((msg_id, _)) => Tok::list(Kind::MsgId, vec![msg_id]),
        Err(()) => Tok::list(Kind::MsgId, get_unstructured(value).parts),
    }
}

/// `parse_message_ids`.
pub(super) fn parse_message_ids(value: &str) -> Tok {
    let mut parts: Vec<Tok> = Vec::new();
    let mut value = value;
    while !value.is_empty() {
        if first(value) == Some(',') {
            parts.push(Tok::space(" ", "invalid-comma-replacement"));
            value = after_first(value);
            continue;
        }
        match get_msg_id(value) {
            Ok((msg_id, rest)) => {
                parts.push(msg_id);
                value = rest;
            }
            Err(()) => {
                parts.push(Tok::list(Kind::MsgId, get_unstructured(value).parts));
                break;
            }
        }
    }
    Tok::list(Kind::MessageIdList, parts)
}

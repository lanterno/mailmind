//! A header's value as Python's `email._header_value_parser` holds it, a tree of tokens, and the tree folded into
//! lines as `email.policy.SMTP` folds it: 78 columns, CRLF, and no UTF-8 in a header, so what isn't ASCII goes out as
//! RFC 2047 encoded words, or as RFC 2231 in a MIME parameter (`_refold_parse_tree`, `_fold_as_ew`,
//! `_fold_mime_parameters`).

use std::collections::VecDeque;

use super::parse;

/// `policy.max_line_length`.
pub(super) const MAX_LINE: usize = 78;
/// `policy.linesep`.
pub(super) const LINESEP: &str = "\r\n";

/// `SPECIALS`: what ends an atom, beside whitespace.
pub(super) const SPECIALS: &str = "()<>@,:;.\"[]\\";
const WSP: [char; 2] = [' ', '\t'];

/// Which of the parser's classes a token is. A terminal holds text; the rest hold tokens.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum Kind {
    /// `ValueTerminal`.
    Value,
    /// `WhiteSpaceTerminal`.
    Space,
    /// `EWWhiteSpaceTerminal`: the space between two encoded words, which reads as nothing.
    EwSpace,
    /// The `ListSeparator` terminal, a comma that is never encoded and never a place to break.
    ListSeparator,
    /// A `TokenList` with nothing of its own.
    List,
    Unstructured,
    Phrase,
    Cfws,
    Atom,
    Token,
    EncodedWord,
    QuotedString,
    BareQuotedString,
    Comment,
    AddressList,
    Address,
    Mailbox,
    InvalidMailbox,
    NameAddr,
    AngleAddr,
    Domain,
    DotAtom,
    DotAtomText,
    NoFoldLiteral,
    AddrSpec,
    ObsLocalPart,
    DisplayName,
    LocalPart,
    DomainLiteral,
    MimeParameters,
    ContentType,
    ContentDisposition,
    ContentTransferEncoding,
    MimeVersion,
    HeaderLabel,
    MsgId,
    MessageIdList,
    Header,
}

/// A token of a parse tree: a terminal's text and `token_type`, or a list's tokens.
#[derive(Clone, Debug)]
pub(super) struct Tok {
    pub kind: Kind,
    /// A terminal's `token_type` (`ptext`, `vtext`, `fws`, `dot`, ...); a list's is its kind's.
    pub ty: &'static str,
    pub text: String,
    pub parts: Vec<Tok>,
    /// The decoded parameters of `MimeParameters`, in the order first seen.
    pub params: Vec<(String, String)>,
}

impl Tok {
    /// `ValueTerminal(text, ty)`.
    pub fn value(text: impl Into<String>, ty: &'static str) -> Tok {
        Tok { kind: Kind::Value, ty, text: text.into(), parts: Vec::new(), params: Vec::new() }
    }

    /// `WhiteSpaceTerminal(text, ty)`.
    pub fn space(text: impl Into<String>, ty: &'static str) -> Tok {
        Tok { kind: Kind::Space, ty, text: text.into(), parts: Vec::new(), params: Vec::new() }
    }

    /// `ListSeparator`.
    pub fn list_separator() -> Tok {
        Tok { kind: Kind::ListSeparator, ty: "list-separator", text: ",".into(), parts: Vec::new(), params: Vec::new() }
    }

    /// `DOT`.
    pub fn dot() -> Tok {
        Tok::value(".", "dot")
    }

    pub fn list(kind: Kind, parts: Vec<Tok>) -> Tok {
        Tok { kind, ty: "", text: String::new(), parts, params: Vec::new() }
    }

    /// `MimeParameters`, by its decoded parameters.
    pub fn mime_parameters(params: Vec<(String, String)>) -> Tok {
        Tok { kind: Kind::MimeParameters, ty: "", text: String::new(), parts: Vec::new(), params }
    }

    pub fn is_terminal(&self) -> bool {
        matches!(self.kind, Kind::Value | Kind::Space | Kind::EwSpace | Kind::ListSeparator)
    }

    /// The token's `token_type`, for the checks the parser makes on one.
    pub fn token_type(&self) -> &'static str {
        match self.kind {
            Kind::Value | Kind::Space | Kind::EwSpace | Kind::ListSeparator => self.ty,
            Kind::Cfws => "cfws",
            Kind::EncodedWord => "encoded-word",
            Kind::QuotedString => "quoted-string",
            Kind::BareQuotedString => "bare-quoted-string",
            Kind::Comment => "comment",
            Kind::DotAtom => "dot-atom",
            Kind::NameAddr => "name-addr",
            Kind::AddrSpec => "addr-spec",
            Kind::Domain => "domain",
            Kind::MimeParameters => "mime-parameters",
            _ => "",
        }
    }

    /// `str(token)`.
    pub fn render(&self) -> String {
        match self.kind {
            Kind::Value | Kind::Space | Kind::ListSeparator => self.text.clone(),
            Kind::EwSpace => String::new(),
            Kind::BareQuotedString => quote_string(&self.parts.iter().map(Tok::render).collect::<String>()),
            Kind::Comment => {
                let mut out = String::from("(");
                for p in &self.parts {
                    if p.kind == Kind::Comment {
                        out.push_str(&p.render());
                    } else {
                        out.push_str(&parenthesis_pairs(&p.render()));
                    }
                }
                out.push(')');
                out
            }
            Kind::MimeParameters => {
                let params: Vec<String> = self
                    .params
                    .iter()
                    .map(
                        |(name, value)| {
                            if value.is_empty() { name.clone() } else { format!("{name}={}", quote_string(value)) }
                        },
                    )
                    .collect();
                let params = params.join("; ");
                if params.is_empty() { params } else { format!(" {params}") }
            }
            _ => self.parts.iter().map(Tok::render).collect(),
        }
    }

    /// `as_ew_allowed`: whether the token may go out as an encoded word.
    pub fn as_ew_allowed(&self) -> bool {
        match self.kind {
            Kind::ListSeparator => false,
            Kind::Value | Kind::Space | Kind::EwSpace => true,
            Kind::DotAtomText => true,
            Kind::Domain
            | Kind::NoFoldLiteral
            | Kind::AddrSpec
            | Kind::ObsLocalPart
            | Kind::LocalPart
            | Kind::DomainLiteral
            | Kind::ContentType
            | Kind::ContentDisposition
            | Kind::ContentTransferEncoding
            | Kind::HeaderLabel
            | Kind::MsgId => false,
            _ => self.parts.iter().all(Tok::as_ew_allowed),
        }
    }

    /// `syntactic_break`: whether the token is a place a line may break before.
    fn syntactic_break(&self) -> bool {
        !matches!(self.kind, Kind::ListSeparator | Kind::MimeParameters | Kind::ContentType | Kind::ContentDisposition)
    }

    /// `startswith_fws`.
    fn startswith_fws(&self) -> bool {
        match self.kind {
            Kind::Space | Kind::EwSpace => self.text.starts_with(WSP),
            Kind::Value | Kind::ListSeparator => false,
            _ => self.parts.first().is_some_and(Tok::startswith_fws),
        }
    }

    /// `token.fold(policy=policy)`.
    fn fold(&self) -> String {
        if self.kind == Kind::MsgId {
            return self.render() + LINESEP;
        }
        refold(self)
    }
}

/// `quote_string`.
pub(super) fn quote_string(value: &str) -> String {
    format!("\"{}\"", quoted_pairs(value))
}

/// `make_quoted_pairs`.
fn quoted_pairs(value: &str) -> String {
    value.replace('\\', "\\\\").replace('"', "\\\"")
}

/// `make_parenthesis_pairs`.
fn parenthesis_pairs(value: &str) -> String {
    value.replace('\\', "\\\\").replace('(', "\\(").replace(')', "\\)")
}

/// `len()` of a string, in characters.
pub(super) fn clen(s: &str) -> usize {
    s.chars().count()
}

/// The characters of `s` from `from` on.
fn chars_from(s: &str, from: usize) -> &str {
    s.char_indices().nth(from).map_or("", |(at, _)| &s[at..])
}

/// The first `upto` characters of `s`.
fn chars_upto(s: &str, upto: usize) -> &str {
    s.char_indices().nth(upto).map_or(s, |(at, _)| &s[..at])
}

fn last(lines: &mut [String]) -> &mut String {
    let at = lines.len() - 1;
    &mut lines[at]
}

/// `_steal_trailing_WSP_if_exists`.
fn steal_trailing_wsp(lines: &mut Vec<String>) -> String {
    let mut wsp = String::new();
    if let Some(line) = lines.last_mut()
        && let Some(c) = line.chars().last()
        && WSP.contains(&c)
    {
        line.pop();
        wsp.push(c);
        if line.is_empty() {
            lines.pop();
        }
    }
    wsp
}

enum Item {
    Tok(Tok),
    /// `end_ew_not_allowed`: where the tokens of one that may not be encoded end.
    EndBlocked,
}

/// `_refold_parse_tree`: the tree's text in lines of at most 78 columns where it can, each line ending CRLF.
pub(super) fn refold(tree: &Tok) -> String {
    let maxlen = MAX_LINE as i64;
    let mut lines: Vec<String> = vec![String::new()];
    let mut last_word_is_ew = false;
    let mut last_ew: Option<usize> = None;
    let mut blocked = 0usize;
    let mut want_encoding = false;
    let mut parts: VecDeque<Item> = tree.parts.iter().cloned().map(Item::Tok).collect();
    while let Some(item) = parts.pop_front() {
        let part = match item {
            Item::EndBlocked => {
                blocked -= 1;
                continue;
            }
            Item::Tok(part) => part,
        };
        let tstr = part.render();
        if !want_encoding {
            want_encoding = if part.is_terminal() && matches!(part.ty, "ptext" | "vtext") {
                tstr.chars().any(|c| SPECIALS.contains(c) || c == '\n' || c == '\r')
            } else {
                tstr.contains(['\n', '\r'])
            };
        }
        if !tstr.is_ascii() {
            want_encoding = true;
        }
        if part.kind == Kind::MimeParameters {
            fold_mime_parameters(&part.params, &mut lines);
            last_word_is_ew = false;
            continue;
        }
        if want_encoding && blocked == 0 {
            if !part.as_ew_allowed() {
                want_encoding = false;
                last_ew = None;
                if part.syntactic_break() {
                    let folded = part.fold();
                    let encoded = &folded[..folded.len() - LINESEP.len()];
                    if !encoded.contains(LINESEP) {
                        if clen(encoded) as i64 > maxlen - clen(last(&mut lines)) as i64 {
                            let newline = steal_trailing_wsp(&mut lines);
                            lines.push(newline);
                        }
                        last(&mut lines).push_str(encoded);
                        last_word_is_ew = false;
                        continue;
                    }
                }
            }
            if !part.is_terminal() {
                for p in part.parts.into_iter().rev() {
                    parts.push_front(Item::Tok(p));
                }
                want_encoding = false;
                continue;
            } else if part.as_ew_allowed() {
                last_ew = fold_as_ew(&tstr, &mut lines, last_ew, true, last_word_is_ew);
                last_word_is_ew = true;
                want_encoding = false;
                continue;
            } else {
                last_ew = None;
                want_encoding = false;
            }
        }
        if clen(&tstr) as i64 <= maxlen - clen(last(&mut lines)) as i64 {
            last(&mut lines).push_str(&tstr);
            last_word_is_ew = last_word_is_ew && tstr.trim_matches(WSP).is_empty();
            continue;
        }
        if part.syntactic_break() && (clen(&tstr) as i64) < maxlen {
            let newline = steal_trailing_wsp(&mut lines);
            if !newline.is_empty() || part.startswith_fws() {
                lines.push(newline + &tstr);
                last_word_is_ew = last_word_is_ew && last(&mut lines).trim_matches(WSP).is_empty();
                last_ew = None;
                continue;
            }
        }
        if !part.is_terminal() {
            let mut newparts: Vec<Item> = match part.kind {
                Kind::BareQuotedString => std::iter::once(Tok::value("\"", "ptext"))
                    .chain(part.parts.iter().map(|p| Tok::value(quoted_pairs(&p.render()), "ptext")))
                    .chain(std::iter::once(Tok::value("\"", "ptext")))
                    .map(Item::Tok)
                    .collect(),
                Kind::Comment => std::iter::once(Tok::value("(", "ptext"))
                    .chain(part.parts.iter().map(|p| {
                        if p.is_terminal() && p.ty == "ptext" {
                            Tok::value(parenthesis_pairs(&p.render()), "ptext")
                        } else {
                            p.clone()
                        }
                    }))
                    .chain(std::iter::once(Tok::value(")", "ptext")))
                    .map(Item::Tok)
                    .collect(),
                _ => part.parts.iter().cloned().map(Item::Tok).collect(),
            };
            if !part.as_ew_allowed() {
                blocked += 1;
                newparts.push(Item::EndBlocked);
            }
            for p in newparts.into_iter().rev() {
                parts.push_front(p);
            }
            continue;
        }
        if part.as_ew_allowed() && blocked == 0 {
            parts.push_front(Item::Tok(part));
            want_encoding = true;
            continue;
        }
        let newline = steal_trailing_wsp(&mut lines);
        if !newline.is_empty() || part.startswith_fws() {
            lines.push(newline + &tstr);
        } else {
            last(&mut lines).push_str(&tstr);
        }
        last_word_is_ew = last_word_is_ew && tstr.trim_matches(WSP).is_empty();
    }
    lines.join(LINESEP) + LINESEP
}

/// `_fold_as_ew`: `to_encode` added to the lines as encoded words, combined with the encoded word already on the
/// last line where it may be. Answers where the last line's encoded word now starts.
fn fold_as_ew(
    to_encode: &str,
    lines: &mut Vec<String>,
    last_ew: Option<usize>,
    combine: bool,
    last_word_is_ew: bool,
) -> Option<usize> {
    let maxlen = MAX_LINE as i64;
    let mut to_encode = to_encode.to_owned();
    if let (Some(at), true) = (last_ew, combine) {
        let line = last(lines);
        let tail = chars_from(line, at).to_owned();
        to_encode = parse::get_unstructured(&(tail + &to_encode)).render();
        *line = chars_upto(line, at).to_owned();
    } else if last_word_is_ew {
        let line = last(lines);
        let kept = line.trim_end_matches(WSP).len();
        let leading = line[kept..].to_owned();
        line.truncate(kept);
        if !leading.is_empty() {
            line.push(' ');
        }
        to_encode = leading + &to_encode;
    } else if let Some(first) = to_encode.chars().next()
        && WSP.contains(&first)
    {
        to_encode.remove(0);
        if clen(last(lines)) as i64 == maxlen {
            let newline = steal_trailing_wsp(lines);
            lines.push(newline);
        }
        last(lines).push(first);
    }
    let mut trailing = None;
    if let Some(c) = to_encode.chars().last()
        && WSP.contains(&c)
    {
        to_encode.pop();
        trailing = Some(c);
    }
    let mut new_last_ew = last_ew.unwrap_or_else(|| clen(last(lines)));
    // The chrome of `=?utf-8?q?...?=`.
    let chrome_len = 12i64;
    let mut rest: Vec<char> = to_encode.chars().collect();
    while !rest.is_empty() {
        let remaining = maxlen - clen(last(lines)) as i64;
        let text_space = remaining - chrome_len;
        if text_space <= 0 {
            let newline = steal_trailing_wsp(lines);
            lines.push(if newline.is_empty() { " ".to_owned() } else { newline });
            new_last_ew = clen(last(lines));
            continue;
        }
        let mut take = (text_space as usize).min(rest.len());
        let mut encoded = ew_encode(&rest[..take].iter().collect::<String>());
        while clen(&encoded) as i64 - remaining > 0 && take > 0 {
            take -= 1;
            encoded = ew_encode(&rest[..take].iter().collect::<String>());
        }
        last(lines).push_str(&encoded);
        rest.drain(..take);
        if !rest.is_empty() {
            lines.push(" ".to_owned());
            new_last_ew = clen(last(lines));
        }
    }
    if let Some(c) = trailing {
        last(lines).push(c);
    }
    combine.then_some(new_last_ew)
}

/// `_ew.encode(string, charset='utf-8')`: Q or B, whichever is shorter, Q while it is fewer than 5 longer.
pub(super) fn ew_encode(text: &str) -> String {
    let bytes = text.as_bytes();
    let qlen: usize = bytes.iter().map(|b| if q_safe(*b) || *b == b' ' { 1 } else { 3 }).sum();
    let blen = bytes.len() / 3 * 4 + if bytes.len().is_multiple_of(3) { 0 } else { 4 };
    if (qlen as i64) - (blen as i64) < 5 {
        let mut out = String::from("=?utf-8?q?");
        for &b in bytes {
            if b == b' ' {
                out.push('_');
            } else if q_safe(b) {
                out.push(char::from(b));
            } else {
                out.push_str(&format!("={b:02X}"));
            }
        }
        out + "?="
    } else {
        format!("=?utf-8?b?{}?=", super::mime::base64(bytes))
    }
}

/// What Q encoding writes as itself.
fn q_safe(b: u8) -> bool {
    b.is_ascii_alphanumeric() || b"-!*+/".contains(&b)
}

/// `_fold_mime_parameters`: each parameter after a `;`, on the line if it fits, else on one of its own, else in
/// RFC 2231 sections.
fn fold_mime_parameters(params: &[(String, String)], lines: &mut Vec<String>) {
    for (name, value) in params {
        let mut maxlen = MAX_LINE as i64;
        if !last(lines).trim_end_matches(mm_kernel::text::is_space).ends_with(';') {
            last(lines).push(';');
        }
        let ascii = value.is_ascii();
        let charset = if ascii { "us-ascii" } else { "utf-8" };
        let tstr = if ascii {
            format!("{name}={}", quote_string(value))
        } else {
            format!("{name}*={charset}''{}", url_quote(value))
        };
        if ((clen(last(lines)) + clen(&tstr) + 1) as i64) < maxlen {
            let line = last(lines);
            line.push(' ');
            line.push_str(&tstr);
            continue;
        } else if clen(&tstr) as i64 + 2 <= maxlen {
            lines.push(format!(" {tstr}"));
            continue;
        }
        let mut section = 0usize;
        let mut extra_chrome = format!("{charset}''");
        let mut rest: Vec<char> = value.chars().collect();
        while !rest.is_empty() {
            let chrome_len = (clen(name) + section.to_string().len() + 3 + extra_chrome.len()) as i64;
            if maxlen <= chrome_len + 3 {
                maxlen = MAX_LINE as i64;
            }
            let maxchars = maxlen - chrome_len - 2;
            let mut splitpoint = maxchars.max(1) as usize;
            let encoded = loop {
                let encoded = url_quote(&rest[..splitpoint.min(rest.len())].iter().collect::<String>());
                if encoded.len() as i64 <= maxchars || splitpoint == 1 {
                    break encoded;
                }
                splitpoint -= 1;
            };
            lines.push(format!(" {name}*{section}*={extra_chrome}{encoded}"));
            extra_chrome.clear();
            section += 1;
            rest.drain(..splitpoint.min(rest.len()));
            if !rest.is_empty() {
                last(lines).push(';');
            }
        }
    }
}

/// `urllib.parse.quote(value, safe='')`: UTF-8, each byte but the unreserved ones as `%XX`.
pub(super) fn url_quote(value: &str) -> String {
    let mut out = String::new();
    for &b in value.as_bytes() {
        if b.is_ascii_alphanumeric() || b"_.-~".contains(&b) {
            out.push(char::from(b));
        } else {
            out.push_str(&format!("%{b:02X}"));
        }
    }
    out
}

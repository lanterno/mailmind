//! Addresses as a person types them (`mailmind.kernel.addresses`): split as Python's `email.utils.getaddresses`
//! splits them in its strict mode, validated, IDNA-normalized, and free of what must never reach a header.
//!
//! The splitting is `email._parseaddr.AddrlistClass`, step for step, over the text's characters. A domain that is
//! ASCII goes through IDNA's fast path exactly. One that isn't is lowered label by label and Punycode-encoded:
//! Python ran nameprep first, whose Unicode tables (NFKC and the prohibited characters) aren't here, so a domain
//! that needs more of nameprep than lowering reads differently, or is refused here where Python took it.

use crate::text::{is_space, strip};

pub const MAX_RECIPIENTS: usize = 100;

/// Never in a header: C0 and C1 controls, bidirectional overrides and isolates, zero-width characters, and the line
/// and paragraph separators.
fn is_danger(c: char) -> bool {
    matches!(c,
        '\u{0}'..='\u{1f}' | '\u{7f}'..='\u{9f}' | '\u{200b}'..='\u{200f}' | '\u{202a}'..='\u{202e}' | '\u{2060}'
        | '\u{2066}'..='\u{2069}' | '\u{2028}' | '\u{2029}' | '\u{feff}')
}

/// Never in an addr-spec: whitespace, separators, brackets or controls.
fn is_addr_bad(c: char) -> bool {
    is_space(c) || ",;:<>()[]\\\"".contains(c) || c <= '\u{1f}' || c == '\u{7f}'
}

/// `clean`: a free-text header value (a subject, a display name) without what is dangerous in one.
pub fn clean(s: Option<&str>) -> String {
    let kept: String = s.unwrap_or_default().replace('\t', " ").chars().filter(|c| !is_danger(*c)).collect();
    strip(&kept).to_owned()
}

/// `idna_domain`: the domain's ASCII (IDNA) form, or None where Python raised ValueError.
pub fn idna_domain(domain: Option<&str>) -> Option<String> {
    let domain = strip(domain.unwrap_or_default()).trim_end_matches('.');
    if domain.is_empty() || (!domain.contains('.') && domain.to_lowercase() != "localhost") {
        return None;
    }
    idna_encode(domain)
}

/// `str.encode("idna")`, as far as this module goes (see the module's note).
fn idna_encode(input: &str) -> Option<String> {
    if input.is_ascii() {
        let labels: Vec<&str> = input.split('.').collect();
        let (_, but_last) = labels.split_last()?;
        if but_last.iter().any(|l| l.is_empty()) || labels.iter().any(|l| l.len() >= 64) {
            return None;
        }
        return Some(input.to_owned());
    }
    // The dots IDNA splits at: the full stop and its ideographic, fullwidth and halfwidth forms.
    let mut labels: Vec<&str> = input.split(['.', '\u{3002}', '\u{ff0e}', '\u{ff61}']).collect();
    let trailing = labels.last().is_some_and(|l| l.is_empty());
    if trailing {
        labels.pop();
    }
    let mut out = String::new();
    for label in labels {
        if !out.is_empty() {
            out.push('.');
        }
        out.push_str(&to_ascii(label)?);
    }
    if trailing {
        out.push('.');
    }
    Some(out)
}

/// `encodings.idna.ToASCII` with nameprep as lowering alone.
fn to_ascii(label: &str) -> Option<String> {
    if label.is_ascii() {
        return (!label.is_empty() && label.len() < 64).then(|| label.to_owned());
    }
    let label = label.to_lowercase();
    if label.is_ascii() {
        return (!label.is_empty() && label.len() < 64).then_some(label);
    }
    if label.starts_with("xn--") {
        return None;
    }
    let encoded = format!("xn--{}", punycode_encode(&label)?);
    (encoded.len() < 64).then_some(encoded)
}

/// `str.encode("ascii").decode("idna")` of a domain: each `xn--` label Punycode-decoded, the rest as they are. None
/// where Python raised UnicodeError.
pub fn idna_decode(domain: &str) -> Option<String> {
    if !domain.is_ascii() {
        return None;
    }
    let labels: Vec<String> = domain
        .split('.')
        .map(|label| match label.get(..4) {
            Some(prefix) if prefix.eq_ignore_ascii_case("xn--") => punycode_decode(&label[4..].to_lowercase()),
            _ => Some(label.to_lowercase()),
        })
        .collect::<Option<_>>()?;
    Some(labels.join("."))
}

const BASE: u32 = 36;
const T_MIN: u32 = 1;
const T_MAX: u32 = 26;
const SKEW: u32 = 38;
const DAMP: u32 = 700;
const INITIAL_BIAS: u32 = 72;
const INITIAL_N: u32 = 128;

fn adapt(delta: u32, points: u32, first: bool) -> u32 {
    let mut delta = if first { delta / DAMP } else { delta / 2 };
    delta += delta / points;
    let mut k = 0;
    while delta > ((BASE - T_MIN) * T_MAX) / 2 {
        delta /= BASE - T_MIN;
        k += BASE;
    }
    k + (BASE - T_MIN + 1) * delta / (delta + SKEW)
}

fn digit(d: u32) -> char {
    char::from(if d < 26 { b'a' + d as u8 } else { b'0' + (d - 26) as u8 })
}

/// RFC 3492's encoding.
fn punycode_encode(input: &str) -> Option<String> {
    let points: Vec<u32> = input.chars().map(u32::from).collect();
    let mut out: String = input.chars().filter(char::is_ascii).collect();
    let basic = out.len() as u32;
    if basic > 0 {
        out.push('-');
    }
    let (mut n, mut delta, mut bias, mut handled) = (INITIAL_N, 0u32, INITIAL_BIAS, basic);
    while (handled as usize) < points.len() {
        let m = points.iter().copied().filter(|&p| p >= n).min()?;
        delta = delta.checked_add((m - n).checked_mul(handled + 1)?)?;
        n = m;
        for &p in &points {
            if p < n {
                delta = delta.checked_add(1)?;
            }
            if p == n {
                let mut q = delta;
                let mut k = BASE;
                loop {
                    let t = if k <= bias {
                        T_MIN
                    } else if k >= bias + T_MAX {
                        T_MAX
                    } else {
                        k - bias
                    };
                    if q < t {
                        break;
                    }
                    out.push(digit(t + (q - t) % (BASE - t)));
                    q = (q - t) / (BASE - t);
                    k += BASE;
                }
                out.push(digit(q));
                bias = adapt(delta, handled + 1, handled == basic);
                delta = 0;
                handled += 1;
            }
        }
        delta += 1;
        n += 1;
    }
    Some(out)
}

/// RFC 3492's decoding.
fn punycode_decode(input: &str) -> Option<String> {
    let (basic, rest) = input.rfind('-').map_or(("", input), |at| (&input[..at], &input[at + 1..]));
    let mut out: Vec<char> = basic.chars().collect();
    if !basic.is_ascii() {
        return None;
    }
    let (mut n, mut i, mut bias) = (INITIAL_N, 0u32, INITIAL_BIAS);
    let mut digits = rest.bytes().peekable();
    while digits.peek().is_some() {
        let old = i;
        let mut w = 1u32;
        let mut k = BASE;
        loop {
            let d = match digits.next()? {
                c @ b'a'..=b'z' => u32::from(c - b'a'),
                c @ b'A'..=b'Z' => u32::from(c - b'A'),
                c @ b'0'..=b'9' => u32::from(c - b'0') + 26,
                _ => return None,
            };
            i = i.checked_add(d.checked_mul(w)?)?;
            let t = if k <= bias {
                T_MIN
            } else if k >= bias + T_MAX {
                T_MAX
            } else {
                k - bias
            };
            if d < t {
                break;
            }
            w = w.checked_mul(BASE - t)?;
            k += BASE;
        }
        let length = out.len() as u32 + 1;
        bias = adapt(i - old, length, old == 0);
        n = n.checked_add(i / length)?;
        i %= length;
        out.insert(i as usize, char::from_u32(n)?);
        i += 1;
    }
    Some(out.into_iter().collect())
}

/// `validate_pair`: (the display name made clean, the addr-spec as local@IDNA-domain), or None where Python raised.
pub fn validate_pair(name: &str, addr: &str) -> Option<(String, String)> {
    let addr = strip(addr);
    if addr.is_empty() || addr.matches('@').count() != 1 || addr.chars().any(is_danger) {
        return None;
    }
    let (local, domain) = addr.rsplit_once('@')?;
    if local.is_empty() || local.chars().any(is_addr_bad) || domain.chars().any(is_addr_bad) {
        return None;
    }
    let ascii = idna_domain(Some(domain))?;
    if ascii.contains('@') || ascii.chars().any(is_addr_bad) {
        return None;
    }
    Some((clean(Some(name)), format!("{local}@{ascii}")))
}

/// `parse_addr_list` of a list of tokens: each (name, addr-spec), the first of each addr-spec whatever its case, or
/// None where one of them isn't an address.
pub fn parse_addr_list(tokens: &[&str]) -> Option<Vec<(String, String)>> {
    let given: Vec<&str> = tokens.iter().copied().filter(|t| !t.is_empty()).collect();
    let mut out: Vec<(String, String)> = Vec::new();
    let mut seen = std::collections::HashSet::new();
    for (name, addr) in getaddresses(&given) {
        let (name, spec) = validate_pair(&name, &addr)?;
        if seen.insert(spec.to_lowercase()) {
            out.push((name, spec));
        }
    }
    Some(out)
}

/// `email.utils.getaddresses(fieldvalues, strict=True)`.
pub fn getaddresses(fieldvalues: &[&str]) -> Vec<(String, String)> {
    let values: Vec<String> =
        fieldvalues.iter().map(|v| if balanced(v) { (*v).to_owned() } else { "('', '')".to_owned() }).collect();
    let joined = values.join(", ");
    let result: Vec<(String, String)> = Parser::new(&joined)
        .addresses()
        .into_iter()
        .map(|(name, addr)| if addr.contains('[') { (String::new(), String::new()) } else { (name, addr) })
        .collect();
    let expected: usize = values.iter().map(|v| 1 + without_quoted_names(v).matches(',').count()).sum();
    if result.len() != expected {
        return vec![(String::new(), String::new())];
    }
    result
}

/// `_iter_escaped_chars`: each character with where it is, a backslash and what it escapes as one.
fn escaped(text: &str) -> Vec<(usize, String)> {
    let chars: Vec<char> = text.chars().collect();
    let mut out = Vec::new();
    let mut escape = false;
    for (pos, &c) in chars.iter().enumerate() {
        if escape {
            out.push((pos, format!("\\{c}")));
            escape = false;
        } else if c == '\\' {
            escape = true;
        } else {
            out.push((pos, c.to_string()));
        }
    }
    if escape {
        out.push((chars.len().saturating_sub(1), "\\".to_owned()));
    }
    out
}

/// `_strip_quoted_realnames`.
fn without_quoted_names(addr: &str) -> String {
    if !addr.contains('"') {
        return addr.to_owned();
    }
    let chars: Vec<char> = addr.chars().collect();
    let (mut start, mut open) = (0usize, None::<usize>);
    let mut out = String::new();
    for (pos, c) in escaped(addr) {
        if c == "\"" {
            match open {
                None => open = Some(pos),
                Some(at) => {
                    if start != at {
                        out.extend(&chars[start..at]);
                    }
                    start = pos + 1;
                    open = None;
                }
            }
        }
    }
    if start < chars.len() {
        out.extend(&chars[start..]);
    }
    out
}

/// `_check_parenthesis`.
fn balanced(addr: &str) -> bool {
    let mut opens = 0i64;
    for (_, c) in escaped(&without_quoted_names(addr)) {
        if c == "(" {
            opens += 1;
        } else if c == ")" {
            opens -= 1;
            if opens < 0 {
                return false;
            }
        }
    }
    opens == 0
}

const SPECIALS: &str = "()<>@,:;.\"[]";

/// `email._parseaddr.AddrlistClass`.
struct Parser {
    field: Vec<char>,
    pos: usize,
    comments: Vec<String>,
}

fn is_lws(c: char) -> bool {
    c == ' ' || c == '\t'
}

fn is_atom_end(c: char) -> bool {
    SPECIALS.contains(c) || is_lws(c) || c == '\r' || c == '\n'
}

fn is_phrase_end(c: char) -> bool {
    c != '.' && is_atom_end(c)
}

impl Parser {
    fn new(field: &str) -> Parser {
        Parser { field: field.chars().collect(), pos: 0, comments: Vec::new() }
    }

    fn at(&self) -> Option<char> {
        self.field.get(self.pos).copied()
    }

    fn more(&self) -> bool {
        self.pos < self.field.len()
    }

    fn addresses(&mut self) -> Vec<(String, String)> {
        let mut result = Vec::new();
        while self.more() {
            let found = self.address();
            if found.is_empty() {
                result.push((String::new(), String::new()));
            } else {
                result.extend(found);
            }
        }
        result
    }

    fn goto_next(&mut self) -> String {
        let mut ws = String::new();
        while let Some(c) = self.at() {
            if is_lws(c) || c == '\n' || c == '\r' {
                if c != '\n' && c != '\r' {
                    ws.push(c);
                }
                self.pos += 1;
            } else if c == '(' {
                let comment = self.comment();
                self.comments.push(comment);
            } else {
                break;
            }
        }
        ws
    }

    fn address(&mut self) -> Vec<(String, String)> {
        self.comments.clear();
        self.goto_next();
        let old_pos = self.pos;
        let old_comments = self.comments.clone();
        let phrases = self.phrases();
        self.goto_next();
        let mut found = Vec::new();
        match self.at() {
            None => {
                if let Some(first) = phrases.first() {
                    found.push((self.comments.join(" "), first.clone()));
                }
            }
            Some('.' | '@') => {
                self.pos = old_pos;
                self.comments = old_comments;
                let spec = self.addr_spec();
                found.push((self.comments.join(" "), spec));
            }
            Some(':') => {
                self.pos += 1;
                while self.more() {
                    self.goto_next();
                    if self.at() == Some(';') {
                        self.pos += 1;
                        break;
                    }
                    found.extend(self.address());
                }
            }
            Some('<') => {
                let route = self.route_addr();
                let name = if self.comments.is_empty() {
                    phrases.join(" ")
                } else {
                    format!("{} ({})", phrases.join(" "), self.comments.join(" "))
                };
                found.push((name, route));
            }
            Some(c) => {
                if let Some(first) = phrases.first() {
                    found.push((self.comments.join(" "), first.clone()));
                } else if SPECIALS.contains(c) {
                    self.pos += 1;
                }
            }
        }
        self.goto_next();
        if self.at() == Some(',') {
            self.pos += 1;
        }
        found
    }

    fn route_addr(&mut self) -> String {
        if self.at() != Some('<') {
            return String::new();
        }
        let mut expect_route = false;
        self.pos += 1;
        self.goto_next();
        let mut spec = String::new();
        while let Some(c) = self.at() {
            if expect_route {
                self.domain();
                expect_route = false;
            } else if c == '>' {
                self.pos += 1;
                break;
            } else if c == '@' {
                self.pos += 1;
                expect_route = true;
            } else if c == ':' {
                self.pos += 1;
            } else {
                spec = self.addr_spec();
                self.pos += 1;
                break;
            }
            self.goto_next();
        }
        spec
    }

    fn addr_spec(&mut self) -> String {
        let mut parts: Vec<String> = Vec::new();
        self.goto_next();
        while let Some(c) = self.at() {
            let mut keep_ws = true;
            if c == '.' {
                if parts.last().is_some_and(|p| p.trim().is_empty()) {
                    parts.pop();
                }
                parts.push(".".to_owned());
                self.pos += 1;
                keep_ws = false;
            } else if c == '"' {
                let quoted = self.quote();
                parts.push(format!("\"{}\"", quoted.replace('\\', "\\\\").replace('"', "\\\"")));
            } else if is_atom_end(c) {
                if parts.last().is_some_and(|p| p.trim().is_empty()) {
                    parts.pop();
                }
                break;
            } else {
                let atom = self.atom(is_atom_end);
                parts.push(atom);
            }
            let ws = self.goto_next();
            if keep_ws && !ws.is_empty() {
                parts.push(ws);
            }
        }
        if self.at() != Some('@') {
            return parts.concat();
        }
        parts.push("@".to_owned());
        self.pos += 1;
        self.goto_next();
        let domain = self.domain();
        if domain.is_empty() {
            return String::new();
        }
        parts.concat() + &domain
    }

    fn domain(&mut self) -> String {
        let mut parts: Vec<String> = Vec::new();
        while let Some(c) = self.at() {
            if is_lws(c) {
                self.pos += 1;
            } else if c == '(' {
                let comment = self.comment();
                self.comments.push(comment);
            } else if c == '[' {
                let literal = self.delimited('[', "]\r", false);
                parts.push(format!("[{literal}]"));
            } else if c == '.' {
                self.pos += 1;
                parts.push(".".to_owned());
            } else if c == '@' {
                return String::new();
            } else if is_atom_end(c) {
                break;
            } else {
                let atom = self.atom(is_atom_end);
                parts.push(atom);
            }
        }
        parts.concat()
    }

    fn delimited(&mut self, begin: char, ends: &str, comments: bool) -> String {
        if self.at() != Some(begin) {
            return String::new();
        }
        let mut out = String::new();
        let mut quote = false;
        self.pos += 1;
        while let Some(c) = self.at() {
            if quote {
                out.push(c);
                quote = false;
            } else if ends.contains(c) {
                self.pos += 1;
                break;
            } else if comments && c == '(' {
                let comment = self.comment();
                out.push_str(&comment);
                continue;
            } else if c == '\\' {
                quote = true;
            } else {
                out.push(c);
            }
            self.pos += 1;
        }
        out
    }

    fn quote(&mut self) -> String {
        self.delimited('"', "\"\r", false)
    }

    fn comment(&mut self) -> String {
        self.delimited('(', ")\r", true)
    }

    fn atom(&mut self, ends: fn(char) -> bool) -> String {
        let mut out = String::new();
        while let Some(c) = self.at() {
            if ends(c) {
                break;
            }
            out.push(c);
            self.pos += 1;
        }
        out
    }

    fn phrases(&mut self) -> Vec<String> {
        let mut out = Vec::new();
        while let Some(c) = self.at() {
            if is_lws(c) || c == '\r' || c == '\n' {
                self.pos += 1;
            } else if c == '"' {
                let quoted = self.quote();
                out.push(quoted);
            } else if c == '(' {
                let comment = self.comment();
                self.comments.push(comment);
            } else if is_phrase_end(c) {
                break;
            } else {
                let atom = self.atom(is_phrase_end);
                out.push(atom);
            }
        }
        out
    }
}

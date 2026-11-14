//! An iCalendar text (RFC 5545) read into its components, as the Python app's reader (`icalendar`'s parser, with
//! what `mailmind.calendar.adapters.icalendar_acl` leaves out of it) reads one. A stranger wrote it, so nothing here
//! fails on it but the one thing that made the Python app drop the whole text, an END with no BEGIN before it:
//!
//! - A line that won't read as a property is skipped, and so is a property outside any component.
//! - A VTIMEZONE is dropped once it ends: a TZID means what `invite::zone` says it means, never what the sender's
//!   VTIMEZONE says.
//! - A text cut short keeps its outermost component, with what of it was finished.
//!
//! Each property keeps its value twice: with the backslash escapes of text undone, and as it was said.

/// A property: its name, upper case, its parameters and its value.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Prop {
    pub name: String,
    /// Each parameter by its upper-case name, a later one of a name in place of an earlier.
    pub params: Vec<(String, Param)>,
    /// The value with text's backslash escapes undone.
    pub value: String,
    /// The value as the line said it.
    pub raw: String,
}

/// A parameter's value: one, or a list where commas separated several.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Param {
    One(String),
    Many(Vec<String>),
}

impl Prop {
    /// A parameter's value on one line, "" where it has none: the first of a list, its line breaks (RFC 6868's `^n`
    /// can say one) each a space.
    pub fn param(&self, name: &str) -> String {
        let value = match self.params.iter().find(|(key, _)| key.eq_ignore_ascii_case(name)) {
            None => return String::new(),
            Some((_, Param::One(value))) => value.as_str(),
            Some((_, Param::Many(values))) => values.first().map_or("", String::as_str),
        };
        splitlines(value).join(" ")
    }
}

/// A component: its name, upper case, its properties in the order said and the components it holds.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Component {
    pub name: String,
    pub props: Vec<Prop>,
    pub children: Vec<Component>,
}

impl Component {
    /// Each property of this name, in the order said.
    pub fn each<'a>(&'a self, name: &'a str) -> impl Iterator<Item = &'a Prop> + 'a {
        self.props.iter().filter(move |p| p.name == name)
    }

    /// A property said more than once: the last.
    pub fn last(&self, name: &str) -> Option<&Prop> {
        self.props.iter().rev().find(|p| p.name == name)
    }

    /// This component and each it holds, depth first, that has this name: `Component.walk(name)`.
    pub fn walk<'a>(&'a self, name: &str, found: &mut Vec<&'a Component>) {
        if self.name == name {
            found.push(self);
        }
        for child in &self.children {
            child.walk(name, found);
        }
    }
}

/// Why a text yields no component at all.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct EndWithNoBegin;

/// The components a text holds at its top: usually one VCALENDAR.
pub fn read(text: &str) -> Result<Vec<Component>, EndWithNoBegin> {
    let text = text.trim_start_matches('\u{feff}').replace("\r\n", "\n").replace('\r', "\n");
    let mut stack: Vec<Component> = Vec::new();
    let mut done: Vec<Component> = Vec::new();
    for line in unfold(&text).split('\n') {
        if line.is_empty() {
            continue;
        }
        let Some((name, params, raw)) = parts(line) else { continue };
        match name.to_uppercase().as_str() {
            "BEGIN" => {
                stack.push(Component { name: unescape(raw).to_uppercase(), ..Component::default() });
            }
            "END" => {
                let ended = stack.pop().ok_or(EndWithNoBegin)?;
                if unescape(raw).to_uppercase() == "VTIMEZONE" {
                    continue;
                }
                match stack.last_mut() {
                    Some(parent) => parent.children.push(ended),
                    None => done.push(ended),
                }
            }
            upper => {
                if let Some(component) = stack.last_mut() {
                    component.props.push(Prop {
                        name: upper.to_owned(),
                        params,
                        value: unescape(raw),
                        raw: raw.to_owned(),
                    });
                }
            }
        }
    }
    if !stack.is_empty() {
        // A text cut short: what it did finish.
        done.push(stack.swap_remove(0));
    }
    Ok(done)
}

/// The text with each fold undone: a line break, any blank lines after it, and the space or tab that begins the
/// line it continues on.
fn unfold(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut chars = text.chars().peekable();
    while let Some(c) = chars.next() {
        if c != '\n' {
            out.push(c);
            continue;
        }
        let mut breaks = 1;
        while chars.peek() == Some(&'\n') {
            chars.next();
            breaks += 1;
        }
        if matches!(chars.peek(), Some(' ' | '\t')) {
            chars.next();
        } else {
            out.extend(std::iter::repeat_n('\n', breaks));
        }
    }
    out
}

/// A property line's name, its parameters and its value as said.
type Line<'a> = (String, Vec<(String, Param)>, &'a str);

/// A line's name, parameters and value as said, or None where it isn't a property line (`Contentline.raw_parts`).
fn parts(line: &str) -> Option<Line<'_>> {
    let (mut name_split, mut value_split) = (None, None);
    let (mut in_quotes, mut escaped) = (false, false);
    for (i, ch) in line.char_indices() {
        if ch == '"' && !escaped {
            in_quotes = !in_quotes;
        } else if ch == '\\' && !in_quotes {
            escaped = true;
            continue;
        } else if !in_quotes && !escaped {
            if matches!(ch, ':' | ';') && name_split.is_none() {
                name_split = Some(i);
            }
            if ch == ':' && value_split.is_none() {
                value_split = Some(i);
            }
        }
        escaped = false;
    }
    let value_split = value_split.filter(|&at| at > 0).unwrap_or(line.len());
    let name_split = name_split.filter(|&at| at > 0)?;
    let name: String = py_strip(&line[..name_split]).chars().filter(|c| !matches!(c, ' ' | '\t')).collect();
    if !is_token(&name) || name_split + 1 == value_split {
        return None;
    }
    let params = if name_split + 1 < value_split { &line[name_split + 1..value_split] } else { "" };
    let params = parameters(&escape_params(&strip_ows(params)))?;
    let value = line.get(value_split + 1..).unwrap_or("");
    Some((name, params, value))
}

/// `[\w.-]+`, all of it.
fn is_token(name: &str) -> bool {
    !name.is_empty() && name.chars().all(|c| c.is_alphanumeric() || matches!(c, '_' | '.' | '-'))
}

/// Text's backslash escapes undone: `\\`, `\,`, `\;`, `\:`, and `\n` or `\N` a line break. Any other stays.
pub fn unescape(value: &str) -> String {
    let mut out = String::with_capacity(value.len());
    let mut chars = value.chars().peekable();
    while let Some(c) = chars.next() {
        match (c, chars.peek()) {
            ('\\', Some(&next @ ('\\' | ',' | ';' | ':'))) => {
                out.push(next);
                chars.next();
            }
            ('\\', Some('n' | 'N')) => {
                out.push('\n');
                chars.next();
            }
            _ => out.push(c),
        }
    }
    out
}

/// Python's `str.strip()`.
pub(crate) fn py_strip(text: &str) -> &str {
    mm_kernel::text::strip(text)
}

/// Python's `str.splitlines()`.
pub(crate) fn splitlines(text: &str) -> Vec<&str> {
    let breaks = |c: char| {
        matches!(
            c,
            '\n' | '\r' | '\u{b}' | '\u{c}' | '\u{1c}' | '\u{1d}' | '\u{1e}' | '\u{85}' | '\u{2028}' | '\u{2029}'
        )
    };
    let mut lines = Vec::new();
    let mut start = 0;
    let mut chars = text.char_indices().peekable();
    while let Some((at, c)) = chars.next() {
        if !breaks(c) {
            continue;
        }
        lines.push(&text[start..at]);
        start = at + c.len_utf8();
        if c == '\r'
            && let Some(&(next, '\n')) = chars.peek()
        {
            chars.next();
            start = next + 1;
        }
    }
    if start < text.len() {
        lines.push(&text[start..]);
    }
    lines
}

/// The parameters with the whitespace around each `;` and `=` outside quotes taken out, and the ends stripped
/// (`_strip_ows_around_delimiters`).
fn strip_ows(text: &str) -> String {
    if !text.contains([' ', '\t']) {
        return text.to_owned();
    }
    let mut out = String::with_capacity(text.len());
    let mut pending = String::new();
    let (mut in_quotes, mut escaped, mut after_delimiter) = (false, false, false);
    let flush = |out: &mut String, pending: &mut String, after_delimiter: bool| {
        if !after_delimiter {
            out.push_str(pending);
        }
        pending.clear();
    };
    for ch in text.chars() {
        if escaped {
            flush(&mut out, &mut pending, after_delimiter);
            out.push(ch);
            escaped = false;
            after_delimiter = false;
        } else if ch == '\\' && !in_quotes {
            flush(&mut out, &mut pending, after_delimiter);
            out.push(ch);
            escaped = true;
            after_delimiter = false;
        } else if ch == '"' {
            in_quotes = !in_quotes;
            flush(&mut out, &mut pending, after_delimiter);
            out.push(ch);
            after_delimiter = false;
        } else if !in_quotes && matches!(ch, ' ' | '\t') {
            pending.push(ch);
        } else if !in_quotes && matches!(ch, ';' | '=') {
            pending.clear();
            while out.ends_with([' ', '\t']) {
                out.pop();
            }
            out.push(ch);
            after_delimiter = true;
        } else {
            flush(&mut out, &mut pending, after_delimiter);
            out.push(ch);
            after_delimiter = false;
        }
    }
    if !after_delimiter {
        out.push_str(&pending);
    }
    py_strip(&out).to_owned()
}

/// A parameter's backslash escapes as the percent signs `unescape_params` turns back (`_escape_string`).
fn escape_params(text: &str) -> String {
    text.replace('%', "%25").replace("\\,", "%2C").replace("\\:", "%3A").replace("\\;", "%3B").replace("\\\\", "%5C")
}

/// `_unescape_string`.
fn unescape_params(text: &str) -> String {
    text.replace("%2C", ",").replace("%3A", ":").replace("%3B", ";").replace("%5C", "\\").replace("%25", "%")
}

/// `q_split`: `text` split at `sep` outside double quotes, into at most `most + 1` pieces where `most` is given.
fn q_split(text: &str, sep: char, most: Option<usize>) -> Vec<&str> {
    let mut out = Vec::new();
    let (mut cursor, mut quoted, mut splits) = (0, false, 0);
    let mut chars = text.char_indices().peekable();
    while let Some((at, ch)) = chars.next() {
        if ch == '"' {
            quoted = !quoted;
        }
        if !quoted && ch == sep {
            out.push(&text[cursor..at]);
            cursor = at + ch.len_utf8();
            splits += 1;
        }
        if chars.peek().is_none() || Some(splits) == most {
            out.push(&text[cursor..]);
            break;
        }
    }
    out
}

/// RFC 6868's caret escapes undone: `^^`, `^n` a line break and `^'` a double quote.
fn caret_unescape(text: &str) -> String {
    text.replace("^^", "\u{0}").replace("^n", "\n").replace("^'", "\"").replace('\u{0}', "^")
}

/// `Parameters.from_ical`, then each name and value unescaped. None where one won't read, which drops the line.
fn parameters(text: &str) -> Option<Vec<(String, Param)>> {
    let unsafe_char = |c: char, quoted: bool| {
        matches!(c, '\u{0}'..='\u{8}' | '\u{a}'..='\u{1f}' | '\u{7f}' | '"')
            || (!quoted && matches!(c, ',' | ':' | ';'))
    };
    let mut out: Vec<(String, Param)> = Vec::new();
    for param in q_split(text, ';', None) {
        let [key, value] = q_split(param, '=', Some(1))[..] else { return None };
        if !is_token(key) {
            return None;
        }
        let mut values = Vec::new();
        for v in q_split(value, ',', None) {
            let quoted = !v.is_empty() && v.starts_with('"') && v.ends_with('"');
            let v = if quoted { v.trim_matches('"') } else { v };
            if v.chars().any(|c| unsafe_char(c, quoted)) {
                return None;
            }
            values.push(unescape_params(&caret_unescape(v)));
        }
        let value = match values.len() {
            0 => Param::One(unescape_params(value)),
            1 => Param::One(values.swap_remove(0)),
            _ => Param::Many(values),
        };
        let key = unescape_params(&key.to_uppercase());
        out.retain(|(held, _)| *held != key);
        out.push((key, value));
    }
    Some(out)
}

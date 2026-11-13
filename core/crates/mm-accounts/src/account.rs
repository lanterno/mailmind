//! An account as `mailmind.config.json` holds it: an entry's defaults, what the API may show of it, and what the
//! rest of the app may count on (`mailmind.accounts.domain.account`).
//!
//! An entry is JSON as it parsed, so what a key holds is the file's word for it: a value is echoed as it came, and
//! read as Python reads it (`truthy`), so both apps answer one file alike.

use mm_kernel::errors::Problem;
use mm_kernel::{parsed, text};
use serde_json::{Map, Value, json};

use crate::credential::CredentialRef;

/// What an account's type gives the keys its entry leaves out.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct TypeDefaults {
    pub host: Option<&'static str>,
    pub port: u16,
    pub starttls: bool,
    pub trash: &'static str,
    pub strip_spaces: bool,
    pub smtp_host: Option<&'static str>,
    pub smtp_port: u16,
    pub smtp_starttls: bool,
}

const GMAIL: TypeDefaults = TypeDefaults {
    host: Some("imap.gmail.com"),
    port: 993,
    starttls: false,
    trash: "[Gmail]/Trash",
    strip_spaces: true,
    smtp_host: Some("smtp.gmail.com"),
    smtp_port: 587,
    smtp_starttls: true,
};
const IMAP: TypeDefaults = TypeDefaults {
    host: None,
    port: 993,
    starttls: false,
    trash: "Trash",
    strip_spaces: false,
    smtp_host: None,
    smtp_port: 587,
    smtp_starttls: true,
};
const PROTON: TypeDefaults = TypeDefaults {
    host: Some("127.0.0.1"),
    port: 1143,
    starttls: true,
    trash: "Trash",
    strip_spaces: false,
    smtp_host: Some("127.0.0.1"),
    smtp_port: 1025,
    smtp_starttls: true,
};

/// The defaults of the entry's type; a type that is none of them is generic IMAP's.
pub fn defaults(entry: &Map<String, Value>) -> TypeDefaults {
    match entry.get("type") {
        None => IMAP,
        Some(Value::String(t)) if t == "gmail" => GMAIL,
        Some(Value::String(t)) if t == "proton" => PROTON,
        Some(_) => IMAP,
    }
}

/// What Python's `bool()` makes of a JSON value.
pub fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// `a or b`: `a` when it is truthy, else `b`.
fn or(a: Option<&Value>, b: Value) -> Value {
    match a {
        Some(v) if truthy(v) => v.clone(),
        _ => b,
    }
}

fn given_or(entry: &Map<String, Value>, key: &str, default: Value) -> Value {
    entry.get(key).cloned().unwrap_or(default)
}

/// Where a server listens and how TLS starts there, each as the entry says it or else its type's default.
#[derive(Clone, Debug, PartialEq)]
pub struct Endpoint {
    pub host: Value,
    pub port: Value,
    pub starttls: Value,
}

pub fn imap_endpoint(entry: &Map<String, Value>) -> Endpoint {
    let d = defaults(entry);
    Endpoint {
        host: given_or(entry, "host", json!(d.host)),
        port: given_or(entry, "port", json!(d.port)),
        starttls: given_or(entry, "starttls", json!(d.starttls)),
    }
}

/// An explicit override, else the type's default; generic IMAP has none, so it is the IMAP host.
pub fn smtp_endpoint(entry: &Map<String, Value>) -> Endpoint {
    let d = defaults(entry);
    Endpoint {
        host: or(entry.get("smtp_host"), or(d.smtp_host.map(|h| json!(h)).as_ref(), imap_endpoint(entry).host)),
        port: given_or(entry, "smtp_port", json!(d.smtp_port)),
        starttls: given_or(entry, "smtp_starttls", json!(d.smtp_starttls)),
    }
}

/// An entry's text that a person may have padded, as Python's `(x or "").strip() or None` reads it. A value that is
/// set and isn't text is malformed, as Python's `.strip()` of it would raise.
fn stripped(value: Option<&Value>) -> Result<Option<String>, Malformed> {
    match value {
        Some(v) if truthy(v) => match v {
            Value::String(s) => Ok(Some(text::strip(s).to_owned()).filter(|s| !s.is_empty())),
            _ => Err(Malformed),
        },
        _ => Ok(None),
    }
}

/// An entry the file holds in a shape no reading of it takes.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Malformed;

const APPLIED: [&str; 7] = ["host", "port", "starttls", "trash", "smtp_host", "smtp_port", "smtp_starttls"];

/// One entry as the API may show it (`masked_row`): its effective endpoints, which of them its type's defaults
/// gave, and its credential masked. Never the entry itself.
pub fn masked_row(entry: &Value) -> Result<Value, Malformed> {
    let Value::Object(entry) = entry else {
        return Err(Malformed);
    };
    let (imap, smtp) = (imap_endpoint(entry), smtp_endpoint(entry));
    let mut applied = Map::new();
    for key in APPLIED {
        applied.insert(key.to_owned(), Value::Bool(!entry.contains_key(key)));
    }
    let mut row = Map::new();
    row.insert("email".into(), entry.get("email").cloned().unwrap_or(Value::Null));
    row.insert("type".into(), given_or(entry, "type", json!("imap")));
    row.insert("alias".into(), json!(stripped(entry.get("alias"))?));
    row.insert("host".into(), imap.host);
    row.insert("port".into(), imap.port);
    row.insert("starttls".into(), imap.starttls);
    row.insert("trash".into(), given_or(entry, "trash", json!(defaults(entry).trash)));
    row.insert("smtp_host".into(), smtp.host);
    row.insert("smtp_port".into(), smtp.port);
    row.insert("smtp_starttls".into(), smtp.starttls);
    row.insert("disabled".into(), Value::Bool(entry.get("disabled").is_some_and(truthy)));
    row.insert("defaults_applied".into(), Value::Object(applied));
    row.insert("credential".into(), CredentialRef::parse(entry.get("credential")).masked());
    row.insert("signature".into(), or(entry.get("signature"), json!("")));
    Ok(Value::Object(row))
}

/// An account the rest of the app may use: one with an address and an IMAP host, not disabled (`normalized`).
#[derive(Clone, Debug, PartialEq)]
pub struct Account {
    pub email: String,
    pub kind: String,
    pub host: Value,
    pub port: Value,
    pub starttls: bool,
    pub credential: Value,
    pub strip_spaces: bool,
    /// The name the page shows for it, when the entry gives one that isn't blank.
    pub alias: Option<String>,
    /// Its Trash folder: the entry's, or its type's (`accounts.trash_folder`).
    pub trash: String,
}

impl Account {
    /// The entry as an account, or None for one that can't be used: disabled, with no address, or a generic IMAP
    /// account with no host.
    pub fn from_entry(entry: &Value) -> Option<Account> {
        let Value::Object(entry) = entry else {
            return None;
        };
        if entry.get("disabled").is_some_and(truthy) {
            return None;
        }
        let email = match entry.get("email") {
            Some(Value::String(e)) if !e.is_empty() => e.clone(),
            _ => return None,
        };
        let imap = imap_endpoint(entry);
        if !truthy(&imap.host) {
            return None;
        }
        let kind = match entry.get("type") {
            Some(Value::String(t)) => t.clone(),
            _ => "imap".to_owned(),
        };
        Some(Account {
            email,
            kind,
            host: imap.host,
            port: imap.port,
            starttls: truthy(&imap.starttls),
            credential: entry.get("credential").cloned().unwrap_or_else(|| json!({})),
            strip_spaces: entry.get("strip_spaces").map_or(defaults(entry).strip_spaces, truthy),
            alias: entry
                .get("alias")
                .and_then(Value::as_str)
                .map(|a| text::strip(a).to_owned())
                .filter(|a| !a.is_empty()),
            trash: match entry.get("trash") {
                Some(Value::String(t)) if !t.is_empty() => t.clone(),
                _ => defaults(entry).trash.to_owned(),
            },
        })
    }

    /// The IMAP server's host and port, when the entry names them in a form a connection can take.
    pub fn imap_server(&self) -> Option<(String, u16)> {
        let host = self.host.as_str()?.to_owned();
        let port = match &self.port {
            Value::Number(n) => n.as_u64().and_then(|p| u16::try_from(p).ok()),
            Value::String(s) => text::strip(s).parse::<u16>().ok(),
            _ => None,
        }?;
        Some((host, port))
    }
}

/// A signature as the composer inserts it: multiline plain text with no control, bidirectional or zero-width
/// character but the line breaks and tabs, at most 2,000 characters (`clean_signature`).
pub fn clean_signature(value: &Value) -> String {
    let written = match value {
        Value::Null => return String::new(),
        Value::String(s) => s.clone(),
        Value::Bool(true) => "True".to_owned(),
        Value::Bool(false) => "False".to_owned(),
        other => other.to_string(),
    };
    let kept: String = written.chars().filter(|c| matches!(c, '\n' | '\r' | '\t') || text::is_printable(*c)).collect();
    text::strip(&kept).chars().take(2000).collect()
}

/// An alias as the page shows it: printable characters and spaces, stripped, at most 64 characters (`clean_alias`).
pub fn clean_alias(alias: &str) -> String {
    let kept: String = alias.chars().filter(|c| *c == ' ' || text::is_printable(*c)).collect();
    text::strip(&kept).chars().take(64).collect()
}

/// What a port that isn't one says, by the key it was given under.
fn port_said(key: &str) -> &'static str {
    if key == "smtp_port" { "SMTP port must be 1–65535" } else { "port must be 1–65535" }
}

/// A port as a request or the file gives it, as `int()` reads it; else the refusal that names it (`_port`).
fn port(key: &str, given: &Value) -> Result<i64, Problem> {
    parsed::as_int(given).ok_or_else(|| Problem::refused(port_said(key)))
}

/// The refusal a port outside 1–65535 gets.
fn port_in_range(key: &str, given: &Value) -> Result<(), Problem> {
    if (1..=65535).contains(&port(key, given)?) { Ok(()) } else { Err(Problem::refused(port_said(key))) }
}

/// What a request's `p[k] not in (None, "")` lets through.
fn said(value: &Value) -> bool {
    !matches!(value, Value::Null) && value.as_str() != Some("")
}

/// The fields a request may change, other than its credential, applied to `entry` (`apply_fields`): a blank alias
/// or signature unsets it, a port is read as a number, a flag is taken only as true or false, and any other value
/// but null and "" is kept as it came.
pub fn apply_fields(entry: &mut Map<String, Value>, asked: &Map<String, Value>) -> Result<(), Problem> {
    if let Some(alias) = asked.get("alias") {
        let alias = alias.as_str().map(clean_alias).unwrap_or_default();
        if alias.is_empty() {
            entry.shift_remove("alias");
        } else {
            entry.insert("alias".into(), Value::String(alias));
        }
    }
    if let Some(signature) = asked.get("signature") {
        let signature = clean_signature(signature);
        if signature.is_empty() {
            entry.shift_remove("signature");
        } else {
            entry.insert("signature".into(), Value::String(signature));
        }
    }
    for key in ["type", "host", "trash", "smtp_host"] {
        if let Some(value) = asked.get(key).filter(|v| said(v)) {
            entry.insert(key.into(), value.clone());
        }
    }
    for key in ["port", "smtp_port"] {
        if let Some(value) = asked.get(key).filter(|v| said(v)) {
            entry.insert(key.into(), Value::from(port(key, value)?));
        }
    }
    for key in ["starttls", "strip_spaces", "disabled", "smtp_starttls"] {
        if let Some(flag @ Value::Bool(_)) = asked.get(key) {
            entry.insert(key.into(), flag.clone());
        }
    }
    Ok(())
}

/// What an account must be to be saved (`validate_new`): an address, a type MailMind knows, a host for generic
/// IMAP, and a port a connection can reach.
pub fn validate_new(entry: &Map<String, Value>) -> Result<(), Problem> {
    let email = entry.get("email").and_then(Value::as_str).map(text::strip).unwrap_or_default();
    if !email.contains('@') {
        return Err(Problem::refused("a valid email is required"));
    }
    let kind = match entry.get("type") {
        None => Some("imap"),
        Some(Value::String(t)) => ["gmail", "imap", "proton"].contains(&t.as_str()).then_some(t.as_str()),
        Some(_) => None,
    };
    let Some(kind) = kind else {
        return Err(Problem::refused("type must be gmail, imap, or proton"));
    };
    let host = match entry.get("host") {
        Some(v) if truthy(v) => match v {
            Value::String(s) => s.clone(),
            other => other.to_string(),
        },
        _ => String::new(),
    };
    if kind == "imap" && text::strip(&host).is_empty() {
        return Err(Problem::refused("a host is required for a generic IMAP account"));
    }
    match entry.get("port") {
        Some(given) => port_in_range("port", given),
        None => Ok(()),
    }
}

/// Text that names a host: not empty, and every character printable and no space (`_names_a_host`).
fn names_a_host(held: &Value) -> bool {
    held.as_str().is_some_and(|h| !h.is_empty() && h.chars().all(|c| text::is_printable(c) && !text::is_space(c)))
}

/// Python's `==` of two values a file or a request gave: a number equals the same number written otherwise, and
/// true is 1.
fn same(a: &Value, b: &Value) -> bool {
    let number = |v: &Value| match v {
        Value::Bool(b) => Some(f64::from(u8::from(*b))),
        Value::Number(n) => n.as_f64(),
        _ => None,
    };
    match (number(a), number(b)) {
        (Some(x), Some(y)) => x == y,
        _ => a == b,
    }
}

/// A write may not leave an endpoint no connection could reach (`refuse_bad_endpoints`). Only what it changes from
/// `before` counts: what the file held is the owner's, and asking for a new alias is no time to refuse it.
pub fn refuse_bad_endpoints(entry: &Map<String, Value>, before: &Map<String, Value>) -> Result<(), Problem> {
    let changed = |key: &str| entry.get(key).filter(|now| !same(now, before.get(key).unwrap_or(&Value::Null)));
    if changed("host").is_some_and(|h| !names_a_host(h)) {
        return Err(Problem::refused("host must be a host name"));
    }
    if changed("smtp_host").is_some_and(|h| !names_a_host(h)) {
        return Err(Problem::refused("SMTP host must be a host name"));
    }
    match changed("smtp_port") {
        Some(given) => port_in_range("smtp_port", given),
        None => Ok(()),
    }
}

/// Where in the file's `accounts` the entry under `email` is, matched as Python's `account.find` matches it:
/// stripped, in any case, and an entry with no address answering to "".
pub fn position(raw: &Map<String, Value>, email: &str) -> Option<usize> {
    let wanted = text::strip(email).to_lowercase();
    let Some(Value::Array(entries)) = raw.get("accounts") else {
        return None;
    };
    entries.iter().position(|e| {
        let named = match e.get("email") {
            Some(Value::String(named)) => Some(text::strip(named).to_lowercase()),
            Some(named) if truthy(named) => None,
            _ => Some(String::new()),
        };
        e.is_object() && named.as_deref() == Some(wanted.as_str())
    })
}

/// The entry under `email`, as `position` finds it.
pub fn find<'a>(raw: &'a mut Map<String, Value>, email: &str) -> Option<&'a mut Map<String, Value>> {
    let at = position(raw, email)?;
    match raw.get_mut("accounts") {
        Some(Value::Array(entries)) => entries.get_mut(at).and_then(Value::as_object_mut),
        _ => None,
    }
}

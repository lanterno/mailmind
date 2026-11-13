//! An account as `mailmind.config.json` holds it: an entry's defaults, what the API may show of it, and what the
//! rest of the app may count on (`mailmind.accounts.domain.account`).
//!
//! An entry is JSON as it parsed, so what a key holds is the file's word for it: a value is echoed as it came, and
//! read as Python reads it (`truthy`), so both apps answer one file alike.

use mm_kernel::text;
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

/// The entry under `email`, matched as Python's `account.find` matches it: stripped, in any case.
pub fn find<'a>(raw: &'a mut Map<String, Value>, email: &str) -> Option<&'a mut Map<String, Value>> {
    let wanted = text::strip(email).to_lowercase();
    let Some(Value::Array(entries)) = raw.get_mut("accounts") else {
        return None;
    };
    entries.iter_mut().find_map(|e| match e {
        Value::Object(entry) => {
            let named = entry.get("email").and_then(Value::as_str).map(|e| text::strip(e).to_lowercase());
            (named.as_deref() == Some(wanted.as_str())).then_some(entry)
        }
        _ => None,
    })
}

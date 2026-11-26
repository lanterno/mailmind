//! An account not saved yet, as a test of its login tries it (`mailmind.accounts.domain.draft`).
//!
//! A draft may only try a password typed with it, against the hosts typed with it. One naming a stored secret (an op
//! reference, an environment variable, the Bridge password) would send that secret to whatever host the request
//! names, so it is refused until the account is saved. A saved account's credential goes only to its saved servers,
//! and that is the use case's to see to: it never reads an endpoint out of the request.

use mm_kernel::parsed;
use mm_kernel::seams::Secret;
use serde_json::{Map, Value};

use crate::account::{self, truthy};

/// What a draft's port that no connection could reach says, as an edit of an account says it.
pub const BAD_PORT: &str = "port must be 1–65535";
/// The same of its outgoing (SMTP) server's port.
pub const BAD_SMTP_PORT: &str = "SMTP port must be 1–65535";
const NO_HOST: &str = "a host is required";
const SAVE_FIRST: &str = "save the account to test a stored secret; before then only a typed password is tried";

/// What a draft asks to try: nothing keeps it, and its password is typed, which no file and no Keychain sees.
#[derive(Debug)]
pub struct Draft {
    pub email: Option<String>,
    pub gentlemail: bool,
    /// The host typed, or its type's; `None` for one that isn't text, which no name lookup finds.
    pub host: Option<String>,
    pub port: u16,
    pub starttls: bool,
    /// Its outgoing (SMTP) server, as typed or as its type gives it, else the IMAP host as a saved account's is
    /// (`account::smtp_endpoint`); `None` for a host that isn't text.
    pub smtp_host: Option<String>,
    pub smtp_port: u16,
    pub smtp_starttls: bool,
    pub password: Secret,
}

/// Why a draft isn't tried.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum NotTried {
    /// Its port is no port: a failed test, in the sentence `BAD_PORT` (`BadPort`).
    BadPort,
    /// Its outgoing server's port is no port: a failed test, in the sentence `BAD_SMTP_PORT`.
    BadSmtpPort,
    /// It can't be tried as it stands: one of the fixed sentences here, which hold nothing typed (`DraftRefused`).
    Refused(&'static str),
}

/// A port as a draft gives it, or `default` when it gives none: None for one no connection could reach.
fn port_of(draft: &Map<String, Value>, key: &str, default: u16) -> Option<u16> {
    let port = match draft.get(key).filter(|p| truthy(p)) {
        Some(given) => parsed::as_int(given),
        None => Some(i64::from(default)),
    };
    port.and_then(|p| u16::try_from(p).ok()).filter(|p| *p >= 1)
}

/// A host as a draft gives it: the text typed, None for one that isn't text, or `default` when it gives none.
fn host_of(draft: &Map<String, Value>, key: &str, default: Option<String>) -> Option<Option<String>> {
    match draft.get(key).filter(|h| truthy(h)) {
        Some(Value::String(host)) => Some(Some(host.clone())),
        Some(_) => Some(None),
        None => default.map(Some),
    }
}

/// The draft as an account a connection can open (`throwaway_account`). Its port is judged first, then its host,
/// then its outgoing server's port, then its credential. Its password is tried as it would be kept
/// (`account::as_kept`).
pub fn throwaway_account(draft: &Map<String, Value>) -> Result<Draft, NotTried> {
    let defaults = account::defaults(draft);
    let port = port_of(draft, "port", defaults.port).ok_or(NotTried::BadPort)?;
    let host = host_of(draft, "host", defaults.host.map(str::to_owned)).ok_or(NotTried::Refused(NO_HOST))?;
    let starttls = draft.get("starttls").map_or(defaults.starttls, truthy);
    let smtp_port = port_of(draft, "smtp_port", defaults.smtp_port).ok_or(NotTried::BadSmtpPort)?;
    let smtp_host = host_of(draft, "smtp_host", defaults.smtp_host.map(str::to_owned)).unwrap_or_else(|| host.clone());
    let smtp_starttls = draft.get("smtp_starttls").map_or(defaults.smtp_starttls, truthy);
    let credential = draft.get("credential").filter(|c| truthy(c));
    let keychain = credential.and_then(|c| c.get("style")).and_then(Value::as_str) == Some("keychain");
    let password = match credential.and_then(|c| c.get("value")) {
        Some(Value::String(typed)) if keychain && !typed.is_empty() => Secret::new(typed.clone()),
        _ => return Err(NotTried::Refused(SAVE_FIRST)),
    };
    let password = account::as_kept(password, draft.get("strip_spaces").map_or(defaults.strip_spaces, truthy));
    let email = draft.get("email").and_then(Value::as_str).map(str::to_owned);
    let gentlemail = draft.get("type").and_then(Value::as_str) == Some("gentlemail");
    Ok(Draft { email, gentlemail, host, port, starttls, smtp_host, smtp_port, smtp_starttls, password })
}

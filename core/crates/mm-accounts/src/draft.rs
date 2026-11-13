//! An account not saved yet, as a test of its login tries it (`mailmind.accounts.domain.draft`).
//!
//! A draft may only try a password typed with it, against the host typed with it. One naming a stored secret (an op
//! reference, an environment variable, the Bridge password) would send that secret to whatever host the request
//! names, so it is refused until the account is saved. A saved account's credential goes only to its saved server,
//! and that is the use case's to see to: it never reads an endpoint out of the request.

use mm_kernel::parsed;
use mm_kernel::seams::Secret;
use serde_json::{Map, Value};

use crate::account::{self, truthy};

/// What a draft's port that no connection could reach says, as an edit of an account says it.
pub const BAD_PORT: &str = "port must be 1–65535";
const NO_HOST: &str = "a host is required";
const SAVE_FIRST: &str = "save the account to test a stored secret; before then only a typed password is tried";

/// What a draft asks to try: nothing keeps it, and its password is typed, which no file and no Keychain sees.
#[derive(Debug)]
pub struct Draft {
    pub email: Option<String>,
    /// The host typed, or its type's; `None` for one that isn't text, which no name lookup finds.
    pub host: Option<String>,
    pub port: u16,
    pub starttls: bool,
    pub password: Secret,
}

/// Why a draft isn't tried.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum NotTried {
    /// Its port is no port: a failed test, in the sentence `BAD_PORT` (`BadPort`).
    BadPort,
    /// It can't be tried as it stands: one of the fixed sentences here, which hold nothing typed (`DraftRefused`).
    Refused(&'static str),
}

/// The draft as an account a connection can open (`throwaway_account`). Its port is judged first, then its host,
/// then its credential.
pub fn throwaway_account(draft: &Map<String, Value>) -> Result<Draft, NotTried> {
    let defaults = account::defaults(draft);
    let port = match draft.get("port").filter(|p| truthy(p)) {
        Some(given) => parsed::as_int(given),
        None => Some(i64::from(defaults.port)),
    };
    let port = port.and_then(|p| u16::try_from(p).ok()).filter(|p| *p >= 1).ok_or(NotTried::BadPort)?;
    let host = match draft.get("host").filter(|h| truthy(h)) {
        Some(Value::String(host)) => Some(host.clone()),
        Some(_) => None,
        None => Some(defaults.host.ok_or(NotTried::Refused(NO_HOST))?.to_owned()),
    };
    let starttls = draft.get("starttls").map_or(defaults.starttls, truthy);
    let credential = draft.get("credential").filter(|c| truthy(c));
    let keychain = credential.and_then(|c| c.get("style")).and_then(Value::as_str) == Some("keychain");
    let password = match credential.and_then(|c| c.get("value")) {
        Some(Value::String(typed)) if keychain && !typed.is_empty() => Secret::new(typed.clone()),
        _ => return Err(NotTried::Refused(SAVE_FIRST)),
    };
    let email = draft.get("email").and_then(Value::as_str).map(str::to_owned);
    Ok(Draft { email, host, port, starttls, password })
}

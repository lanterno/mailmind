//! What the Settings page does to an account: try its login, trust its certificate again, change the file
//! (`mailmind.accounts.application.connections`).

use std::sync::Arc;
use std::time::Duration;

use mm_kernel::errors::{Failure, Problem};
use mm_kernel::seams::{Clock, Secret};
use serde_json::{Map, Value, json};

use crate::Directory;
use crate::account::truthy;
use crate::draft::{self, NotTried};
use crate::ports::{AccountChange, AccountEvents, LoginProbe, LoginTrouble};

/// How long each step of a test login may take: a short wait gives quick word of a bad host.
pub const PROBE_TIMEOUT: Duration = Duration::from_secs(10);

/// What a wrong App Password at a Gmail account is told (`conn_error.GMAIL_REFUSED`).
pub const GMAIL_REFUSED: &str = "Gmail didn't accept that App Password: paste it again, or create a new one.";

/// A connection's trouble in words that are safe to show (`conn_error.quiet`): what went wrong, never what the server
/// or the error said. Each class is named as the Python app names the same trouble, and each of Gmail's refusals
/// (`mm_imap`'s) says what to do.
pub fn quiet(trouble: LoginTrouble) -> &'static str {
    match trouble.0 {
        "LoginNeedsAppPassword" => {
            "Gmail needs an App Password here, not your Google password. If Google offers none, Advanced Protection or \
             a Workspace admin has switched them off."
        }
        "LoginNeedsWebSignIn" => {
            "Google wants you to sign in on the web first: sign in to Gmail in your browser, then test again."
        }
        "LoginImapOff" => {
            "IMAP is switched off for this account: turn it on in Gmail's settings, or ask your Workspace admin to."
        }
        "TLSPinMismatch" => "certificate changed",
        // A 1Password read that failed: Python's words say so of one whose reference names its `password` field.
        "LoginRefused" | "LoginUnavailable" | "Unsendable" | "NoCredential" | "OpReadFailed" => "AUTHENTICATIONFAILED",
        "ConnectionRefused" => "connection refused",
        "Timeout" => "timeout",
        "NameNotResolved" => "host not found",
        "CertificateNotVerified" => "SSLCertVerificationError",
        "TlsError" => "SSLError",
        "Abort" => "abort",
        other => other,
    }
}

/// `quiet`'s words for a test of an account of the type `kind`: a login refused at a Gmail account says so of its App
/// Password, where any other's says what it always said.
pub fn quiet_for(trouble: LoginTrouble, kind: Option<&str>) -> &'static str {
    if kind == Some("gmail") && trouble.0 == crate::login_hold::REFUSED { GMAIL_REFUSED } else { quiet(trouble) }
}

/// What a request asked of an account, as its schema handed it on.
pub type Asked = Map<String, Value>;

/// What a test of an account's login comes to.
#[derive(Debug)]
pub struct Connections {
    directory: Arc<Directory>,
    probe: Arc<dyn LoginProbe>,
    events: Arc<dyn AccountEvents>,
    clock: Arc<dyn Clock>,
}

/// What trusting an account's certificate again asks of the sync.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Trusted {
    /// The account is enabled: its sync is to run again, which pins the certificate it meets now.
    SyncAgain(String),
    /// It is disabled, and waits until it is enabled.
    Waits(String),
}

impl Connections {
    pub fn new(
        directory: Arc<Directory>,
        probe: Arc<dyn LoginProbe>,
        events: Arc<dyn AccountEvents>,
        clock: Arc<dyn Clock>,
    ) -> Connections {
        Connections { directory, probe, events, clock }
    }

    /// Try a saved account's login at its saved server (`{email}`), or a draft's where it was typed (`{draft}`), and
    /// write nothing. A login that didn't work is an answer (`connect: false`), in `quiet_for`'s words for its type;
    /// an account the file doesn't hold is NotFound, and a draft that may not be tried Refused.
    pub fn test(&self, body: &Asked) -> Result<Value, Failure> {
        let started = self.clock.now();
        let draft = body.get("draft").filter(|d| truthy(d));
        // The account's type, which says how a refusal is put: a draft's as it names one, generic IMAP's when it names
        // none.
        let kind: Option<String>;
        let tried = match draft {
            Some(Value::Object(draft)) => {
                kind = draft.get("type").map_or(Some("imap"), Value::as_str).map(str::to_owned);
                match draft::throwaway_account(draft) {
                    Ok(draft) => {
                        self.probe.draft(&draft, PROBE_TIMEOUT).map(|()| draft.email.map_or(Value::Null, Value::from))
                    }
                    Err(NotTried::BadPort) => Err(LoginTrouble(draft::BAD_PORT)),
                    Err(NotTried::Refused(said)) => return Err(Problem::refused(said).into()),
                }
            }
            Some(_) => return Err(Problem::bad_request().into()),
            None => {
                let named = body.get("email").and_then(Value::as_str);
                let saved = match named {
                    Some(email) => self.directory.get(email)?,
                    None => None,
                };
                let saved = saved.ok_or_else(|| Problem::not_found("account not found"))?;
                kind = Some(saved.kind.clone());
                self.probe.saved(&saved, PROBE_TIMEOUT).map(|()| Value::from(saved.email))
            }
        };
        Ok(match tried {
            Ok(email) => {
                let ms = (self.clock.now().micros() - started.micros()) / 1000;
                json!({"ok": true, "connect": true, "email": email, "detail": "login ok", "ms": ms})
            }
            Err(trouble) => {
                let email = body.get("email").filter(|e| truthy(e)).or_else(|| draft.and_then(|d| d.get("email")));
                json!({"ok": true, "connect": false, "email": email, "error": quiet_for(trouble, kind.as_deref())})
            }
        })
    }

    /// Trust the account's servers again after its loopback certificate changed (Bridge reinstalled or reset):
    /// forget its pins (`trust_cert`). NotFound for an account the file doesn't hold.
    pub fn trust_cert(&self, email: Option<&str>) -> Result<Trusted, Failure> {
        let email = mm_kernel::text::strip(email.unwrap_or_default());
        let saved_as = self.directory.forget_tls_pins(email)?.ok_or_else(|| Problem::not_found("not found"))?;
        self.events.publish(AccountChange::Changed(saved_as));
        let enabled = self.directory.accounts()?.iter().any(|a| a.email == email);
        Ok(if enabled { Trusted::SyncAgain(email.to_owned()) } else { Trusted::Waits(email.to_owned()) })
    }

    /// Add an account (`change("add")`).
    pub fn add(&self, body: &Asked) -> Result<Value, Failure> {
        let row = self.directory.add_account(body)?;
        if let Some(email) = row.get("email").and_then(Value::as_str) {
            self.events.publish(AccountChange::Added(email.to_owned()));
        }
        Ok(json!({"ok": true, "account": row}))
    }

    /// Change what the body names of the account saved under its `email` (`change("edit")`).
    pub fn edit(&self, body: &Asked) -> Result<Value, Failure> {
        let email = body.get("email").and_then(Value::as_str).unwrap_or_default();
        let row = self.directory.update_account(email, body)?.ok_or_else(|| Problem::not_found("not found"))?;
        if let Some(email) = row.get("email").and_then(Value::as_str) {
            self.events.publish(AccountChange::Changed(email.to_owned()));
        }
        Ok(json!({"ok": true, "account": row}))
    }

    /// Remove the account saved under the body's `email` (`change("delete")`).
    pub fn delete(&self, body: &Asked) -> Result<Value, Failure> {
        let asked = body.get("email").cloned().unwrap_or(Value::Null);
        let saved_as = self
            .directory
            .remove_account(asked.as_str().unwrap_or_default())?
            .ok_or_else(|| Problem::not_found("not found"))?;
        self.events.publish(AccountChange::Removed(saved_as));
        Ok(json!({"ok": true, "removed": asked}))
    }

    /// Keep `typed` as the Proton Bridge's password, in the login Keychain (`set_bridge_password`). Each account that
    /// logs in with it has changed: a login its server refused is let go, and IDLE logs in afresh.
    pub fn set_bridge_password(&self, typed: Secret) -> Result<(), Failure> {
        self.directory.set_bridge_password(typed)?;
        self.bridged_changed()
    }

    /// Take the Bridge's password out of the login Keychain (`clear_bridge_password`), and say so of each account
    /// that logs in with it.
    pub fn clear_bridge_password(&self) -> Result<(), Failure> {
        self.directory.clear_bridge_password()?;
        self.bridged_changed()
    }

    fn bridged_changed(&self) -> Result<(), Failure> {
        for email in self.directory.bridged()? {
            self.events.publish(AccountChange::Changed(email));
        }
        Ok(())
    }
}

//! The accounts as `mailmind.config.json` holds them, and what is kept for each (`mailmind.accounts.application
//! .directory`): the use cases that read the file, and the one write a connection makes, a certificate's pin.

use std::sync::{Arc, Mutex};

use mm_kernel::errors::{Failure, Fault};
use mm_kernel::seams::{Clock, Keychain, Secret};
use mm_kernel::when;
use serde_json::{Map, Value, json};

use crate::account::{self, Account, truthy};
use crate::credential::{CredentialRef, Style};
use crate::ports::ConfigFile;

const MALFORMED: Fault = Fault::new("MalformedConfig");
const NO_CREDENTIAL: Fault = Fault::new("NoCredential");
const KEYCHAIN: Fault = Fault::new("KeychainProblem");
/// A credential kept where the Rust server can't read it yet: 1Password, a variable or the Bridge password.
const NOT_YET: Fault = Fault::new("CredentialStyleNotOnTheRustServerYet");

/// What a pin check found at an endpoint.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Pin {
    /// The certificate is the one pinned there, or it was pinned now, on first use.
    Kept,
    /// Another certificate is pinned there.
    Other,
    /// No account is saved under that address, as for a draft being tried: nothing is pinned for it.
    NoAccount,
}

#[derive(Debug)]
pub struct Directory {
    file: Arc<dyn ConfigFile>,
    keychain: Arc<dyn Keychain>,
    clock: Arc<dyn Clock>,
    write: Mutex<()>,
}

impl Directory {
    pub fn new(file: Arc<dyn ConfigFile>, keychain: Arc<dyn Keychain>, clock: Arc<dyn Clock>) -> Directory {
        Directory { file, keychain, clock, write: Mutex::new(()) }
    }

    fn entries(raw: &Map<String, Value>) -> Result<&[Value], Fault> {
        match raw.get("accounts") {
            None => Ok(&[]),
            Some(Value::Array(entries)) => Ok(entries),
            Some(_) => Err(MALFORMED),
        }
    }

    /// The accounts the rest of the app may use, disabled ones left out.
    pub fn accounts(&self) -> Result<Vec<Account>, Fault> {
        let raw = self.file.read()?;
        Ok(Directory::entries(&raw)?.iter().filter_map(Account::from_entry).collect())
    }

    /// The account saved under exactly `email`, or None.
    pub fn get(&self, email: &str) -> Result<Option<Account>, Fault> {
        Ok(self.accounts()?.into_iter().find(|a| a.email == email))
    }

    /// `GET /api/accounts`: every entry as the API may show it, the default signature, and when this was said.
    pub fn listed(&self) -> Result<Value, Failure> {
        let raw = self.file.read()?;
        let rows = Directory::entries(&raw)?
            .iter()
            .map(account::masked_row)
            .collect::<Result<Vec<_>, _>>()
            .map_err(|_| MALFORMED)?;
        Ok(json!({
            "ok": true,
            "accounts": rows,
            "signature_default": account::clean_signature(&raw.get("signature_default").cloned()
                .filter(truthy).unwrap_or_else(|| json!(""))),
            "generated": when::iso(self.clock.now()),
        }))
    }

    /// The password an account logs in with. A credential that names nothing to read, or a secret that isn't there,
    /// is a fault, as it is in the Python app.
    pub fn password(&self, account: &Account) -> Result<Secret, Fault> {
        let credential = CredentialRef::parse(Some(&account.credential));
        match credential.style {
            Some(Style::Keychain) => {
                let item = credential.item().filter(|i| !mm_kernel::text::strip(i).is_empty()).ok_or(NO_CREDENTIAL)?;
                self.keychain.find(item).map_err(|_| KEYCHAIN)?.ok_or(NO_CREDENTIAL)
            }
            Some(Style::Env | Style::Op | Style::ProtonBridge) => Err(NOT_YET),
            _ => Err(NO_CREDENTIAL),
        }
    }

    /// The certificate pinned for `email` at `endpoint`, against `seen`: the one pinned before, or `seen`, pinned now
    /// on first use, which writes the file.
    pub fn tls_pin(&self, email: &str, endpoint: &str, seen: &str) -> Result<Pin, Fault> {
        let same = |pinned: &Value| if pinned.as_str() == Some(seen) { Pin::Kept } else { Pin::Other };
        let mut raw = self.file.read()?;
        let Some(entry) = account::find(&mut raw, email) else {
            return Ok(Pin::NoAccount);
        };
        if let Some(pinned) = entry.get("tls_pins").and_then(|pins| pins.get(endpoint)).filter(|p| truthy(p)) {
            return Ok(same(pinned));
        }
        let _writing = self.write.lock().map_err(|_| Fault::new("PoisonedLock"))?;
        let mut raw = self.file.read()?;
        let Some(entry) = account::find(&mut raw, email) else {
            return Ok(Pin::NoAccount);
        };
        if !entry.get("tls_pins").is_some_and(Value::is_object) {
            entry.insert("tls_pins".into(), json!({}));
        }
        let Some(Value::Object(pins)) = entry.get_mut("tls_pins") else {
            return Err(MALFORMED);
        };
        if let Some(pinned) = pins.get(endpoint).filter(|p| truthy(p)) {
            return Ok(same(pinned));
        }
        pins.insert(endpoint.to_owned(), json!(seen));
        self.file.write(&raw)?;
        Ok(Pin::Kept)
    }
}

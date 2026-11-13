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
use crate::preferences;

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

/// What the page names each account by, for every entry with an address, disabled ones too, so mail stored before
/// one was disabled still reads with its name (`alias_map`, `webmail_map`, `signature_map`, `signature_default`).
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Faces {
    pub aliases: Vec<(String, Option<String>)>,
    /// Each account's webmail, where "Open original" goes for one whose provider has no link of its own.
    pub webmail: Vec<(String, Option<String>)>,
    /// Each account's signature, the default where it has none of its own.
    pub signatures: Vec<(String, String)>,
    pub signature_default: String,
}

/// `{e["email"]: value}`: a later entry with the same address takes the place of the first.
fn keyed<T>(map: &mut Vec<(String, T)>, email: &str, value: T) {
    match map.iter_mut().find(|(e, _)| e == email) {
        Some(slot) => slot.1 = value,
        None => map.push((email.to_owned(), value)),
    }
}

/// `(e.get(key) or "").strip() or None`.
fn stripped(entry: &Map<String, Value>, key: &str) -> Result<Option<String>, Fault> {
    match entry.get(key) {
        Some(Value::String(s)) => {
            let s = mm_kernel::text::strip(s);
            Ok((!s.is_empty()).then(|| s.to_owned()))
        }
        Some(v) if truthy(v) => Err(MALFORMED),
        _ => Ok(None),
    }
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

    /// What the page names each account by: its alias, webmail and signature.
    pub fn faces(&self) -> Result<Faces, Fault> {
        let raw = self.file.read()?;
        let mut faces = Faces {
            signature_default: account::clean_signature(
                &raw.get("signature_default").cloned().filter(truthy).unwrap_or_else(|| json!("")),
            ),
            ..Faces::default()
        };
        for entry in Directory::entries(&raw)? {
            let Some(Value::String(email)) = entry.get("email").filter(|e| truthy(e)) else { continue };
            let Value::Object(entry) = entry else { continue };
            keyed(&mut faces.aliases, email, stripped(entry, "alias")?);
            keyed(&mut faces.webmail, email, stripped(entry, "webmail")?);
            let own = account::clean_signature(entry.get("signature").unwrap_or(&Value::Null));
            let signature = if own.is_empty() { faces.signature_default.clone() } else { own };
            keyed(&mut faces.signatures, email, signature);
        }
        Ok(faces)
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

/// A person's preferences (`Directory.preferences` and its changes): what the Settings page shows and changes.
impl Directory {
    /// Every preference as it stands, and the AI's daily limit. Reading one writes nothing, so a file with no block
    /// keeps none.
    pub fn preferences(&self) -> Result<Value, Failure> {
        let raw = self.file.read()?;
        let read = preferences::read(&raw);
        Ok(Directory::preferences_answer(read, &self.file.read()?))
    }

    /// Keep what `asked` changes (named in the order `preferences::changeable` gives), and leave every other key in
    /// the file as it was. A value none can be, refused whole, writes nothing. The answer is each preference as it
    /// stands now.
    pub fn set_preferences(&self, asked: &Map<String, Value>) -> Result<Value, Failure> {
        self.change_preferences(|_| preferences::changes(asked).map_err(Failure::Problem))
    }

    /// What a browser held under its old keys, once, for what the file doesn't say yet. When there is nothing to
    /// take, nothing is written. The answer is each preference as it stands now.
    pub fn import_preferences(&self, held: &Map<String, Value>) -> Result<Value, Failure> {
        self.change_preferences(|raw| Ok(preferences::importable(held, raw)))
    }

    fn change_preferences(
        &self,
        wanted_of: impl FnOnce(&Map<String, Value>) -> Result<Map<String, Value>, Failure>,
    ) -> Result<Value, Failure> {
        let read = {
            let _writing = self.write.lock().map_err(|_| Fault::new("PoisonedLock"))?;
            let mut raw = self.file.read()?;
            let wanted = wanted_of(&raw)?;
            if !wanted.is_empty() {
                raw.entry("accounts").or_insert_with(|| json!([]));
                preferences::apply(&mut raw, &wanted);
                self.file.write(&raw)?;
            }
            preferences::read(&raw)
        };
        Ok(Directory::preferences_answer(read, &self.file.read()?))
    }

    /// The preferences, with the AI's daily limit as the Assistant reads it from the file as it is now.
    fn preferences_answer(mut read: Map<String, Value>, raw: &Map<String, Value>) -> Value {
        read.insert(preferences::AI_DAILY_LIMIT.to_owned(), preferences::dollars(preferences::daily_limit_usd(raw)));
        json!({"ok": true, "preferences": read})
    }
}

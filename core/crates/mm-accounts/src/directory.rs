//! The accounts as `mailmind.config.json` holds them, and what is kept for each (`mailmind.accounts.application
//! .directory`): the use cases that read the file, the one write a connection makes, a certificate's pin, and the
//! changes Settings makes.

use std::sync::{Arc, Mutex, MutexGuard};

use mm_kernel::errors::{Failure, Fault, Kind, Problem};
use mm_kernel::log;
use mm_kernel::seams::{Clock, Keychain, Secret, keepable_name, keepable_secret};
use mm_kernel::when;
use serde_json::{Map, Value, json};

use crate::account::{self, Account, truthy};
use crate::credential::{self, Asked, CredentialRef, Style, credential_from};
use crate::one_password::OpReads;
use crate::ports::ConfigFile;
use crate::preferences;

const MALFORMED: Fault = Fault::new("MalformedConfig");
const TAG: &str = "accounts";
const NO_CREDENTIAL: Fault = Fault::new("NoCredential");
const KEYCHAIN: Fault = Fault::new("KeychainProblem");
/// A credential kept where the Rust server can't read it yet: a variable or the Bridge password, or 1Password when the
/// Directory was given no way to read it.
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
    /// What 1Password gave this process, and the way to ask it for more: none until `reading_op` gives it.
    op: Option<Arc<OpReads>>,
}

impl Directory {
    pub fn new(file: Arc<dyn ConfigFile>, keychain: Arc<dyn Keychain>, clock: Arc<dyn Clock>) -> Directory {
        Directory { file, keychain, clock, write: Mutex::new(()), op: None }
    }

    /// The Directory, reading a 1Password credential through `op`, which the Assistant's key shares.
    pub fn reading_op(self, op: Arc<OpReads>) -> Directory {
        Directory { op: Some(op), ..self }
    }

    /// Forget what was read of `credential`'s 1Password references, once it is replaced or its account removed
    /// (`secrets.evict`).
    fn evict(&self, credential: Option<&Value>) {
        if let (Some(op), Some(refs)) = (&self.op, CredentialRef::parse(credential).op_refs()) {
            op.evict(refs);
        }
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

    /// Each saved entry's alias by its address, disabled ones too, so older mail still shows a friendly name: None
    /// where it has none (`alias_map`).
    pub fn alias_map(&self) -> Result<std::collections::BTreeMap<String, Option<String>>, Fault> {
        let raw = self.file.read()?;
        let mut aliases = std::collections::BTreeMap::new();
        for entry in Directory::entries(&raw)?.iter().filter_map(Value::as_object) {
            let Some(email) = entry.get("email").and_then(Value::as_str).filter(|e| !e.is_empty()) else { continue };
            let alias =
                entry.get("alias").and_then(Value::as_str).map(mm_kernel::text::strip).filter(|a| !a.is_empty());
            aliases.insert(email.to_owned(), alias.map(str::to_owned));
        }
        Ok(aliases)
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
    /// is a fault, as it is in the Python app. A 1Password read is kept for the process, its spaces taken out for an
    /// account that asks for that (`strip_spaces`, a Gmail app password).
    pub fn password(&self, account: &Account) -> Result<Secret, Fault> {
        let credential = CredentialRef::parse(Some(&account.credential));
        match credential.style {
            Some(Style::Keychain) => {
                let item = credential.item().filter(|i| !mm_kernel::text::strip(i).is_empty()).ok_or(NO_CREDENTIAL)?;
                self.keychain.find(item).map_err(|_| KEYCHAIN)?.ok_or(NO_CREDENTIAL)
            }
            Some(Style::Op) => match (&self.op, credential.op_refs()) {
                (Some(op), Some(refs)) => op.read(refs, account.strip_spaces),
                _ => Err(NOT_YET),
            },
            Some(Style::Env | Style::ProtonBridge) => Err(NOT_YET),
            _ => Err(NO_CREDENTIAL),
        }
    }

    /// Read every 1Password credential now (`preload`), so a long-running server needn't ask again: how many read. One
    /// that doesn't is logged, by its class, and the rest go on.
    pub fn preload(&self) -> Result<usize, Fault> {
        let mut read = 0;
        for account in self.accounts()? {
            if CredentialRef::parse(Some(&account.credential)).style != Some(Style::Op) {
                continue;
            }
            match self.password(&account) {
                Ok(_) => read += 1,
                Err(fault) => log::warning(TAG, "a credential's preload failed", &[], Some(fault.class)),
            }
        }
        Ok(read)
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

/// What a name the Keychain can't take is told.
const UNNAMEABLE: &str = "A Keychain item MailMind keeps is named in letters, digits and . _ % + @ : = - alone.";
/// What a secret the Keychain can't hold as itself is told.
const UNPRINTABLE: &str = "A secret MailMind keeps in the Keychain is printable ASCII, and not empty: letters, digits, punctuation and spaces.";

fn writing(lock: &Mutex<()>) -> Result<MutexGuard<'_, ()>, Fault> {
    lock.lock().map_err(|_| Fault::new("PoisonedLock"))
}

/// The address an entry the file holds is saved under.
fn email_of(entry: &Map<String, Value>) -> Result<String, Fault> {
    entry.get("email").and_then(Value::as_str).map(str::to_owned).ok_or(MALFORMED)
}

/// The changes Settings makes to the file: an account added, edited or removed, and its pins forgotten. A typed
/// password goes to the login Keychain, in the account's own item, before the file is written, and the file names the
/// item; a Keychain that won't is `Unreachable`, and the file is left as it was.
impl Directory {
    /// What the file holds of `asked`: a typed secret is kept in the Keychain as `item` first, and the file names the
    /// item, one version past `before`'s (`_kept`).
    fn kept(&self, item: &str, asked: Asked, before: Option<&Value>) -> Result<Value, Failure> {
        let typed = match asked {
            Asked::Stored(credential) => return Ok(credential),
            Asked::Typed(typed) => typed,
        };
        if !keepable_name(item) {
            return Err(Problem::refused(UNNAMEABLE).into());
        }
        if !keepable_secret(typed.expose()) {
            return Err(Problem::refused(UNPRINTABLE).into());
        }
        self.keychain.put(item, &typed).map_err(|_| {
            Problem::new(Kind::Unreachable, "The Keychain didn't keep the password. Is the login keychain locked?")
        })?;
        Ok(credential::keychain_credential(item, before))
    }

    /// Take the account's own Keychain item out, if it has one (`_let_go`). An address no item could be named for
    /// has none.
    fn let_go(&self, email: &str) -> Result<(), Failure> {
        let item = credential::account_item(email);
        if !keepable_name(&item) {
            return Ok(());
        }
        match self.keychain.delete(&item) {
            Ok(_) => Ok(()),
            Err(_) => Err(Problem::new(
                Kind::Unreachable,
                "The Keychain didn't let go of the password. Is the login keychain locked?",
            )
            .into()),
        }
    }

    /// Add the account `asked` describes (`add_account`): its row as the API may show it. One saved under its
    /// address already, one with no credential, and one `validate_new` refuses are refused, and write nothing.
    pub fn add_account(&self, asked: &Map<String, Value>) -> Result<Value, Failure> {
        let _writing = writing(&self.write)?;
        let mut raw = self.file.read()?;
        Directory::entries(&raw)?;
        let email = asked.get("email").and_then(Value::as_str).map(mm_kernel::text::strip).unwrap_or_default();
        if account::position(&raw, email).is_some() {
            return Err(Problem::refused(format!("{email} already exists")).into());
        }
        let mut entry = Map::new();
        entry.insert("email".into(), json!(email));
        entry.insert("type".into(), asked.get("type").cloned().unwrap_or_else(|| json!("imap")));
        account::apply_fields(&mut entry, asked)?;
        let credential =
            credential_from(asked.get("credential"))?.ok_or_else(|| Problem::refused("a credential is required"))?;
        account::refuse_bad_endpoints(&entry, &Map::new())?;
        account::validate_new(&entry)?;
        entry.insert("credential".into(), self.kept(&credential::account_item(email), credential, None)?);
        let row = account::masked_row(&Value::Object(entry.clone())).map_err(|_| MALFORMED)?;
        match raw.entry("accounts").or_insert_with(|| json!([])) {
            Value::Array(entries) => entries.push(Value::Object(entry)),
            _ => return Err(MALFORMED.into()),
        }
        self.file.write(&raw)?;
        Ok(row)
    }

    /// Change what `asked` names of the account saved under `email` (`update_account`): its row, or None when the
    /// file holds no such account. A credential is replaced only when `asked` gives one with a value, and the
    /// account's own Keychain item goes once its credential no longer names it.
    pub fn update_account(&self, email: &str, asked: &Map<String, Value>) -> Result<Option<Value>, Failure> {
        let _writing = writing(&self.write)?;
        let mut raw = self.file.read()?;
        Directory::entries(&raw)?;
        let Some(entry) = account::find(&mut raw, email) else {
            return Ok(None);
        };
        let before = entry.clone();
        let old = before.get("credential");
        account::apply_fields(entry, asked)?;
        let new = match asked.contains_key("credential") {
            true => credential_from(asked.get("credential"))?,
            false => None,
        };
        account::refuse_bad_endpoints(entry, &before)?;
        account::validate_new(entry)?;
        if let Some(new) = new {
            let saved_as = email_of(entry)?;
            let own = credential::account_item(&saved_as);
            entry.insert("credential".into(), self.kept(&own, new, old)?);
            if credential::names(old, &own) && !credential::names(entry.get("credential"), &own) {
                self.let_go(&saved_as)?;
            }
            self.evict(old);
        }
        let row = account::masked_row(&Value::Object(entry.clone())).map_err(|_| MALFORMED)?;
        self.file.write(&raw)?;
        Ok(Some(row))
    }

    /// Remove the account saved under `email`, and its own Keychain item (`remove_account`): the address it was
    /// saved under, or None when the file holds no such account.
    pub fn remove_account(&self, email: &str) -> Result<Option<String>, Failure> {
        let _writing = writing(&self.write)?;
        let mut raw = self.file.read()?;
        Directory::entries(&raw)?;
        let Some(at) = account::position(&raw, email) else {
            return Ok(None);
        };
        let Some(Value::Array(entries)) = raw.get_mut("accounts") else {
            return Err(MALFORMED.into());
        };
        let saved_as = entries.get(at).and_then(Value::as_object).ok_or(MALFORMED).and_then(email_of)?;
        self.let_go(&saved_as)?;
        self.evict(entries.get(at).and_then(|entry| entry.get("credential")));
        entries.remove(at);
        self.file.write(&raw)?;
        Ok(Some(saved_as))
    }

    /// Trust the account's servers again: forget each certificate pinned for it, so the next connection pins the one
    /// it meets (`forget_tls_pins`). The address it is saved under, or None when the file holds no such account.
    pub fn forget_tls_pins(&self, email: &str) -> Result<Option<String>, Fault> {
        let _writing = writing(&self.write)?;
        let mut raw = self.file.read()?;
        let Some(entry) = account::find(&mut raw, email) else {
            return Ok(None);
        };
        let saved_as = email_of(entry)?;
        if entry.shift_remove("tls_pins").is_some_and(|pins| !pins.is_null()) {
            self.file.write(&raw)?;
        }
        Ok(Some(saved_as))
    }
}

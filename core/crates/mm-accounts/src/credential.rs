//! Where an account's secret is, as `mailmind.config.json` names it (`mailmind.accounts.domain.credential`):
//!
//! ```text
//! { "keychain": "account:you@example.com", "version": 2 }   a generic password in the login Keychain
//! { "op": "op://Vault/Item/Field" }                          the 1Password CLI, or a list of references tried in order
//! { "env": "ENV_VAR_NAME" }                                  an environment variable
//! { "proton_bridge": true }                                  the Proton Mail Bridge password
//! ```
//!
//! An entry naming two gets the first of keychain, env, proton_bridge, op; one naming plain, or a bare string, reads
//! as the style "plain" only so it can be refused. No credential may name the item the database's key is kept in.

use mm_kernel::errors::Problem;
use mm_kernel::seams::Secret;
use mm_kernel::{parsed, text};
use serde_json::{Value, json};

use crate::account::truthy;

/// The Keychain item the database's key is kept in, which no credential may name.
pub const DATABASE_KEY_ITEM: &str = "database-key";

/// The Keychain item MailMind keeps `email`'s password in.
pub fn account_item(email: &str) -> String {
    format!("account:{email}")
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Style {
    Plain,
    Keychain,
    Op,
    Env,
    ProtonBridge,
    Unknown,
}

impl Style {
    pub fn name(self) -> &'static str {
        match self {
            Style::Plain => "plain",
            Style::Keychain => "keychain",
            Style::Op => "op",
            Style::Env => "env",
            Style::ProtonBridge => "proton_bridge",
            Style::Unknown => "unknown",
        }
    }
}

/// Where an account's secret is. `style` is None when there is no credential at all. `named` is what the style's key
/// holds: the Keychain item's name, the op reference or references, the variable's name, or for plain the password
/// itself, so `Debug` never shows it.
#[derive(Clone, PartialEq)]
pub struct CredentialRef {
    pub style: Option<Style>,
    named: Value,
    /// A Keychain credential's: how many secrets MailMind has kept under its item, so a new one reads as new.
    version: Value,
}

impl std::fmt::Debug for CredentialRef {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("CredentialRef").field("style", &self.style).finish_non_exhaustive()
    }
}

impl CredentialRef {
    pub fn parse(credential: Option<&Value>) -> CredentialRef {
        let of =
            |style, named: &Value| CredentialRef { style: Some(style), named: named.clone(), version: Value::Null };
        let cred = match credential {
            Some(Value::String(s)) => return of(Style::Plain, &Value::String(s.clone())),
            Some(Value::Object(cred)) if !cred.is_empty() => cred,
            _ => return CredentialRef { style: None, named: Value::Null, version: Value::Null },
        };
        if let Some(plain) = cred.get("plain") {
            return of(Style::Plain, plain);
        }
        if let Some(item) = cred.get("keychain") {
            if item == DATABASE_KEY_ITEM {
                return of(Style::Unknown, &Value::Null);
            }
            let version = cred.get("version").cloned().unwrap_or(Value::Null);
            return CredentialRef { version, ..of(Style::Keychain, item) };
        }
        if let Some(var) = cred.get("env") {
            return of(Style::Env, var);
        }
        if cred.get("proton_bridge").is_some_and(truthy) {
            return of(Style::ProtonBridge, &Value::Bool(true));
        }
        if let Some(refs) = cred.get("op") {
            return of(Style::Op, refs);
        }
        of(Style::Unknown, &Value::Null)
    }

    /// The Keychain item a keychain credential names.
    pub fn item(&self) -> Option<&str> {
        match self.style {
            Some(Style::Keychain) => self.named.as_str(),
            _ => None,
        }
    }

    /// What an op credential names: a 1Password reference, or a list of them (`one_password::references`).
    pub fn op_refs(&self) -> Option<&Value> {
        match self.style {
            Some(Style::Op) => Some(&self.named),
            _ => None,
        }
    }

    /// A Keychain credential's version, as the file wrote it.
    pub fn version(&self) -> &Value {
        &self.version
    }

    /// The credential as the API may show it: its style and whether it is set, and only ever a pointer. Never a value,
    /// a length, a prefix or a hash.
    pub fn masked(&self) -> Value {
        match self.style {
            Some(Style::Keychain) => {
                let item = self.named.as_str();
                json!({"style": "keychain", "set": item.is_some_and(|i| !mm_kernel::text::strip(i).is_empty()),
                       "item": item.unwrap_or("")})
            }
            Some(Style::Op) => json!({"style": "op", "set": truthy(&self.named), "ref": self.named}),
            Some(Style::Env) => json!({"style": "env", "set": truthy(&self.named), "var": self.named}),
            Some(Style::Unknown) => json!({"style": "unknown", "set": false}),
            Some(style) => json!({"style": style.name(), "set": truthy(&self.named)}),
            None => json!({"style": null, "set": false}),
        }
    }
}

/// What a request asks the file to hold of an account's credential (`credential_from`).
#[derive(Debug)]
pub enum Asked {
    /// A password typed into Settings, for the Keychain: the file never holds it.
    Typed(Secret),
    /// Where a secret is, as the file holds it: a 1Password reference, a variable or the Bridge password.
    Stored(Value),
}

/// Text that isn't blank.
fn text_given(held: &Value) -> bool {
    held.as_str().is_some_and(|t| !text::strip(t).is_empty())
}

/// What a request's `{style, value|ref|var}` asks for (`credential_from`): None keeps the credential there is,
/// for one with no style it reads. One it can't take is refused in a sentence that holds nothing typed.
pub fn credential_from(asked: Option<&Value>) -> Result<Option<Asked>, Problem> {
    let Some(asked) = asked.filter(|a| truthy(a)) else { return Ok(None) };
    let Value::Object(asked) = asked else {
        return Err(Problem::refused("a credential names its style"));
    };
    match asked.get("style").and_then(Value::as_str) {
        Some("keychain") => match asked.get("value") {
            Some(Value::String(typed)) if !typed.is_empty() => Ok(Some(Asked::Typed(Secret::new(typed.clone())))),
            _ => Err(Problem::refused("password required")),
        },
        Some("op") => {
            let given = asked.get("ref").cloned().unwrap_or(Value::Null);
            let listed = given.as_array().is_some_and(|refs| !refs.is_empty() && refs.iter().all(text_given));
            if !(text_given(&given) || listed) {
                return Err(Problem::refused("1Password reference required"));
            }
            Ok(Some(Asked::Stored(json!({"op": given}))))
        }
        Some("env") => match asked.get("var") {
            Some(var @ Value::String(name)) if text_given(var) => {
                Ok(Some(Asked::Stored(json!({"env": text::strip(name)}))))
            }
            _ => Err(Problem::refused("environment variable name required")),
        },
        Some("proton_bridge") => Ok(Some(Asked::Stored(json!({"proton_bridge": true})))),
        _ => Ok(None),
    }
}

/// The credential naming `item` once a secret is kept there (`keychain_credential`). Its version is one past
/// `before`'s when that named the same item, so the file says the secret changed.
pub fn keychain_credential(item: &str, before: Option<&Value>) -> Value {
    let was = CredentialRef::parse(before);
    let kept = match was.item() {
        Some(named) if named == item => parsed::as_int(was.version()),
        _ => None,
    };
    json!({"keychain": item, "version": kept.unwrap_or(0).max(0) + 1})
}

/// Whether `credential` names the Keychain item `item` (`_names`).
pub fn names(credential: Option<&Value>, item: &str) -> bool {
    CredentialRef::parse(credential).item() == Some(item)
}

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
}

impl std::fmt::Debug for CredentialRef {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("CredentialRef").field("style", &self.style).finish_non_exhaustive()
    }
}

impl CredentialRef {
    pub fn parse(credential: Option<&Value>) -> CredentialRef {
        let of = |style, named: &Value| CredentialRef { style: Some(style), named: named.clone() };
        let cred = match credential {
            Some(Value::String(s)) => return of(Style::Plain, &Value::String(s.clone())),
            Some(Value::Object(cred)) if !cred.is_empty() => cred,
            _ => return CredentialRef { style: None, named: Value::Null },
        };
        if let Some(plain) = cred.get("plain") {
            return of(Style::Plain, plain);
        }
        if let Some(item) = cred.get("keychain") {
            if item == DATABASE_KEY_ITEM {
                return of(Style::Unknown, &Value::Null);
            }
            return of(Style::Keychain, item);
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

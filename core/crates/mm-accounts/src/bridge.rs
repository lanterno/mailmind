//! The Proton Bridge's password (`mailmind.accounts.adapters.secrets`' `bridge_password`): what an account whose
//! credential is `{"proton_bridge": true}` logs in with, the one password the Bridge gives every Proton account.
//!
//! It is the first of: what the environment gives itself (MAILMIND_PROTON_BRIDGE_PASS); 1Password's
//! `op://Private/<item>/password`, when the environment names the item (MAILMIND_PROTON_OP_ITEM); and the login
//! Keychain's item `proton-bridge-password` (`ITEM`), which Settings keeps and takes out. The Python app reads a file
//! after those, where a desktop app keeps no secret. What is found is kept for the process, as Python keeps it, until
//! Settings keeps another or takes it out (`forget`).
//!
//! A source that gives nothing, an op read that failed or printed only spaces included, passes to the next. A Keychain
//! that doesn't answer is a fault, as it is for an account's own item.

use std::fmt;
use std::sync::{Mutex, MutexGuard, PoisonError};

use mm_kernel::errors::Fault;
use mm_kernel::seams::{Keychain, Secret};
use serde_json::Value;

use crate::one_password::OpReads;

/// The login Keychain's item MailMind keeps the Bridge's password in.
pub const ITEM: &str = "proton-bridge-password";

const NO_CREDENTIAL: Fault = Fault::new("NoCredential");
const KEYCHAIN: Fault = Fault::new("KeychainProblem");

/// What the environment says of the Bridge's password: the password itself, and the 1Password item that holds it.
/// Each is None when unset or empty.
#[derive(Default)]
pub struct BridgeGiven {
    pub password: Option<Secret>,
    pub op_item: Option<String>,
}

/// Whether each is given, and nothing of either.
impl fmt::Debug for BridgeGiven {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("BridgeGiven")
            .field("password", &self.password.is_some())
            .field("op_item", &self.op_item.is_some())
            .finish()
    }
}

impl BridgeGiven {
    /// The 1Password reference the environment's item names: its `password` field, in the vault `Private`.
    pub fn op_reference(&self) -> Option<String> {
        self.op_item.as_ref().map(|item| format!("op://Private/{item}/password"))
    }
}

/// The Bridge's password as this process has it.
#[derive(Debug, Default)]
pub(crate) struct Bridge {
    given: BridgeGiven,
    /// What was found, once found.
    kept: Mutex<Option<Secret>>,
}

fn copy(secret: &Secret) -> Secret {
    Secret::new(secret.expose().to_owned())
}

impl Bridge {
    pub(crate) fn new(given: BridgeGiven) -> Bridge {
        Bridge { given, kept: Mutex::new(None) }
    }

    fn kept(&self) -> MutexGuard<'_, Option<Secret>> {
        // What is kept is whole or none by the time the lock is let go.
        self.kept.lock().unwrap_or_else(PoisonError::into_inner)
    }

    /// The password: the one kept, or the first source's that gives one, kept from now on. One lookup at a time, so
    /// two logins at once ask 1Password and the Keychain once.
    pub(crate) fn password(&self, keychain: &dyn Keychain, op: Option<&OpReads>) -> Result<Secret, Fault> {
        let mut kept = self.kept();
        if let Some(secret) = kept.as_ref() {
            return Ok(copy(secret));
        }
        let found = self.find(keychain, op)?;
        *kept = Some(copy(&found));
        Ok(found)
    }

    fn find(&self, keychain: &dyn Keychain, op: Option<&OpReads>) -> Result<Secret, Fault> {
        if let Some(given) = self.given.password.as_ref().filter(|p| !p.expose().is_empty()) {
            return Ok(copy(given));
        }
        if let (Some(op), Some(reference)) = (op, self.given.op_reference())
            && let Ok(read) = op.read(&Value::String(reference), false)
            && !read.expose().is_empty()
        {
            return Ok(read);
        }
        match keychain.find(ITEM) {
            Ok(Some(secret)) if !secret.expose().is_empty() => Ok(secret),
            Ok(_) => Err(NO_CREDENTIAL),
            Err(_) => Err(KEYCHAIN),
        }
    }

    /// Let go of what was found, so the next login looks again: once Settings has kept another or taken it out. A
    /// lookup running now ends first, so what it found is let go too.
    pub(crate) fn forget(&self) {
        *self.kept() = None;
    }
}

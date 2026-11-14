//! A secret kept in 1Password, read as the Python app reads one (`mailmind.accounts.adapters.secrets`' `_op_read`,
//! `resolve` and `evict`): a credential `{"op": "op://Vault/Item/Field"}`, or a list of references tried in turn,
//! read through the `OnePassword` port.
//!
//! Each reference is read once a process, and kept only once it reads, so one that failed is asked again next time.
//! What it read is kept trimmed, and without its spaces when the first to read it asked for that (a Gmail app password
//! as 1Password keeps it). `evict` drops what a credential's references read, once it is replaced or its account
//! removed; a new process starts with none. One read runs at a time, so a reference two logins want at once is asked
//! of op once.
//!
//! A reference goes on op's command line, so only one that is `readable` is ever sent: it starts `op://`, so op can't
//! take it for an option, and holds no control character. Nothing here says what a secret is or what op said: a read
//! that failed is a fault, by its class.

use std::collections::HashMap;
use std::fmt;
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};

use mm_kernel::errors::Fault;
use mm_kernel::seams::Secret;
use serde_json::Value;

use crate::ports::OnePassword;

/// No reference the credential names could be read.
pub const UNREAD: Fault = Fault::new("OpReadFailed");
/// The credential names no reference at all.
const NO_REFERENCE: Fault = Fault::new("NoCredential");

/// Whether `reference` may go on op's command line: a secret reference, which starts `op://`, with no control
/// character in it.
pub fn readable(reference: &str) -> bool {
    reference.starts_with("op://") && !reference.chars().any(char::is_control)
}

/// The references an op credential names, in the order they're tried (`op_refs`): each of a list, or the one.
pub fn references(named: &Value) -> Vec<&Value> {
    match named {
        Value::Array(refs) => refs.iter().collect(),
        one => vec![one],
    }
}

/// What 1Password gave this process, by reference.
pub struct OpReads {
    vault: Arc<dyn OnePassword>,
    kept: Mutex<HashMap<String, Secret>>,
    /// One read of op at a time.
    reading: Mutex<()>,
}

impl fmt::Debug for OpReads {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("OpReads(..)")
    }
}

impl OpReads {
    /// Reading through `vault`, with nothing read yet.
    pub fn new(vault: Arc<dyn OnePassword>) -> OpReads {
        OpReads { vault, kept: Mutex::new(HashMap::new()), reading: Mutex::new(()) }
    }

    fn kept(&self) -> MutexGuard<'_, HashMap<String, Secret>> {
        // Each change to what is kept is whole by the time the lock is let go.
        self.kept.lock().unwrap_or_else(PoisonError::into_inner)
    }

    fn kept_for(&self, reference: &str) -> Option<Secret> {
        self.kept().get(reference).map(|kept| Secret::new(kept.expose().to_owned()))
    }

    /// The secret the first of the references `named` gives that reads (`resolve` of an op credential). `strip` takes
    /// the spaces out of one read now. When none reads, the fault of the last.
    pub fn read(&self, named: &Value, strip: bool) -> Result<Secret, Fault> {
        let mut last = NO_REFERENCE;
        for one in references(named) {
            let read = match one.as_str() {
                Some(reference) => self.read_one(reference, strip),
                None => Err(UNREAD),
            };
            match read {
                Ok(secret) => return Ok(secret),
                Err(fault) => last = fault,
            }
        }
        Err(last)
    }

    fn read_one(&self, reference: &str, strip: bool) -> Result<Secret, Fault> {
        if let Some(kept) = self.kept_for(reference) {
            return Ok(kept);
        }
        if !readable(reference) {
            return Err(UNREAD);
        }
        let _reading = self.reading.lock().unwrap_or_else(PoisonError::into_inner);
        // Another read of it may have ended while this one waited.
        if let Some(kept) = self.kept_for(reference) {
            return Ok(kept);
        }
        let printed = self.vault.read(reference)?;
        let trimmed = mm_kernel::text::strip(printed.expose());
        let secret = Secret::new(if strip { trimmed.replace(' ', "") } else { trimmed.to_owned() });
        self.kept().insert(reference.to_owned(), Secret::new(secret.expose().to_owned()));
        Ok(secret)
    }

    /// Drop what each reference `named` gives read as (`evict`), so the next read asks op again.
    pub fn evict(&self, named: &Value) {
        let mut kept = self.kept();
        for reference in references(named).into_iter().filter_map(Value::as_str) {
            kept.remove(reference);
        }
    }
}

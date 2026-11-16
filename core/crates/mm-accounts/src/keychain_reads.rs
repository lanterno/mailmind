//! A secret kept in the login Keychain, read as the Python app reads one (`mailmind.accounts.adapters.secrets`'
//! `_keychain_read`, `keep`, `discard` and `evict`): once a process for each item and the version of it the config
//! names, so a secret kept again, by this process or another, which names the next version, is read again.
//!
//! A read is kept only once it finds the item; one that found none, or a Keychain that couldn't be asked, is asked
//! again next time. What was read of an item is forgotten, every version of it, as MailMind keeps the item again or
//! takes it out (before and after, so a read that ran meanwhile isn't kept), and as a credential that named it is
//! replaced. A new process starts with none. Nothing here says what a secret is.

use std::collections::HashMap;
use std::fmt;
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};

use mm_kernel::seams::{Keychain, KeychainProblem, Secret};
use serde_json::Value;

/// What the Keychain gave this process, by item and the version the config names.
pub struct KeychainReads {
    keychain: Arc<dyn Keychain>,
    kept: Mutex<HashMap<(String, String), Secret>>,
}

impl fmt::Debug for KeychainReads {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("KeychainReads(..)")
    }
}

impl KeychainReads {
    /// Reading `keychain`, with nothing read yet.
    pub fn new(keychain: Arc<dyn Keychain>) -> KeychainReads {
        KeychainReads { keychain, kept: Mutex::new(HashMap::new()) }
    }

    fn kept(&self) -> MutexGuard<'_, HashMap<(String, String), Secret>> {
        // Each change to what is kept is whole by the time the lock is let go.
        self.kept.lock().unwrap_or_else(PoisonError::into_inner)
    }

    /// The secret the Keychain keeps as `item`, as this process read it under `version` (the config's), else read
    /// now and kept once found. None when it holds none.
    pub fn read(&self, item: &str, version: &Value) -> Result<Option<Secret>, KeychainProblem> {
        let at = (item.to_owned(), version.to_string());
        if let Some(kept) = self.kept().get(&at) {
            return Ok(Some(Secret::new(kept.expose().to_owned())));
        }
        let found = self.keychain.find(item)?;
        if let Some(secret) = &found {
            self.kept().insert(at, Secret::new(secret.expose().to_owned()));
        }
        Ok(found)
    }

    /// Forget what was read of `item`, under every version (`_drop`), so the next read asks the Keychain again.
    pub fn forget(&self, item: &str) {
        self.kept().retain(|(named, _), _| named != item);
    }
}

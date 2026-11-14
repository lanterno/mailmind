//! What IDLE's workers need of the world (`realtime`): the accounts to watch and how each is reached, a session that
//! idles, and who hears that an INBOX changed.

use std::collections::BTreeSet;
use std::fmt;
use std::sync::Arc;
use std::time::Duration;

use mm_kernel::errors::Fault;
use serde_json::Value;

use crate::idle_supervisor::Interrupt;
use crate::session::{Selected, ServerError};

/// An account to watch, and how it is reached: everything a login depends on (its kind, host, port, TLS,
/// credential and whether spaces are stripped from its password), as a value compared whole. An alias or a
/// signature isn't in it.
#[derive(Clone, Debug, PartialEq)]
pub struct Reached {
    pub email: String,
    pub way: Value,
}

/// The accounts, as the configuration has them now.
pub trait IdleAccounts: Send + Sync + fmt::Debug {
    /// Each account to watch, enabled ones alone, in the configuration's order.
    fn accounts(&self) -> Result<Vec<Reached>, Fault>;
    /// How `email` is reached now; None when it is gone or disabled.
    fn get(&self, email: &str) -> Result<Option<Reached>, Fault>;
}

/// A logged-in session that waits on its selected folder (`MailSession.idle` and `interrupt`).
pub trait IdleSession: Send + fmt::Debug {
    /// The server's capabilities, upper-case, as it listed them once logged in.
    fn capabilities(&self) -> Vec<String>;
    fn select(&mut self, folder: &str, readonly: bool) -> Result<Selected, ServerError>;
    /// IDLE on the selected folder for at most `wait`: whether it changed. It leaves IDLE (DONE) before it returns.
    fn idle(&mut self, wait: Duration) -> Result<bool, ServerError>;
    /// What ends an `idle` on this session from another thread, closing nothing; the session is spent.
    fn interrupter(&self) -> Result<Arc<dyn Interrupt>, ServerError>;
    /// Log out and close, however it stands.
    fn logout(&mut self);
}

/// Where IDLE's sessions come from: each logs in as every other does, so a login its server refused is held back.
pub trait IdleSessions: Send + Sync + fmt::Debug {
    fn open(&self, email: &str) -> Result<Box<dyn IdleSession>, ServerError>;
}

/// Who hears that IDLE saw these accounts' INBOX change, a burst of signals coalesced into one (InboxChanged):
/// whoever hears it syncs them.
pub trait InboxNews: Send + Sync + fmt::Debug {
    fn inbox_changed(&self, accounts: BTreeSet<String>);
}

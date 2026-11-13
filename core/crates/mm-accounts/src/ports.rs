//! What the Accounts use cases need of the world.

use mm_kernel::errors::Fault;
use serde_json::{Map, Value};

/// `mailmind.config.json`: the accounts, where each one's secret is, and the top-level settings.
pub trait ConfigFile: Send + Sync + std::fmt::Debug {
    /// The file's contents, a copy of the caller's own, or `{"accounts": []}` when there is no file. A file that
    /// isn't a JSON object is a fault, every time it is read.
    fn read(&self) -> Result<Map<String, Value>, Fault>;

    /// Replace the file with `raw`, atomically and readable by its owner only.
    fn write(&self, raw: &Map<String, Value>) -> Result<(), Fault>;
}

/// What an account's syncs came to, as the Mailbox recorded them, and how its sync stands now. Of an error it holds
/// the class's name and its kind, never what it said.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Synced {
    pub account: String,
    pub last_ok_ts: Option<String>,
    pub last_error_ts: Option<String>,
    /// As stored, so an older row may say what no kind says now.
    pub last_error_kind: Option<String>,
    pub last_error: Option<String>,
    pub sync_count: i64,
    /// "ok", "syncing", "error", or "" when none has run since the start.
    pub live: String,
}

/// The Mailbox's record of each account's syncs.
pub trait SyncRecord: Send + Sync + std::fmt::Debug {
    fn sync_record(&self) -> Result<Vec<Synced>, Fault>;
}

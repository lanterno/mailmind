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

/// 1Password, through its CLI: the secret a reference (`op://Vault/Item/Field`) names, as `op read` prints it.
pub trait OnePassword: Send + Sync + std::fmt::Debug {
    /// What op printed for `reference`, untrimmed, or the class of why it gave nothing: never what op said.
    fn read(&self, reference: &str) -> Result<mm_kernel::seams::Secret, Fault>;
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

/// Why a login didn't work, by the class of its trouble alone (`mm_imap`'s and `mm_net`'s, or a fault's): never
/// what a server said.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct LoginTrouble(pub &'static str);

/// An account's server, asked whether a login works there: a login, and then a logout.
pub trait LoginProbe: Send + Sync + std::fmt::Debug {
    /// Log a saved account in at its saved server, with its saved secret.
    fn saved(&self, account: &crate::Account, timeout: std::time::Duration) -> Result<(), LoginTrouble>;

    /// Log a draft in where it was typed, with the password typed with it.
    fn draft(&self, draft: &crate::draft::Draft, timeout: std::time::Duration) -> Result<(), LoginTrouble>;
}

/// What happened to an account, once the file holds it or once its server has answered
/// (`mailmind.accounts.domain.events`): each names the account by its address, and says nothing of its credential.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum AccountChange {
    Added(String),
    /// Its settings changed: endpoint, credential, alias, signature, or the certificates trusted for it.
    Changed(String),
    Removed(String),
    /// Its server answered a login with no: which login that was (`login_hold::login_id`).
    CredentialsRejected(String, crate::login_hold::LoginId),
}

/// Who hears of an account's change: its logins, and its IDLE (`bus.publish` in the Python app).
pub trait AccountEvents: Send + Sync + std::fmt::Debug {
    fn publish(&self, change: AccountChange);
}

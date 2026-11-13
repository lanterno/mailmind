//! What Compose's use cases need of the world: the outbox and the drafts' journal, as stored.

use std::fmt;

use mm_kernel::errors::Fault;

/// A stored row, by column name, each value as SQLite holds it.
pub type Row = serde_json::Map<String, serde_json::Value>;

/// The outbox and the drafts' journal, read.
pub trait StoredOutbox: Send + Sync + fmt::Debug {
    /// The newest sends first, at most `limit` of them (`outbox_list`): a negative limit is no limit, as SQLite's.
    fn recent(&self, limit: i64) -> Result<Vec<Row>, Fault>;
    /// The saves that should have reached their server by `before` and haven't, newest first, leaving out a draft
    /// whose send is queued or going (`journal_overdue`).
    fn overdue(&self, before: i64) -> Result<Vec<Row>, Fault>;
}

/// A draft MailMind tracks (`drafts`), as a message opens.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Tracked {
    pub account: String,
    pub folder: Option<String>,
    pub uid: Option<i64>,
    pub message_id: Option<String>,
    pub uidvalidity: Option<i64>,
    pub mode: &'static str,
    pub subject: Option<String>,
    /// When it was first tracked, and last changed: the same moment for a new one.
    pub at: String,
}

/// The drafts MailMind tracks.
pub trait StoredDrafts: Send + Sync + fmt::Debug {
    /// The draft an account tracks under a Message-ID (`draft_by_msgid`): none for a message with none.
    fn by_message_id(&self, account: &str, message_id: Option<&str>) -> Result<Option<Row>, Fault>;
    /// Track a draft found on its server, and say its id (`draft_upsert` of a new row).
    fn track(&self, draft: &Tracked) -> Result<i64, Fault>;
    /// A folder's UIDVALIDITY as its last sync read it (the Mailbox's `get_uidvalidity`).
    fn uidvalidity(&self, account: &str, folder: &str) -> Result<Option<i64>, Fault>;
}

/// What the Mailbox says of a folder, for a draft.
pub trait DraftFolders: Send + Sync + fmt::Debug {
    /// Whether `folder` is the account's Drafts: what its last LIST said, else what its name says on its own.
    fn plays_drafts(&self, account: &str, folder: &str) -> Result<bool, Fault>;
}

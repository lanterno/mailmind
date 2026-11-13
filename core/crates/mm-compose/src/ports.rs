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

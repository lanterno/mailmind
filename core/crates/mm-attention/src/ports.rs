//! What Attention's use cases need of the world (`mailmind.attention.application.ports`): its own tables, the rows of
//! the messages it names, the accounts and the undo stack. Each `AttentionBook` call is one block of the database, as
//! each `with uow.write()` of the Python use cases is.

use std::fmt;

use mm_kernel::errors::Fault;
use serde_json::Value;

use crate::followups::FollowUp;

/// A message's row as Attention reads one.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Message {
    pub pk: i64,
    pub account: String,
    pub thread_id: Option<String>,
    pub key: Option<i64>,
    pub folder: String,
    pub seen: bool,
    /// When `snoozes` says it wakes, if it is snoozed.
    pub snooze_until: Option<i64>,
}

/// A copy of a message by what outlives its row: the message's key and its folder.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Copy {
    pub key: Option<i64>,
    pub folder: String,
}

/// What muting a thread found: its unread members, and one member whichever, which can say later which thread this
/// has become.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct MuteFound {
    pub unread: Vec<Copy>,
    pub member: Option<Copy>,
}

/// A task and the copy its message has now, none where it has gone.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TaskRow {
    pub id: i64,
    pub account: String,
    pub key: Option<i64>,
    pub title: String,
    pub due: Option<String>,
    pub done_at: Option<String>,
    pub folder: Option<String>,
    pub uid: Option<i64>,
    pub thread_id: Option<String>,
    pub subject: Option<String>,
    pub from_name: Option<String>,
    pub from_addr: Option<String>,
}

/// A thread waiting on someone, by its latest message.
#[derive(Clone, Debug, PartialEq)]
pub struct WaitingRow {
    pub key: Option<i64>,
    pub folder: String,
    pub uid: Option<i64>,
    pub from_name: Option<String>,
    pub from_addr: Option<String>,
    pub subject: Option<String>,
    pub date_iso: Option<String>,
    pub snippet: Option<String>,
    pub seen: bool,
    pub flagged: bool,
    pub has_attach: bool,
    pub is_junk: bool,
    pub bucket: Option<String>,
    pub thread_id: Option<String>,
    pub ts: Option<i64>,
    pub thread_count: i64,
    pub account: String,
    pub kind: String,
    pub first_sent: Option<i64>,
    pub recipients: Option<String>,
}

/// Snoozes, mutes, follow-ups and tasks (`adapters.sqlite`).
pub trait AttentionBook: Send + Sync + fmt::Debug {
    /// Message `pk`'s row, or None.
    fn message(&self, pk: i64) -> Result<Option<Message>, Fault>;

    /// Snooze until `until` the messages of `thread`, an account and a thread id, where one is named, else `pks`. Each
    /// pk, with its row as it was before, None where it has none.
    fn snooze(
        &self,
        thread: Option<(&str, &str)>,
        pks: &[i64],
        until: i64,
    ) -> Result<Vec<(i64, Option<Message>)>, Fault>;
    /// Wake those messages now. How many were named.
    fn unsnooze(&self, thread: Option<(&str, &str)>, pks: &[i64]) -> Result<usize, Fault>;
    /// Every snooze come due by `now` ended: the rows it brings back. One with no copy left in its folder ends too,
    /// bringing nothing.
    fn wake_due(&self, now: i64) -> Result<Vec<Message>, Fault>;

    /// Mute the thread, unless it is muted already (None).
    fn mute(&self, account: &str, thread_id: &str, when: &str) -> Result<Option<MuteFound>, Fault>;
    fn unmute(&self, account: &str, thread_id: &str) -> Result<(), Fault>;

    /// Change the thread's follow-up: `change` is given it as it stands and says what it becomes, None for nothing
    /// to write; `delete` true takes the row away.
    fn follow_up(
        &self,
        account: &str,
        thread_id: &str,
        change: &mut dyn FnMut(Option<FollowUp>, Option<i64>) -> FollowUpChange,
    ) -> Result<(), Fault>;
    /// The minute's round over every thread: resolve, expire, promote and detect, for `accounts`, at `now`. Whether
    /// the due follow-ups changed.
    fn follow_up_round(&self, accounts: &[String], now: i64, when: &str) -> Result<bool, Fault>;
    /// Each sent thread of `accounts` still with no reply, the longest wait first, at most `limit`.
    fn waiting_on_others(&self, accounts: &[String], limit: i64) -> Result<Vec<WaitingRow>, Fault>;

    /// A task from message `pk`, by its account, key and folder, or None where it has no row or no key.
    fn add_task(&self, pk: i64, title: &str, due: Option<&str>, when: &str) -> Result<Option<TaskRow>, Fault>;
    fn tasks(&self) -> Result<Vec<TaskRow>, Fault>;
    /// Whether the task was there.
    fn set_task_done(&self, id: i64, done_at: Option<&str>) -> Result<bool, Fault>;
    fn remove_task(&self, id: i64) -> Result<bool, Fault>;
}

/// What a follow-up becomes.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum FollowUpChange {
    Nothing,
    Put(FollowUp),
    /// Only its state and when it was resolved change.
    Settle(FollowUp),
    Delete,
}

/// The accounts the app may use, by address.
pub trait Accounts: Send + Sync + fmt::Debug {
    fn emails(&self) -> Result<Vec<String>, Fault>;
}

/// The undo stack.
pub trait Undo: Send + Sync + fmt::Debug {
    fn push(&self, entry: &Value, group: Option<&str>) -> Result<(), Fault>;
}

/// A flag change on the mail server, through the Mailbox's queue (the SetFlags port).
pub trait Flags: Send + Sync + fmt::Debug {
    /// Mark the copies read or unread there; None where this server can't yet.
    fn mark_seen(&self, copies: &[Copy], seen: bool) -> Result<Option<()>, Fault>;
}

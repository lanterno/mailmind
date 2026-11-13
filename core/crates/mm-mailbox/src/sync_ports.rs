//! What a sync needs of the world (`mailbox.application.ports` as `Ingest` uses them): the accounts' kinds, the
//! classifier Curation keeps, the store's rows and the events others hear. The store (`mm-store`) and the program
//! implement them. They stand apart from `ports` so the folder rail's and the sync's grow apart.

use std::collections::HashSet;
use std::fmt;

use mm_content::mime::Fields;
use mm_kernel::errors::Fault;
use mm_kernel::events::Topic;

/// What a sync needs to know of an account.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SyncAccount {
    pub email: String,
    /// Its kind (`imap`, `gmail`, `proton`), stored on each row.
    pub kind: String,
    /// Gmail's: it understands X-GM-THRID and X-GM-LABELS.
    pub gmail_threads: bool,
}

pub trait SyncAccounts: Send + Sync + fmt::Debug {
    /// The account, or None when the Directory knows no such one.
    fn sync_account(&self, account: &str) -> Result<Option<SyncAccount>, Fault>;
}

/// The classifier's verdict, as the row stores it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Verdict {
    pub bucket: String,
    pub confidence: String,
    pub reason: String,
    pub is_junk: bool,
}

/// What the classifier reads of a message: its sender's address and name, its subject, and whether it says it is
/// from a list.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Envelope<'a> {
    pub from_addr: &'a str,
    pub subject: &'a str,
    pub from_name: &'a str,
    pub has_list_unsub: bool,
}

/// Curation's classifier over the rule book as it stands (`bootstrap.curation.classify`): a pass reads the rule
/// book once, for every message it fetched.
pub trait Classifier: Send + Sync + fmt::Debug {
    fn classify(&self, messages: &[Envelope<'_>]) -> Result<Vec<Verdict>, Fault>;
}

/// One message as a sync stores it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Incoming {
    pub uid: u32,
    /// The flag columns (`sync_plan::COLUMNS`).
    pub flags: [i64; 5],
    pub fields: Fields,
    pub gmail_thread: Option<String>,
    pub gmail_labels: Option<Vec<String>>,
    pub verdict: Verdict,
    /// Its body's text, for the Inbox; empty elsewhere and for junk.
    pub snippet: String,
}

/// What is stored of a folder's sync so far.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct FolderState {
    pub uidvalidity: Option<i64>,
    /// The highest UID a sync has seen every message up to.
    pub watermark: i64,
    /// The HIGHESTMODSEQ the last whole read of its flags left.
    pub modseq: Option<i64>,
}

/// A stored row, as a read of the server's flags compares with it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct FlagRow {
    pub pk: i64,
    pub uid: u32,
    pub message_id: String,
    /// A queued move is taking it away.
    pub pending_removal: bool,
    pub flags: [i64; 5],
}

/// The rows the store keeps for a sync. Each call is one transaction: none stays open across a network call.
pub trait SyncStore: Send + Sync + fmt::Debug {
    fn folder_state(&self, account: &str, folder: &str) -> Result<FolderState, Fault>;
    /// The server renumbered the folder: its rows and its watermark go, so it syncs again from nothing. `now` is
    /// the Unix time a message's identity is gone from, once no copy of it is left.
    fn reset_folder(&self, account: &str, folder: &str, now: i64) -> Result<(), Fault>;
    fn set_uidvalidity(&self, account: &str, folder: &str, uidvalidity: u32) -> Result<(), Fault>;
    /// Store one message, its flags, its way out of a list, its recipients, its labels and its headers, and index
    /// it for search. Its pk, and whether threading has yet to hear of it.
    fn store_copy(&self, account: &SyncAccount, folder: &str, message: &Incoming) -> Result<(i64, bool), Fault>;
    /// Every UID up to `max_uid` is seen: the watermark rises to it, never falls.
    fn advance(&self, account: &str, folder: &str, max_uid: u32, when: &str) -> Result<(), Fault>;
    fn set_scan_state(&self, account: &str, folder: &str, state: &str) -> Result<(), Fault>;
    /// The rows to compare with the server's flags, those of `uids` or with None the `newest`, and the Message-IDs
    /// with a flag change still on its way to the server.
    fn flag_rows(
        &self,
        account: &str,
        folder: &str,
        uids: Option<&[u32]>,
        newest: usize,
    ) -> Result<(Vec<FlagRow>, HashSet<String>), Fault>;
    /// Each row's new flags, only where it still holds what was read; and the folder's mark. How many changed.
    fn write_flags(
        &self,
        account: &str,
        folder: &str,
        changed: &[(FlagRow, [i64; 5])],
        mark: Option<u64>,
    ) -> Result<usize, Fault>;
    /// What ends a sync, in one transaction: the folder's mark when a whole scan read every flag, the conversations
    /// of what it stored, mail arriving in a muted thread marked read, and the follow-ups new mail meets.
    fn settle(&self, account: &str, folder: &str, modseq: Option<u64>, stored: &[i64], when: &str)
    -> Result<(), Fault>;
}

/// What the Mailbox says happened, for whoever listens (`mailbox.domain.events`).
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum MailboxEvent {
    /// A read of the server's flags changed this many rows of a folder.
    FlagsChanged { account: String, folder: String, changed: usize },
    /// The worker carried a queued change to its server.
    Applied { id: i64, account: String },
    /// A change won't happen, and its message is as the server has it again.
    Restored { id: i64, account: String },
    /// What the page hears, on the topic the event stream names it by (`sse.publish`), and what it says.
    Page { topic: Topic, data: serde_json::Value },
}

pub trait Events: Send + Sync + fmt::Debug {
    fn publish(&self, event: MailboxEvent);
}

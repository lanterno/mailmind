//! What a sync needs of the world (`mailbox.application.ports` as `Ingest` uses them): the accounts' kinds, the
//! classifier Curation keeps, the store's rows and the events others hear. The store (`mm-store`) and the program
//! implement them. They stand apart from `ports` so the folder rail's and the sync's grow apart.

use std::collections::HashSet;
use std::fmt;

use mm_content::mime::Fields;
use mm_kernel::errors::Fault;
use mm_kernel::events::Topic;

use crate::fetch_ports::KeptBody;
use crate::sync_plan::Scan;

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
    /// Its body's text, where the sync read it (`sync_plan::reads_text`): the Inbox's but junk, a draft's, what Undo
    /// brought back; empty elsewhere.
    pub snippet: String,
    /// Its whole body, where the sync read all of it: an Inbox opening that held the whole message, or an invite
    /// fetched whole (Today & next reads invites from stored bodies only). Kept with the row, in its transaction, as
    /// opening it keeps one: its bytes, its parts, who it went to and its text. None leaves the body as it was.
    pub body: Option<KeptBody>,
}

/// What is stored of a folder's sync so far.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct FolderState {
    pub uidvalidity: Option<i64>,
    /// The highest UID a sync has seen every message up to.
    pub watermark: i64,
    /// The HIGHESTMODSEQ the last whole read of its flags left.
    pub modseq: Option<i64>,
    /// What its scans have read so far.
    pub scan: Scan,
}

/// Where a step of a sync leaves its folder, written in the transaction that stores the step's messages.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Place {
    /// The watermark rises to this, never falls; 0 leaves it.
    pub watermark: u32,
    /// What the folder's scans have read now, or None to leave it.
    pub leaves: Option<Scan>,
    /// What they must still have read, or nothing is stored: the folder was read afresh meanwhile.
    pub holds: Option<Scan>,
    /// The folder's last sync.
    pub when: String,
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
    /// Store one message, its flags, its way out of a list, its recipients, its labels, its headers and the body read
    /// whole, and index it for search. Its pk, and whether threading has yet to hear of it.
    fn store_copy(&self, account: &SyncAccount, folder: &str, message: &Incoming) -> Result<(i64, bool), Fault>;
    /// Store each of `messages` as `store_copy` does, and where the sync got to, all in one transaction: so a step is
    /// one write to the disk, and a sync that stops goes on from the last step stored. Each one's pk and whether
    /// threading has yet to hear of it, or None when the folder no longer holds what `place` says it must, and
    /// nothing was stored.
    fn store_step(
        &self,
        account: &SyncAccount,
        folder: &str,
        messages: &[Incoming],
        place: &Place,
    ) -> Result<Option<Vec<(i64, bool)>>, Fault>;
    /// Each folder whose first scan has older mail still to read: its account, its name and the lowest UID read.
    fn reading_older(&self) -> Result<Vec<(String, String, u32)>, Fault>;
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
    /// of what it stored, mail arriving in a muted thread marked read, and the follow-ups new mail meets. With `queue`
    /// false (a read-only process) those marks are made here alone, and nothing is queued for a server.
    fn settle(
        &self,
        account: &str,
        folder: &str,
        modseq: Option<u64>,
        stored: &[i64],
        when: &str,
        queue: bool,
    ) -> Result<(), Fault>;
    /// Whether any copy of the message these fields name has been seen before in the account: what storing it would
    /// find of its identity (`is_known`).
    fn is_known(&self, account: &str, fields: &Fields) -> Result<bool, Fault>;
    /// What a person's filters asked of the stored message `pk`, queued for its server where the row doesn't hold it
    /// already, and shown on the row at once. Whether anything was queued.
    fn queue_filtered(&self, pk: i64, wanted: &[(&'static str, bool)], when: &str) -> Result<bool, Fault>;
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

/// What a sync round asks of the other contexts (`Syncing`'s Attention and its kick): what is snoozed and due wakes
/// first, and new mail is triaged after, outside the round.
pub trait AroundSync: Send + Sync + fmt::Debug {
    /// Wake what is snoozed and due (Attention's `wake_due`). It says nothing back: a round goes on whatever it found.
    fn wake_due(&self);
    /// The round brought new mail: the job that triages it runs its next round now.
    fn new_mail(&self);
    /// A first scan left older mail to read: the round that reads it runs now. Whether anything here reads it: the
    /// served suites start no rounds.
    fn older_to_read(&self) -> bool {
        false
    }
    /// The account's server answers again after a sync of it failed, or a person asked to try it again: the changes
    /// waiting out a backoff for it go now, and its IDLE worker, resting after a connection that failed, connects now.
    fn server_back(&self, _account: &str) {}
    /// A connection to an account's server couldn't be made outside its sync: the sync, which measures whether the
    /// server answers, runs now, where this process runs one. The served suites start no rounds.
    fn sync_soon(&self) {}
}

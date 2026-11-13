//! What the Mailbox's use cases need of the world: the store, the mail servers and the accounts. The store
//! (`mm-store`) and the program implement them; the Mailbox knows no other context.

use std::fmt;

use mm_kernel::errors::Fault;

use crate::roles::Folder;

/// The accounts, as far as the Mailbox asks after them.
pub trait Accounts: Send + Sync + fmt::Debug {
    /// Whether an account the app may use is saved under exactly `account`.
    fn known(&self, account: &str) -> Result<bool, Fault>;
}

/// How many rows each view that isn't a folder holds.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct ViewCounts {
    pub snoozed: i64,
    pub muted: i64,
    pub waiting: i64,
    pub replylater: i64,
}

/// The mail MailMind keeps, as the Mailbox reads it.
pub trait StoredMail: Send + Sync + fmt::Debug {
    /// (folder, unseen, total) of what is stored for the account, each folder a sync has read among them.
    fn stored_folder_counts(&self, account: &str) -> Result<Vec<(String, Option<i64>, i64)>, Fault>;

    /// How many rows each view that isn't a folder holds across `accounts`, read together. Muted, waiting and
    /// Reply Later are Attention's, counted only where the thread still has mail.
    fn view_counts(&self, accounts: &[String]) -> Result<ViewCounts, Fault>;
}

/// Why a session couldn't do what was asked.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SessionError {
    /// The server couldn't be reached, or the connection to it was lost: asking again may help (`UNREACHABLE`).
    Unreachable(&'static str),
    /// The server refused the command, or something else went wrong.
    Failed(Fault),
}

impl SessionError {
    pub fn class(self) -> &'static str {
        match self {
            SessionError::Unreachable(class) => class,
            SessionError::Failed(fault) => fault.class,
        }
    }
}

/// A logged-in connection to an account's mail server (`mailbox.application.session.MailSession`), for reads.
pub trait MailSession: Send {
    /// The folders LIST names.
    fn folders(&mut self) -> Result<Vec<Folder>, SessionError>;
    /// A folder's (unseen, total), as STATUS gives them.
    fn status(&mut self, folder: &str) -> Result<(i64, i64), SessionError>;
    /// Log out, whatever the server says.
    fn logout(&mut self);
}

/// Where sessions come from.
pub trait MailSessions: Send + Sync + fmt::Debug {
    /// A logged-in session on `account`'s server.
    fn open(&self, account: &str) -> Result<Box<dyn MailSession>, SessionError>;
}

// ── The reads a person waits on: the lists, a conversation, the tray of changes ────────────────────────────────

/// A row as a query gave it: each column by its name, its value as SQLite held it (`kernel.row.Row`). A row
/// number may be among them, for the use case to tell rows apart by; none leaves the core.
pub type Row = serde_json::Map<String, serde_json::Value>;

/// A part of a route the Rust server can't do yet: its route answers it as one it doesn't implement.
pub use mm_kernel::errors::NOT_YET;

/// A page of a list, and how many rows the whole list holds.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Page {
    pub rows: Vec<Row>,
    pub total: i64,
}

/// What the lists read of the database (`mailbox.adapters.sqlite.lists`, and Attention's lists beside them). No list
/// holds a row on its way out (`pending_removal`), and a folder's or the Inboxes' leaves out what is snoozed or muted.
pub trait StoredLists: Send + Sync + fmt::Debug {
    /// A folder's rows, newest first, or one a conversation (its latest) if `grouped`.
    fn folder_page(&self, account: &str, folder: &str, grouped: bool, limit: i64, offset: i64) -> Result<Page, Fault>;
    /// The Inboxes of `accounts` as one list, as `folder_page` gives one.
    fn inboxes_page(&self, accounts: &[String], grouped: bool, limit: i64, offset: i64) -> Result<Page, Fault>;
    /// What is snoozed in `accounts`, soonest wake first.
    fn snoozed_page(&self, accounts: &[String], limit: i64, offset: i64) -> Result<Page, Fault>;
    /// Each muted thread of `accounts`, by its latest message, newest first.
    fn muted(&self, accounts: &[String]) -> Result<Vec<Row>, Fault>;
    /// Each thread of `accounts` with a follow-up due, by its latest message, the latest due first.
    fn waiting(&self, accounts: &[String]) -> Result<Vec<Row>, Fault>;
    /// Each thread of `accounts` pinned to reply to later, by its latest message, the newest pin first.
    fn reply_later(&self, accounts: &[String]) -> Result<Vec<Row>, Fault>;
    /// A conversation's messages in the account, oldest first, every copy of each.
    fn thread(&self, account: &str, thread_id: &str) -> Result<Vec<Row>, Fault>;
    /// Whether a scan has read the whole folder (`sync_state.scan_state`).
    fn scanned_whole(&self, account: &str, folder: &str) -> Result<bool, Fault>;
}

/// How a folder's sync went as it opened, for its list to say.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Opened {
    /// Synced, or caught up: the list is what is stored.
    Done,
    /// Its first scan is still reading: what is stored now, then `folder-synced`.
    Syncing,
    /// Its first scan failed.
    SyncFailed,
    /// A catch-up is still reading.
    CatchingUp,
}

/// The sync a folder's opening starts (`mailbox.application.folders.on_open`, `scanning_first`).
pub trait FolderSyncs: Send + Sync + fmt::Debug {
    /// A folder opens: its first scan, or a catch-up if one is due, waited for a moment.
    fn on_open(&self, account: &str, folder: &str) -> Result<Opened, Fault>;
    /// Whether a first scan of the folder is still to finish, starting one if none runs.
    fn scanning_first(&self, account: &str, folder: &str) -> Result<bool, Fault>;
}

/// The accounts as the Mailbox's reads name them.
pub trait AccountBook: Send + Sync + fmt::Debug {
    /// The accounts the app may use, by address, in the file's order.
    fn emails(&self) -> Result<Vec<String>, Fault>;
    /// What the page names each account by, disabled ones too.
    fn faces(&self) -> Result<Faces, Fault>;
}

/// Each account's alias, webmail and signature (the default where it has none), by address, and the default.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Faces {
    pub aliases: Vec<(String, Option<String>)>,
    pub webmail: Vec<(String, Option<String>)>,
    pub signatures: Vec<(String, String)>,
    pub signature_default: String,
}

/// What the digest reads of the database, together (`lists.digest`, `stored_accounts`, `stats`).
#[derive(Clone, Debug, Default, PartialEq)]
pub struct StoredDigest {
    /// The kept mail the AI found worth something, the most important first, then the newest.
    pub rows: Vec<Row>,
    /// Each account that has mail stored.
    pub accounts: Vec<String>,
    /// How many messages are stored, kept, summarized, verified, and kept with no summary yet.
    pub stats: [(&'static str, i64); 5],
}

pub trait DigestReads: Send + Sync + fmt::Debug {
    fn digest(&self, limit: i64) -> Result<StoredDigest, Fault>;
}

/// How the sync stands now (`Syncing.status`, each account's, and `Syncing.sync`, the whole one's).
pub trait SyncStatus: Send + Sync + fmt::Debug {
    fn status(&self) -> serde_json::Value;
    fn sync(&self) -> serde_json::Value;
}

/// How AI stands, with no call made (`assistant.status.report`).
pub trait AiStatus: Send + Sync + fmt::Debug {
    fn report(&self) -> Result<serde_json::Value, Fault>;
}

/// The changes that haven't reached a server, newest first, and how many there are in each state.
pub type OpenCommands = (Vec<Row>, Vec<(String, i64)>);

/// What the tray of changes reads (`mailbox.adapters.sqlite.command_log`).
pub trait StoredCommands: Send + Sync + fmt::Debug {
    /// The changes that haven't reached a server, newest first, and how many there are in each state.
    fn open_commands(&self, limit: i64) -> Result<OpenCommands, Fault>;
    /// The subject of the message a change is for, None when it has no row: its newest copy in `folder` with the
    /// Message-ID, else the newest with it anywhere, and with no Message-ID the row `pk` names.
    fn command_subject(
        &self,
        account: &str,
        folder: &str,
        message_id: Option<&str>,
        pk: Option<i64>,
    ) -> Result<Option<serde_json::Value>, Fault>;
}

/// A message as stored, to open: the row of a copy, its raw bytes and what is kept of its parts.
pub trait StoredBodies: Send + Sync + fmt::Debug {
    /// The row the copy `key`, `folder` and `uid` names now: at that uid, or without one the only copy in that
    /// folder (`copy_pk`).
    fn copy_pk(&self, key: i64, folder: &str, uid: Option<i64>) -> Result<Option<i64>, Fault>;
    /// The message's row, every column.
    fn message(&self, pk: i64) -> Result<Option<Row>, Fault>;
    /// Its raw RFC822 bytes, where they are stored.
    fn raw(&self, pk: i64) -> Result<Option<Vec<u8>>, Fault>;
    /// What is kept of one of its parts: its filename and content type.
    fn part(&self, pk: i64, part_id: &str) -> Result<Option<Row>, Fault>;
    /// Whether any of its parts is kept.
    fn has_parts(&self, pk: i64) -> Result<bool, Fault>;
    /// Keep what its parts are (`insert_attachments`).
    fn keep_parts(&self, pk: i64, parts: &[mm_content::mime::Attachment]) -> Result<(), Fault>;
}

/// A message's body from its server where it isn't stored (`Ingest.fetch_body`).
pub trait BodyFetch: Send + Sync + fmt::Debug {
    /// Its raw bytes, fetched and stored first where they aren't: empty when its server has nothing, None when its
    /// row has gone.
    fn fetch(&self, pk: i64) -> Result<Option<Vec<u8>>, Fault>;
    /// Fetch it in the background if the account's IMAP work can take it now: false when it is busy.
    fn prefetch(&self, pk: i64) -> Result<bool, Fault>;
}

/// What another context adds to a message as it opens (`Reader`).
pub trait Opening: Send + Sync + fmt::Debug {
    /// The invite it carries, and the part that holds it, each null when it carries none.
    fn invite_of(
        &self,
        pk: i64,
        parts: &[mm_content::mime::Attachment],
        raw: &[u8],
    ) -> Result<(serde_json::Value, serde_json::Value), Fault>;
    /// For a draft, its `draft_id`, `draft_mode` and `is_draft`; nothing for another message.
    fn draft_info(&self, row: &Row) -> Result<Row, Fault>;
    /// What is said of its sender beside their name, null for what can't be an address.
    fn sender_facts(&self, addr: Option<&str>) -> Result<serde_json::Value, Fault>;
}

/// One (account, folder)'s sync health as the store keeps it: its last sync that worked and its last that failed, the
/// failure's class and kind, never what it said.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SyncHealth {
    pub account: String,
    pub folder: String,
    pub last_ok_ts: Option<String>,
    pub last_error: Option<String>,
    pub last_error_ts: Option<String>,
    pub last_error_kind: Option<String>,
    pub sync_count: i64,
}

/// What each folder's syncs have come to, as the store keeps it.
pub trait StoredSyncs: Send + Sync + fmt::Debug {
    /// Every (account, folder)'s sync health, by account, then folder.
    fn sync_health(&self) -> Result<Vec<SyncHealth>, Fault>;
}

/// An account as the per-account counts name it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Listed {
    pub email: String,
    pub kind: String,
    pub alias: Option<String>,
}

/// Every account the app may use (`Accounts.accounts`).
pub trait AccountList: Send + Sync + fmt::Debug {
    /// The accounts, in the order the config names them, disabled ones left out.
    fn accounts(&self) -> Result<Vec<Listed>, Fault>;
}

/// What the classifier makes of each account's stored inbox (`Classifier.plan_stored` and `buckets`).
pub trait InboxPlans: Send + Sync + fmt::Debug {
    /// How each account's stored inbox falls into buckets, in the order the accounts are named: `account`, `kind`,
    /// `in_inbox`, `by_bucket`, `junk`, `keep`, `top_senders` and `error`, in that order.
    fn plans_stored(&self, accounts: &[Listed]) -> Result<Vec<serde_json::Map<String, serde_json::Value>>, Fault>;
    /// Every bucket and whether it is junk.
    fn buckets(&self) -> serde_json::Value;
}

/// A Gmail account's own labels as the store keeps them.
pub trait StoredLabels: Send + Sync + fmt::Debug {
    /// Each of `account`'s own labels (none of Gmail's system ones) and how many messages carry it, a message once
    /// however many copies it has, by name with case ignored.
    fn labels_of_account(&self, account: &str) -> Result<Vec<(String, i64)>, Fault>;
}

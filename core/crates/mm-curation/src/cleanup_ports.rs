//! What a sender's cleanup, its unsubscribe and a sweep need of the world (`mailmind.curation.application.ports`): the
//! stored mail they read and Curation's own tables, the Mailbox's filing and Undo, the mail servers, the two ways an
//! unsubscribe goes out, the sweep trail and the confirm tokens. The store and the program implement them.

use std::fmt;

use mm_kernel::errors::{Failure, Fault};
use serde_json::Value;

use crate::unsubscribe::{Attempt, Logged, UnsubWay};

/// One of a sender's INBOX copies, as a cleanup's sweep takes it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Target {
    pub pk: i64,
    pub key: Option<i64>,
    pub uid: Option<i64>,
    pub account: String,
    pub folder: String,
    pub subject: Option<String>,
    pub seen: Option<i64>,
}

/// One of a sender's stored messages, as the way out of its list is read from it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct UnsubRow {
    pub pk: i64,
    pub account: String,
    pub folder: String,
    pub uid: Option<i64>,
    pub list_unsub_raw: Option<String>,
    pub list_unsub_method: Option<String>,
}

/// A sender's stored rows, newest first; the accounts they were written to; and what the newest that can say says of
/// the way out.
pub type StoredWay = (Vec<UnsubRow>, Vec<String>, Option<UnsubWay>);

/// Stored mail by sender, and Curation's own tables (`unsub_log`, `sender_unsub`).
pub trait CleanupMail: Send + Sync + fmt::Debug {
    /// How many messages this sender's address has stored, and how many of them are unread.
    fn counts(&self, addr: &str) -> Result<(i64, i64), Fault>;
    /// The sender's INBOX copies, the newest first, at most `limit`, none on its way out.
    fn sweep_targets(&self, addr: &str, limit: usize) -> Result<Vec<Target>, Fault>;
    /// The sender's rows, newest first; the accounts they were written to, in that order; and what the newest that
    /// can say says of the way out (`unsubscribe::stored`), a body read keeping what it said on its row.
    fn unsub_stored(&self, addr: &str) -> Result<StoredWay, Fault>;
    /// Keep on the row `pk` what its headers said of the way out.
    fn remember_unsub(&self, pk: i64, parsed: &UnsubWay) -> Result<(), Fault>;
    /// Keep the way out past the sender's mail; a way that is none ends what was kept.
    fn keep_unsub(&self, addr: &str, info: &UnsubWay, accounts: &[String]) -> Result<(), Fault>;
    /// What was kept of the way out: its raw headers and the accounts.
    fn kept_unsub(&self, addr: &str) -> Result<Option<(String, Vec<String>)>, Fault>;
    /// The attempt kept under `key`.
    fn attempt(&self, key: &str) -> Result<Option<Logged>, Fault>;
    /// Keep an attempt under `key`, unless one is kept there already.
    fn record_attempt(&self, from_addr: &str, tried: &Attempt, key: &str, accounts: &[String]) -> Result<(), Fault>;
}

/// One message's unsubscribe headers, asked of its server: None where it can't say now.
pub trait ListHeaders: Send + Sync + fmt::Debug {
    fn read(&self, row: &UnsubRow) -> Option<Vec<u8>>;
}

/// The one request MailMind makes to an address a message named: RFC 8058's one-click POST.
pub trait SafeHttp: Send + Sync + fmt::Debug {
    /// Whether this server can make it.
    fn ready(&self) -> bool;
    /// Whether it went, and the status the host answered.
    fn one_click_post(&self, url: &str) -> (bool, Option<i64>);
}

/// A message MailMind sends for itself, from one of the person's accounts, through the outbox. `key` makes a second try
/// the same send.
pub trait SystemSend: Send + Sync + fmt::Debug {
    /// Whether this server can send one.
    fn ready(&self) -> bool;
    fn send(
        &self,
        account: &str,
        raw: &[u8],
        message_id: &str,
        to: &str,
        subject: &str,
        key: &str,
    ) -> Result<(), Fault>;
}

/// A copy as a request names it: its message's key, its folder and its uid there.
pub type NamedCopy = (i64, String, Option<i64>);

/// What a cleanup asks of the Mailbox: file a message through the command path, and keep what reverses a block.
pub trait CleanupFiling: Send + Sync + fmt::Debug {
    /// Trash the stored copy `pk`, as a click on it would, under the undo group: filing's answer.
    fn trash(&self, pk: i64, group: &str) -> Result<Value, Failure>;
    /// Trash these copies as filing marked rows does, under the undo group: its answer, which names each one filed.
    fn trash_many(&self, rows: &[NamedCopy], group: &str) -> Result<Value, Failure>;
    /// Put `entry` on the undo stack under `group`.
    fn undo_push(&self, entry: &Value, group: &str) -> Result<(), Fault>;
    /// Stored mail changed: what counts it may say so.
    fn stats_changed(&self);
}

/// A message a sweep trashes, as the sweep trail records it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Audited {
    pub uid: String,
    pub from: String,
    pub subject: String,
    pub bucket: String,
    pub reason: String,
}

/// The sweep trail, in the database: what a sweep trashed, and each sender's cleanup. A sweep's record is committed
/// when `swept` returns, before the move asks the server anything, so a crash still records it.
pub trait SweepTrail: Send + Sync + fmt::Debug {
    fn swept(&self, account: &str, rows: &[Audited]) -> Result<(), Fault>;
    fn cleaned_up(&self, from_addr: &str, token: &str, swept: usize, status: &str) -> Result<(), Fault>;
}

/// Confirm tokens (`perimeter.tokens`), under this run's secret: proof that this server showed a person this very
/// thing a moment ago.
pub trait ConfirmTokens: Send + Sync + fmt::Debug {
    /// A token for `purpose` and what was shown, minted at `now`, in whole seconds.
    fn mint(&self, purpose: &str, subject: &[Option<String>], now: i64) -> String;
    /// Whether a token in date was minted for this very thing.
    fn names(&self, token: &str, purpose: &str, subject: &[Option<String>]) -> bool;
}

/// Names no one can guess: `n` random bytes as hex.
pub trait Ids: Send + Sync + fmt::Debug {
    fn hex(&self, n: usize) -> String;
}

/// An account a sweep reads and moves the mail of.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SweptAccount {
    pub email: String,
    pub kind: String,
    /// Where its trash goes.
    pub trash: String,
}

/// Why a command on a server didn't do what was asked: a lost connection, which ends the work, or a refusal, which
/// leaves that message for the verification pass. Each by its class alone, never what the server said.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum LiveError {
    Lost(&'static str),
    Refused(&'static str),
}

impl LiveError {
    pub fn class(self) -> &'static str {
        match self {
            LiveError::Lost(class) | LiveError::Refused(class) => class,
        }
    }
}

/// A logged-in connection to an account's server, as far as a sweep reads and moves its INBOX through it.
pub trait LiveSession {
    /// Select a folder, read-only or to change it.
    fn select(&mut self, folder: &str, readonly: bool) -> Result<(), LiveError>;
    /// Every UID in the selected folder, in order.
    fn uids(&mut self) -> Result<Vec<u32>, LiveError>;
    /// These header fields of each message, in UID order: (uid, the fields as the server sent them).
    fn headers(&mut self, uids: &[u32], fields: &[&str]) -> Result<Vec<(u32, Vec<u8>)>, LiveError>;
    /// Move the UIDs to `dest`: one MOVE where the server offers it, else a checked copy, never a bulk EXPUNGE.
    fn move_to(&mut self, uids: &[u32], dest: &str) -> Result<(), LiveError>;
    /// Log out, or hang up where the connection failed.
    fn close(&mut self, failed: bool);
}

/// The accounts a sweep goes over, and a session on each one's server, on its gate.
pub trait LiveInboxes: Send + Sync + fmt::Debug {
    /// The accounts the app may use, in the file's order.
    fn accounts(&self) -> Result<Vec<SweptAccount>, Fault>;
    /// Run `job` on the account's gate, as interactive work, waiting its turn: what it answers, or a fault where the gate
    /// couldn't be had.
    fn on_gate(&self, account: &str, job: &mut dyn FnMut() -> Result<Value, Fault>) -> Result<Value, Fault>;
    /// A logged-in session on the account's server, or the class of why there is none.
    fn open(&self, account: &str) -> Result<Box<dyn LiveSession>, &'static str>;
}

/// What a sweep leaves of the stored mail it moved.
pub trait SweptRows: Send + Sync + fmt::Debug {
    /// Forget the stored INBOX rows of what moved, by Message-ID where it has one, else by UID; a straggler stays.
    fn forget_moved(&self, account: &str, moved: &[(String, String)], stragglers: &[String]) -> Result<(), Fault>;
}

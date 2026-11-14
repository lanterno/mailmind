//! What Curation's use cases need of the world (`mailmind.curation.application.ports`): the rule book, the stored mail
//! by sender and in each inbox, the accounts, the audit trail, and what it asks of the Mailbox and of Discovery. The store
//! (`mm-store`) and the program implement them; Curation knows no other context.

use std::collections::{HashMap, HashSet};
use std::fmt;

use mm_kernel::errors::{Failure, Fault};
use serde_json::Value;

use crate::cleanup_ports::NamedCopy;
use crate::rulebook::Rules;

/// The rule book's one reader and writer.
pub trait RuleBook: Send + Sync + fmt::Debug {
    /// The rules as they stand.
    fn load(&self) -> Result<Rules, Fault>;
    /// One change to the rules, in one write block: `apply` changes them, and if it fails nothing is saved.
    fn change(&self, apply: &mut dyn FnMut(&mut Rules) -> Result<(), Failure>) -> Result<(), Failure>;
}

/// A sender in an account's Inbox, as the Screener lists one.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct InboxSender {
    pub addr: String,
    pub name: Option<String>,
    pub n: i64,
    pub iso: Option<String>,
}

/// A stored message as the classifier read it, and what it said.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Classified {
    pub pk: i64,
    pub from_addr: Option<String>,
    pub from_name: Option<String>,
    pub subject: Option<String>,
    pub bucket: Option<String>,
    pub reason: Option<String>,
    pub list_unsub_method: Option<String>,
}

/// A new classification for a stored message.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Reclassified {
    pub pk: i64,
    pub bucket: &'static str,
    pub confidence: &'static str,
    pub reason: String,
    pub is_junk: bool,
}

/// A sender of mail the classifier put in a noisy bucket, by the numbers a cleanup ranks it on.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct NoisySender {
    pub addr: String,
    pub name: Option<String>,
    pub n: i64,
    pub unseen: i64,
    pub last_ts: Option<i64>,
    pub last_iso: Option<String>,
    pub bucket: Option<String>,
}

/// Stored mail by sender.
pub trait SenderMail: Send + Sync + fmt::Debug {
    /// Each sender in this account's Inbox, the latest first, junk and what is on its way out left out.
    fn inbox_senders(&self, account: &str) -> Result<Vec<InboxSender>, Fault>;
    /// What the classifier read and said of each stored message from `token`, an address, or a domain and its
    /// sub-domains.
    fn classified_from(&self, token: &str) -> Result<Vec<Classified>, Fault>;
    /// Put each new classification in place, in one write block.
    fn set_classifications(&self, changes: &[Reclassified]) -> Result<(), Fault>;
    /// Each sender, by lowered address, of at least `min_count` stored messages in `buckets`, every account's and
    /// folder's.
    fn noisy_senders(&self, buckets: &[&str], min_count: i64) -> Result<Vec<NoisySender>, Fault>;
    /// How many messages from each of `addrs` each account holds: (account, count) by sender.
    fn accounts_by_sender(&self, addrs: &[String]) -> Result<HashMap<String, Vec<(String, i64)>>, Fault>;
}

/// The accounts the app may use, by address.
pub trait Mailboxes: Send + Sync + fmt::Debug {
    fn known(&self) -> Result<HashSet<String>, Fault>;
}

/// What Curation asks of the Mailbox (`FilingCommands`): a change that reaches a mail server, the undo stack, and the
/// counts it keeps.
pub trait Filing: Send + Sync + fmt::Debug {
    /// Trash the stored copy `pk` now, with nothing for Undo to reverse: whether it went or will go.
    fn trash_now(&self, pk: i64) -> Result<bool, Fault>;
    /// Trash the stored copy `pk`, as a click on it would, under the undo group: filing's answer.
    fn trash(&self, pk: i64, group: &str) -> Result<Value, Failure>;
    /// Trash these copies as filing marked rows does, under the undo group: its answer, which names each one filed.
    fn trash_many(&self, rows: &[NamedCopy], group: &str) -> Result<Value, Failure>;
    /// Put `entry` on the undo stack under `group`.
    fn undo_push(&self, entry: &Value, group: &str) -> Result<(), Fault>;
    /// What is stored was classified again: the counts the page shows have moved.
    fn stats_changed(&self);
    /// `moved` messages left the account's INBOX on its server behind the store's back: the counts and the rail move,
    /// and open lists hear it as a reconcile's prune.
    fn folders_changed(&self, account: &str, moved: usize) -> Result<(), Fault>;
}

/// Whether a search can be judged of mail as it arrives: Discovery's to say.
pub trait Conditions: Send + Sync + fmt::Debug {
    /// Why this query can't be a filter, in words for a person, or None.
    fn refusal(&self, q: &str) -> Option<String>;
}

/// The audit trails, as the page reads them.
pub trait AuditTrail: Send + Sync + fmt::Debug {
    /// The newest `n` records of every trail, newest first, and whether there are older ones.
    fn newest(&self, n: i64) -> Result<(Vec<Value>, bool), Fault>;
}

/// Where a new filter's id comes from: `f-` and 8 random bytes in URL-safe base64, as `secrets.token_urlsafe(8)`.
pub trait FilterIds: Send + Sync + fmt::Debug {
    fn filter_id(&self) -> Result<String, Fault>;
}

/// A message stored in an inbox, as much of it as the classifier reads again (`stored_inbox`'s row).
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Stored {
    pub from_addr: Option<String>,
    pub from_name: Option<String>,
    pub subject: Option<String>,
    pub bucket: Option<String>,
    pub list_unsub_raw: Option<String>,
}

/// The mail stored in each account's inbox.
pub trait StoredInbox: Send + Sync + fmt::Debug {
    /// What each account's stored INBOX holds, mail on its way out left out, in the order the accounts are named. One
    /// read of the store serves them all.
    fn stored_inboxes(&self, accounts: &[&str]) -> Result<Vec<Vec<Stored>>, Fault>;
}

/// The rule book as it is stored, for the sweep plan to read.
pub trait RulesDocument: Send + Sync + fmt::Debug {
    /// The rules document as written, or None when no rule has been made.
    fn rules(&self) -> Result<Option<Value>, Fault>;
}

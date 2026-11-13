//! What Discovery's use cases need of the world (`mailmind.discovery.application.ports`): the stored mail as a query
//! finds it, who it came from and went to, the saved views, the accounts, the rule book and the attachments stored. The store (`mm-store`)
//! and the program implement them; Discovery knows no other context.

use std::collections::{BTreeMap, HashSet};
use std::fmt;

use mm_kernel::errors::Fault;
use serde_json::Value;

use crate::query::QuerySpec;

/// A stored copy a search found, with the AI's verdict on its message where there is one.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Found {
    pub pk: i64,
    pub key: Option<i64>,
    pub uid: i64,
    pub account: String,
    pub folder: String,
    pub from_addr: Option<String>,
    pub from_name: Option<String>,
    pub subject: Option<String>,
    pub date_iso: Option<String>,
    pub snippet: Option<String>,
    pub seen: bool,
    pub flagged: bool,
    pub has_attach: bool,
    pub is_junk: bool,
    pub bucket: Option<String>,
    pub thread_id: Option<String>,
    pub importance: Option<i64>,
    pub category: Option<String>,
    pub action: Option<String>,
}

/// Which folder plays which part: by name alone, or by what the last LIST said and else by name.
pub trait FolderRoles: Send + Sync + fmt::Debug {
    fn role(&self, account: &str, folder: &str) -> String;
    fn role_by_name(&self, folder: &str) -> String;
}

/// Stored mail as a query finds it (`adapters.sqlite.StoredMail.search`).
pub trait SearchIndex: Send + Sync + fmt::Debug {
    /// What `spec` finds, a message once however many folders hold it, up to one more than `limit` after `offset`:
    /// None for a query the index rejects. `in_all` says which folders play All Mail, whose copy shows last.
    fn search(
        &self,
        spec: &QuerySpec,
        account: Option<&str>,
        limit: i64,
        offset: i64,
        thread: Option<&str>,
        roles: &dyn FolderRoles,
    ) -> Result<Option<Vec<Found>>, Fault>;
}

/// A sender, as autocomplete offers one.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Sender {
    pub addr: Option<String>,
    pub name: Option<String>,
    pub n: i64,
}

/// What the stored mail says of one sender, a message counted once however many folders hold it.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Tally {
    pub n: i64,
    pub last: Option<i64>,
    pub first: Option<i64>,
    pub name: Option<String>,
    pub replied: i64,
}

/// One of a sender's recent messages, as their card lists it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Recent {
    pub key: Option<i64>,
    pub uid: i64,
    pub folder: String,
    pub account: String,
    pub subject: Option<String>,
    pub thread_id: Option<String>,
    pub date_iso: Option<String>,
    pub seen: bool,
}

/// A row of who stored mail went to: the recipient, where the copy is, whether it is junk and who sent it, and how
/// many such copies there are (1 where not counted).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Recipient {
    pub addr: String,
    pub account: String,
    pub folder: String,
    pub is_junk: bool,
    pub sender: Option<String>,
    pub n: i64,
}

/// What the stored mail says about the addresses asked after (`known.Facts`). Holds no subject and no date.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Facts {
    /// The accounts that hold mail.
    pub accounts: Vec<String>,
    /// (address, folder): non-junk mail from one of them.
    pub senders: Vec<(String, String)>,
    /// (account, folder): every folder synced.
    pub synced: Vec<(String, String)>,
    /// Where a draft is kept.
    pub draft_folders: HashSet<String>,
    /// Mail to one of them.
    pub copies: Vec<Recipient>,
    /// What the outbox delivered, as JSON lists.
    pub envelopes: Vec<Option<String>>,
    /// Too little history to call anyone new.
    pub thin: bool,
}

/// Stored mail by who it came from and went to (`ContactMail`).
pub trait ContactMail: Send + Sync + fmt::Debug {
    fn senders_like(&self, needle: &str) -> Result<Vec<Sender>, Fault>;
    /// (domain, count) of the domains people have written from that hold `needle`, the commonest first.
    fn sender_domains_like(&self, needle: &str) -> Result<Vec<(Option<String>, i64)>, Fault>;
    fn card(&self, addr: &str) -> Result<(Tally, Vec<String>, Vec<Recent>), Fault>;
    fn known_facts(&self, forms: &[String], min_history: i64) -> Result<Facts, Fault>;
    fn written_copies(&self, own: &[String], sent: &[String]) -> Result<Vec<Recipient>, Fault>;
}

/// Where a person's saved views are kept: whatever was kept, as JSON parses it, which may be anything.
pub trait ViewsStore: Send + Sync + fmt::Debug {
    fn read(&self) -> Result<Value, Fault>;
    /// Read what is kept, change it with `change`, and write what it gives back, if it gives anything, in one write
    /// block, so two changes at once lose neither.
    fn change(&self, change: &mut dyn FnMut(Value) -> Option<Value>) -> Result<(), Fault>;
}

/// The accounts as the rest of the app names them.
pub trait AccountNames: Send + Sync + fmt::Debug {
    fn emails(&self) -> Result<Vec<String>, Fault>;
    /// Each account's alias, None where it has none.
    fn aliases(&self) -> Result<BTreeMap<String, Option<String>>, Fault>;
}

/// Whom the rule book keeps. A domain rule isn't knowing a person, so only senders count.
pub trait KeptSenders: Send + Sync + fmt::Debug {
    fn kept(&self) -> Result<HashSet<String>, Fault>;
}

/// A stored row, by column name, each value as SQLite holds it.
pub type Row = serde_json::Map<String, serde_json::Value>;

/// The attachments stored of the mail of `accounts`: each that isn't inline and has a name, of a copy not on its
/// way out, the newest message first and then in the order they were stored.
pub trait StoredDocuments: Send + Sync + fmt::Debug {
    fn attachments(&self, accounts: &[String]) -> Result<Vec<Row>, Fault>;
}

/// What the mail says of one sender, by their lowercase address (`tally`): `n` messages, `replied` of them
/// answered, the `first` and `last` times one came, and the `name` they wrote under.
pub trait StoredTally: Send + Sync + fmt::Debug {
    fn tally(&self, address: &str) -> Result<Row, Fault>;
}

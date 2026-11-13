//! What Curation needs of the world, as traits the store implements (`mailmind.curation.application.ports`).

use std::fmt;

use mm_kernel::errors::Fault;
use serde_json::Value;

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
    /// What `account`'s stored INBOX holds, mail on its way out left out.
    fn stored_inbox(&self, account: &str) -> Result<Vec<Stored>, Fault>;
}

/// The rule book as it is stored.
pub trait RuleBook: Send + Sync + fmt::Debug {
    /// The rules document as written, or None when no rule has been made.
    fn rules(&self) -> Result<Option<Value>, Fault>;
}

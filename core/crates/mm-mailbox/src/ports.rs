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

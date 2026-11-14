//! What warming folders beyond the Inbox needs of the world (`mailbox.application.warm`'s ports): each account, as
//! the Directory knows it, and a folder's sync, bounded to its newest mail, which `Ingest` is.

use std::fmt;

use mm_kernel::errors::Fault;

use crate::ingest::{Ingest, SyncError};

/// What warming knows of an account.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct WarmAccount {
    pub email: String,
    /// Its entry in the file says `"warm_folders": true`, which counts while MAILMIND_WARM_FOLDERS is unset.
    pub asks: bool,
    /// Its archive drops a label and leaves the message in All Mail (Gmail), so All Mail holds the Inbox.
    pub archive_is_label_removal: bool,
}

pub trait WarmAccounts: Send + Sync + fmt::Debug {
    /// Every account the app may use, in the file's order.
    fn warm_accounts(&self) -> Result<Vec<WarmAccount>, Fault>;
}

/// A folder's sync above its watermark, a first one bounded to the newest `recent` messages: how many it kept that
/// aren't junk.
pub trait WarmSync: Send + Sync + fmt::Debug {
    fn sync_recent(&self, account: &str, folder: &str, recent: usize) -> Result<usize, SyncError>;
}

impl WarmSync for Ingest {
    fn sync_recent(&self, account: &str, folder: &str, recent: usize) -> Result<usize, SyncError> {
        self.sync_folder(account, folder, false, Some(recent)).map(|(kept, _, _)| kept)
    }
}

//! What a folder's opening needs of the world beyond the store and the gates: the sync of one folder, and a thread
//! to run it on that the program counts, so a stop waits for it. The sync (`Ingest`) and the program implement them.

use std::fmt;

use crate::ingest::{Ingest, SyncError, Synced};

/// One folder brought up to date: its first scan (`full`), or a catch-up of what came above its watermark and the
/// newest flags again. How many messages it stored, kept and junk, and whether a first scan left older mail to read.
pub trait FolderSync: Send + Sync + fmt::Debug {
    fn sync_folder(&self, account: &str, folder: &str, full: bool) -> Result<Synced, SyncError>;
}

impl FolderSync for Ingest {
    fn sync_folder(&self, account: &str, folder: &str, full: bool) -> Result<Synced, SyncError> {
        Ingest::sync_folder(self, account, folder, full, None)
    }
}

/// The background's read of a first scan's older mail (`Syncing::read_older`): a folder's first scan as it opened
/// that left some sets its round going now, rather than at its next turn, minutes on.
pub trait OlderMail: Send + Sync + fmt::Debug {
    fn left_some(&self);
}

/// The Inbox's sync, which the background keeps (`Syncing`): a first look at an Inbox never read joins it, rather
/// than read the Inbox a second time beside it.
pub trait InboxSync: Send + Sync + fmt::Debug {
    /// The account's sync, now: the one running, waited for, or one started here. Whether it ended well.
    fn sync_inbox(&self, account: &str) -> bool;
}

/// Work a request sets going and answers before it is done, on a thread of its own.
pub trait Background: Send + Sync + fmt::Debug {
    fn spawn(&self, job: Box<dyn FnOnce() + Send>);
}

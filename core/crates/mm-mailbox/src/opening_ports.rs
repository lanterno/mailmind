//! What a folder's opening needs of the world beyond the store and the gates: the sync of one folder, and a thread
//! to run it on that the program counts, so a stop waits for it. The sync (`Ingest`) and the program implement them.

use std::fmt;

use crate::ingest::{Ingest, SyncError};

/// One folder brought up to date: its first scan (`full`), or a catch-up of what came above its watermark and the
/// newest flags again. How many messages it stored, kept and junk.
pub trait FolderSync: Send + Sync + fmt::Debug {
    fn sync_folder(&self, account: &str, folder: &str, full: bool) -> Result<(usize, usize), SyncError>;
}

impl FolderSync for Ingest {
    fn sync_folder(&self, account: &str, folder: &str, full: bool) -> Result<(usize, usize), SyncError> {
        Ingest::sync_folder(self, account, folder, full, None).map(|synced| (synced.kept, synced.junk))
    }
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

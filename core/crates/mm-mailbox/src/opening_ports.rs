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
        Ingest::sync_folder(self, account, folder, full, None).map(|(kept, junk, _)| (kept, junk))
    }
}

/// Work a request sets going and answers before it is done, on a thread of its own.
pub trait Background: Send + Sync + fmt::Debug {
    fn spawn(&self, job: Box<dyn FnOnce() + Send>);
}

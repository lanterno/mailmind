//! What a folder's opening does on the Rust server until the sync is ported (`folders.on_open`): it answers from
//! what is stored, and starts no sync.
//!
//! A folder a scan has read whole is its list, as after a catch-up that found nothing new. One its server doesn't
//! have, or can't be asked about, fails its first scan, as the scan's SELECT would. Any other's first scan isn't on
//! the Rust server yet.

use std::sync::Arc;

use mm_kernel::errors::Fault;

use crate::ports::{FolderSyncs, MailSessions, NOT_YET, Opened, StoredLists};

#[derive(Debug)]
pub struct StoredOnly {
    stored: Arc<dyn StoredLists>,
    sessions: Arc<dyn MailSessions>,
}

impl StoredOnly {
    pub fn new(stored: Arc<dyn StoredLists>, sessions: Arc<dyn MailSessions>) -> StoredOnly {
        StoredOnly { stored, sessions }
    }
}

impl FolderSyncs for StoredOnly {
    fn on_open(&self, account: &str, folder: &str) -> Result<Opened, Fault> {
        if self.stored.scanned_whole(account, folder)? {
            return Ok(Opened::Done);
        }
        let Ok(mut session) = self.sessions.open(account) else {
            return Ok(Opened::SyncFailed);
        };
        let there = session.status(folder).is_ok();
        session.logout();
        if there { Err(NOT_YET) } else { Ok(Opened::SyncFailed) }
    }

    fn scanning_first(&self, account: &str, folder: &str) -> Result<bool, Fault> {
        if self.stored.scanned_whole(account, folder)? { Ok(false) } else { Err(NOT_YET) }
    }
}

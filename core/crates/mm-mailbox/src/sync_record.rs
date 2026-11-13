//! What each account's syncs have come to, for Accounts to read as its health (`mailbox.application.syncing
//! .sync_record`): its INBOX's last sync that worked and last that failed, and how its sync stands now. A pure read:
//! no server is asked.

use std::sync::Arc;

use mm_kernel::errors::Fault;

use crate::ports::{StoredSyncs, SyncHealth};

/// An account's INBOX syncs, and how its sync stands now: "ok", "syncing", "error", or "" when none has run since the
/// start.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SyncRecord {
    pub health: SyncHealth,
    pub live: &'static str,
}

#[derive(Debug)]
pub struct SyncRecords {
    store: Arc<dyn StoredSyncs>,
}

impl SyncRecords {
    pub fn new(store: Arc<dyn StoredSyncs>) -> SyncRecords {
        SyncRecords { store }
    }

    /// Each account's INBOX record, by account. The Rust server runs no sync of its own yet, so none has run since
    /// the start: each stands "".
    pub fn sync_record(&self) -> Result<Vec<SyncRecord>, Fault> {
        Ok(self
            .store
            .sync_health()?
            .into_iter()
            .filter(|h| h.folder.is_empty() || h.folder == "INBOX")
            .map(|health| SyncRecord { health, live: "" })
            .collect())
    }
}

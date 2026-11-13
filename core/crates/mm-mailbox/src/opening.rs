//! What a folder's opening does on the Rust server until the sync is ported (`folders.on_open`): it answers from
//! what is stored, and starts no sync.
//!
//! A folder a scan has read whole is its list, as after a catch-up that found nothing new. One its server doesn't
//! have, or can't be asked about, fails its first scan, as the scan's SELECT would. Any other's first scan isn't on
//! the Rust server yet.

use std::sync::Arc;

use mm_kernel::errors::Fault;

use crate::ports::{BodyFetch, FolderSyncs, MailSessions, NOT_YET, Opened, StoredBodies, StoredLists};

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

/// What fetching a body does on the Rust server until the IMAP fetch is ported (`Ingest.fetch_body`): a body that
/// is stored is read, and what is kept of its parts comes back where a parsing change took it away. One that isn't
/// stored isn't on the Rust server yet.
#[derive(Debug)]
pub struct StoredBody {
    stored: Arc<dyn StoredBodies>,
}

impl StoredBody {
    pub fn new(stored: Arc<dyn StoredBodies>) -> StoredBody {
        StoredBody { stored }
    }
}

impl BodyFetch for StoredBody {
    fn fetch(&self, pk: i64) -> Result<Option<Vec<u8>>, Fault> {
        let Some(row) = self.stored.message(pk)? else { return Ok(None) };
        let fetched = row.get("body_fetched").and_then(serde_json::Value::as_i64).is_some_and(|n| n != 0);
        let raw = if fetched { self.stored.raw(pk)?.unwrap_or_default() } else { Vec::new() };
        if raw.is_empty() {
            return Err(NOT_YET);
        }
        let parts = mm_content::mime::parse(&raw).attachments;
        if !parts.is_empty() && !self.stored.has_parts(pk)? {
            self.stored.keep_parts(pk, &parts)?;
        }
        Ok(Some(raw))
    }

    fn prefetch(&self, _pk: i64) -> Result<bool, Fault> {
        Err(NOT_YET)
    }
}

//! What Curation reads of the database for a sweep's stored plan (`mm_curation::ports`): each account's stored
//! inbox (`curation.adapters.sqlite.stored_inbox`), the same with where each message is once a first scan has read it
//! whole (`ScannedInbox`, a sweep's preview), and the rule book, the `rules` row of `documents`
//! (`curation.adapters.rules_document`).

use mm_curation::cleanup_ports::{InboxCopy, ScannedInbox};
use mm_curation::ports::{RulesDocument, Stored, StoredInbox};
use mm_kernel::errors::Fault;
use mm_mailbox::sync_plan::Scan;
use rusqlite::{OptionalExtension, Row};
use serde_json::Value;

use crate::db::Database;

/// A stored message as the classifier reads it again, from `from_addr, from_name, subject, bucket, list_unsub_raw,
/// seen, flagged` at `at` on.
fn stored_at(r: &Row<'_>, at: usize) -> rusqlite::Result<Stored> {
    Ok(Stored {
        from_addr: r.get(at)?,
        from_name: r.get(at + 1)?,
        subject: r.get(at + 2)?,
        bucket: r.get(at + 3)?,
        list_unsub_raw: r.get(at + 4)?,
        seen: r.get::<_, Option<i64>>(at + 5)?.is_some_and(|f| f != 0),
        flagged: r.get::<_, Option<i64>>(at + 6)?.is_some_and(|f| f != 0),
    })
}

impl StoredInbox for Database {
    fn stored_inboxes(&self, accounts: &[&str]) -> Result<Vec<Vec<Stored>>, Fault> {
        self.read(|c| {
            let mut inbox = c.prepare_cached(
                "SELECT from_addr, from_name, subject, bucket, list_unsub_raw, seen, flagged FROM messages \
                 WHERE account=? AND folder='INBOX' AND deleted=0 AND pending_removal IS NULL",
            )?;
            accounts.iter().map(|account| inbox.query_map([account], |r| stored_at(r, 0))?.collect()).collect()
        })
    }
}

impl ScannedInbox for Database {
    fn scanned(&self, account: &str) -> Result<Option<Vec<InboxCopy>>, Fault> {
        self.read(|c| {
            let state: Option<String> = c
                .prepare_cached("SELECT scan_state FROM sync_state WHERE account=? AND folder='INBOX'")?
                .query_row([account], |r| r.get(0))
                .optional()?;
            if Scan::read(state.as_deref()) != Scan::Whole {
                return Ok(None);
            }
            c.prepare_cached(
                "SELECT uid, message_id, from_addr, from_name, subject, bucket, list_unsub_raw, seen, flagged \
                 FROM messages WHERE account=? AND folder='INBOX' AND deleted=0 AND pending_removal IS NULL",
            )?
            .query_map([account], |r| {
                Ok(InboxCopy { uid: r.get(0)?, message_id: r.get(1)?, stored: stored_at(r, 2)? })
            })?
            .collect::<Result<Vec<_>, _>>()
            .map(Some)
        })
    }
}

impl RulesDocument for Database {
    fn rules(&self) -> Result<Option<Value>, Fault> {
        let body: Option<String> = self.read(|c| crate::blocks::document_text(c, "rules"))?;
        body.map(|body| serde_json::from_str(&body).map_err(|_| Fault::new("MalformedRules"))).transpose()
    }
}

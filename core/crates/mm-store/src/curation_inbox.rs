//! What Curation reads of the database for a sweep's stored plan (`mm_curation::ports`): each account's stored
//! inbox (`curation.adapters.sqlite.stored_inbox`), and the rule book, the `rules` row of `documents`
//! (`curation.adapters.rules_document`).

use mm_curation::ports::{RuleBook, Stored, StoredInbox};
use mm_kernel::errors::Fault;
use rusqlite::OptionalExtension;
use serde_json::Value;

use crate::db::Database;

impl StoredInbox for Database {
    fn stored_inbox(&self, account: &str) -> Result<Vec<Stored>, Fault> {
        self.read(|c| {
            c.prepare_cached(
                "SELECT from_addr, from_name, subject, bucket, list_unsub_raw FROM messages \
                 WHERE account=? AND folder='INBOX' AND deleted=0 AND pending_removal IS NULL",
            )?
            .query_map([account], |r| {
                Ok(Stored {
                    from_addr: r.get(0)?,
                    from_name: r.get(1)?,
                    subject: r.get(2)?,
                    bucket: r.get(3)?,
                    list_unsub_raw: r.get(4)?,
                })
            })?
            .collect()
        })
    }
}

impl RuleBook for Database {
    fn rules(&self) -> Result<Option<Value>, Fault> {
        let body: Option<String> =
            self.read(|c| c.query_row("SELECT body FROM documents WHERE name='rules'", [], |r| r.get(0)).optional())?;
        body.map(|body| serde_json::from_str(&body).map_err(|_| Fault::new("MalformedRules"))).transpose()
    }
}

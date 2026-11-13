//! What Curation reads of the database for a sweep's stored plan (`mm_curation::ports`): each account's stored
//! inbox (`curation.adapters.sqlite.stored_inbox`), and the rule book, the `rules` row of `documents`
//! (`curation.adapters.rules_document`).

use mm_curation::ports::{RulesDocument, Stored, StoredInbox};
use mm_kernel::errors::Fault;
use serde_json::Value;

use crate::db::Database;

impl StoredInbox for Database {
    fn stored_inboxes(&self, accounts: &[&str]) -> Result<Vec<Vec<Stored>>, Fault> {
        self.read(|c| {
            let mut inbox = c.prepare_cached(
                "SELECT from_addr, from_name, subject, bucket, list_unsub_raw FROM messages \
                 WHERE account=? AND folder='INBOX' AND deleted=0 AND pending_removal IS NULL",
            )?;
            accounts
                .iter()
                .map(|account| {
                    inbox
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
                .collect()
        })
    }
}

impl RulesDocument for Database {
    fn rules(&self) -> Result<Option<Value>, Fault> {
        let body: Option<String> = self.read(|c| crate::blocks::document_text(c, "rules"))?;
        body.map(|body| serde_json::from_str(&body).map_err(|_| Fault::new("MalformedRules"))).transpose()
    }
}

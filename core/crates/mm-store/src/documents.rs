//! What the Document Hub reads of the database (`discovery.adapters.sqlite`'s `attachments`): the attachments
//! stored, with what the list shows of the message each is in.

use mm_discovery::ports::{Row, StoredDocuments};
use mm_kernel::errors::Fault;
use rusqlite::params_from_iter;

use crate::db::Database;
use crate::mailbox::lists::rows_of;

impl StoredDocuments for Database {
    fn attachments(&self, accounts: &[String]) -> Result<Vec<Row>, Fault> {
        let marks = vec!["?"; accounts.len()].join(",");
        // One order, or a page could repeat or skip a file.
        let sql = format!(
            "SELECT a.message_pk pk, m.message_key, m.uid, a.part_id, a.filename, a.content_type, a.size, \
             m.message_id, m.account, m.from_name, m.from_addr, m.subject, m.date_iso, m.ts, m.folder \
             FROM attachments a JOIN messages m ON m.pk=a.message_pk \
             WHERE a.is_inline=0 AND a.filename IS NOT NULL AND a.filename!='' AND m.pending_removal IS NULL \
             AND m.account IN ({marks}) ORDER BY m.ts DESC, a.id"
        );
        self.read(|c| rows_of(c, &sql, params_from_iter(accounts)))
    }
}

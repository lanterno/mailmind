//! The Mailbox's labels (`mm_mailbox::ports::StoredLabels`): what `message_labels` holds of an account's own labels
//! (`mailbox.adapters.sqlite.contents.labels_of_account`).

use mm_kernel::errors::Fault;
use mm_mailbox::ports::StoredLabels;

use crate::db::Database;

impl StoredLabels for Database {
    fn labels_of_account(&self, account: &str) -> Result<Vec<(String, i64)>, Fault> {
        self.read(|c| {
            c.prepare_cached(
                r"SELECT l.label AS name, count(DISTINCT m.message_key) AS count
                  FROM message_labels l JOIN messages m ON m.pk=l.message_pk
                  WHERE m.account=? AND m.pending_removal IS NULL AND substr(l.label,1,1) <> '\'
                  GROUP BY l.label ORDER BY l.label COLLATE NOCASE",
            )?
            .query_map([account], |r| Ok((r.get(0)?, r.get(1)?)))?
            .collect()
        })
    }
}

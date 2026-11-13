//! What the digest reads (`mailbox.adapters.sqlite.lists.digest`, `stored_accounts`, `stats`): the kept mail the AI
//! found worth something, none on its way out, snoozed or muted, and what is stored all told.

use mm_kernel::errors::Fault;
use mm_mailbox::ports::{DigestReads, StoredDigest};

use super::lists::rows_of;
use crate::db::Database;

const DIGEST: &str = "SELECT m.*, s.summary, s.importance, s.category, s.action
     FROM messages m JOIN assessments s ON s.message_key=m.message_key
     WHERE m.is_junk = 0
       AND NOT EXISTS (SELECT 1 FROM snoozes z WHERE z.message_key=m.message_key AND z.folder=m.folder)
       AND m.pending_removal IS NULL AND s.importance >= 1
       AND NOT EXISTS (SELECT 1 FROM muted mu WHERE mu.account=m.account AND mu.thread_id=m.thread_id)
     ORDER BY s.importance DESC, m.ts DESC LIMIT ?";

const STATS: [(&str, &str); 5] = [
    ("total", "SELECT COUNT(*) FROM messages"),
    ("kept", "SELECT COUNT(*) FROM messages WHERE is_junk=0"),
    ("summarized", "SELECT COUNT(*) FROM messages m JOIN assessments s ON s.message_key=m.message_key"),
    ("verified", "SELECT COUNT(*) FROM messages WHERE verified=1"),
    (
        "needs",
        "SELECT COUNT(*) FROM messages m LEFT JOIN assessments s ON s.message_key=m.message_key \
         WHERE m.is_junk=0 AND s.message_key IS NULL",
    ),
];

impl DigestReads for Database {
    fn digest(&self, limit: i64) -> Result<StoredDigest, Fault> {
        self.read(|c| {
            let rows = rows_of(c, DIGEST, [limit])?;
            let accounts = c
                .prepare_cached("SELECT DISTINCT account FROM messages ORDER BY account")?
                .query_map([], |r| r.get(0))?
                .collect::<Result<_, _>>()?;
            let mut stats = [("", 0); 5];
            for (slot, (name, sql)) in stats.iter_mut().zip(STATS) {
                *slot = (name, c.prepare_cached(sql)?.query_row([], |r| r.get(0))?);
            }
            Ok(StoredDigest { rows, accounts, stats })
        })
    }
}

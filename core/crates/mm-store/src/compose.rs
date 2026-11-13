//! What Compose reads of the database (`compose.adapters.sqlite`): the outbox and the drafts' journal.

use mm_compose::ports::{Row, StoredOutbox};
use mm_kernel::errors::Fault;

use crate::db::Database;
use crate::mailbox::lists::rows_of;

const RECENT: &str = "SELECT id,account,subject,recipients,state,attempts,last_error,created_at,sent_at,\
                      next_attempt_at,delivered,wire_begun,sent_appended,wait_kind \
                      FROM outbox ORDER BY id DESC LIMIT ?";

const OVERDUE: &str = "SELECT j.draft_id, j.account, j.discard, j.saved_at, j.attempts, d.subject \
                       FROM draft_journal j JOIN drafts d ON d.id=j.draft_id \
                       WHERE (j.attempts>0 OR j.due_at<=?) AND NOT EXISTS (SELECT 1 FROM outbox o \
                       WHERE o.draft_id=j.draft_id AND o.state IN ('queued','sending')) \
                       ORDER BY j.saved_at DESC";

impl StoredOutbox for Database {
    fn recent(&self, limit: i64) -> Result<Vec<Row>, Fault> {
        self.read(|c| rows_of(c, RECENT, [limit]))
    }

    fn overdue(&self, before: i64) -> Result<Vec<Row>, Fault> {
        self.read(|c| rows_of(c, OVERDUE, [before]))
    }
}

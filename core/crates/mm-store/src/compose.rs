//! What Compose reads of the database (`compose.adapters.sqlite`): the outbox and the drafts' journal, and the
//! drafts it tracks, one of which a message opening tracks.

use mm_compose::ports::{Row, StoredDrafts, StoredOutbox, Tracked};
use mm_kernel::errors::Fault;
use rusqlite::{OptionalExtension, named_params};

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

impl StoredDrafts for Database {
    fn by_message_id(&self, account: &str, message_id: Option<&str>) -> Result<Option<Row>, Fault> {
        let Some(message_id) = message_id.filter(|m| !m.is_empty()) else { return Ok(None) };
        let sql = "SELECT * FROM drafts WHERE account=? AND message_id=?";
        self.read(|c| Ok(rows_of(c, sql, [account, message_id])?.into_iter().next()))
    }

    fn track(&self, draft: &Tracked) -> Result<i64, Fault> {
        self.read(|c| {
            c.prepare_cached(
                "INSERT INTO drafts(account,folder,uid,message_id,uidvalidity,mode,subject,created_at,updated_at) \
                 VALUES(:account,:folder,:uid,:message_id,:uidvalidity,:mode,:subject,:at,:at) RETURNING id",
            )?
            .query_row(
                named_params! {
                    ":account": draft.account,
                    ":folder": draft.folder,
                    ":uid": draft.uid,
                    ":message_id": draft.message_id,
                    ":uidvalidity": draft.uidvalidity,
                    ":mode": draft.mode,
                    ":subject": draft.subject,
                    ":at": draft.at,
                },
                |r| r.get(0),
            )
        })
    }

    fn uidvalidity(&self, account: &str, folder: &str) -> Result<Option<i64>, Fault> {
        self.read(|c| {
            c.prepare_cached("SELECT uidvalidity FROM sync_state WHERE account=? AND folder=?")?
                .query_row([account, folder], |r| r.get::<_, Option<i64>>(0))
                .optional()
                .map(Option::flatten)
        })
    }
}

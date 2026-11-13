//! What Compose keeps in the database (`compose.adapters.sqlite`): the outbox and the drafts' journal, the drafts it
//! tracks, one of which a message opening tracks, and the snippets, as the `snippets` document
//! (`mailmind.compose.adapters.snippets_store`), a row of `documents` (migration 48) as
//! `mailmind.platform.db.documents` reads and writes one.

use std::sync::Arc;

use mm_compose::ports::{Row, SnippetsStore, StoredDrafts, StoredOutbox, Tracked};
use mm_kernel::errors::Fault;
use mm_kernel::seams::Clock;
use mm_kernel::when;
use rusqlite::{Connection, OptionalExtension, named_params};
use serde_json::Value;

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
        self.write(|c| {
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

const SNIPPETS: &str = "snippets";

/// The snippets document, written at the time `clock` tells.
#[derive(Debug)]
pub struct SnippetsDocument {
    database: Arc<Database>,
    clock: Arc<dyn Clock>,
}

impl SnippetsDocument {
    pub fn new(database: Arc<Database>, clock: Arc<dyn Clock>) -> SnippetsDocument {
        SnippetsDocument { database, clock }
    }
}

impl SnippetsStore for SnippetsDocument {
    fn read(&self) -> Result<Option<Value>, Fault> {
        self.database.read(|c| document(c, SNIPPETS))
    }

    fn change(&self, change: &mut dyn FnMut(Option<Value>) -> Option<Value>) -> Result<(), Fault> {
        let now = when::iso(self.clock.now());
        self.database.write(|c| match change(document(c, SNIPPETS)?) {
            Some(value) => write_document(c, SNIPPETS, &value, &now),
            None => Ok(()),
        })
    }
}

/// What a document holds, as JSON parses it: None when there is none yet, or its text won't parse.
fn document(c: &Connection, name: &str) -> rusqlite::Result<Option<Value>> {
    let body: Option<String> =
        c.query_row("SELECT body FROM documents WHERE name=?", [name], |r| r.get(0)).optional()?;
    Ok(body.and_then(|body| serde_json::from_str(&body).ok()))
}

/// `value` as the whole document, a version on from the last, written as `json.dumps(value, indent=2,
/// ensure_ascii=False)` writes it.
fn write_document(c: &Connection, name: &str, value: &Value, now: &str) -> rusqlite::Result<()> {
    let body = serde_json::to_string_pretty(value).map_err(|e| rusqlite::Error::ToSqlConversionFailure(Box::new(e)))?;
    c.execute(
        "INSERT INTO documents(name, body, changed_at) VALUES(?, ?, ?) ON CONFLICT(name) DO UPDATE SET \
         body=excluded.body, version=documents.version+1, changed_at=excluded.changed_at",
        (name, body, now),
    )?;
    Ok(())
}

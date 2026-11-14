//! A message as stored, to open (`mailbox.adapters.sqlite.messages` and `.contents`): the row of a copy, its raw
//! bytes and what is kept of its parts.

use mm_content::mime::Attachment;
use mm_kernel::errors::Fault;
use mm_mailbox::fetch_ports::{KeptBodies, KeptBody};
use mm_mailbox::ports::{Row, StoredBodies};
use rusqlite::{Connection, OptionalExtension, params};

use super::lists::rows_of;
use crate::db::Database;

impl StoredBodies for Database {
    fn copy_pk(&self, key: i64, folder: &str, uid: Option<i64>) -> Result<Option<i64>, Fault> {
        self.read(|c| crate::copies::copy_pk(c, key, folder, uid))
    }

    fn message(&self, pk: i64) -> Result<Option<Row>, Fault> {
        self.read(|c| Ok(rows_of(c, "SELECT * FROM messages WHERE pk=?", [pk])?.into_iter().next()))
    }

    fn raw(&self, pk: i64) -> Result<Option<Vec<u8>>, Fault> {
        self.read(|c| {
            c.prepare_cached("SELECT raw FROM bodies WHERE message_pk=?")?
                .query_row([pk], |r| r.get::<_, Option<Vec<u8>>>(0))
                .optional()
                .map(Option::flatten)
        })
    }

    fn part(&self, pk: i64, part_id: &str) -> Result<Option<Row>, Fault> {
        self.read(|c| {
            let sql = "SELECT filename,content_type FROM attachments WHERE message_pk=? AND part_id=?";
            Ok(rows_of(c, sql, params![pk, part_id])?.into_iter().next())
        })
    }

    fn has_parts(&self, pk: i64) -> Result<bool, Fault> {
        self.read(|c| {
            c.prepare_cached("SELECT 1 FROM attachments WHERE message_pk=?")?
                .query_row([pk], |_| Ok(()))
                .optional()
                .map(|found| found.is_some())
        })
    }

    fn keep_parts(&self, pk: i64, parts: &[Attachment]) -> Result<(), Fault> {
        self.write(|c| insert_parts(c, pk, parts))
    }
}

/// How many rows of who a message went to are kept: a forged To can be as long as it likes.
const RECIPIENT_ROWS_MAX: usize = 5000;

/// Keep what is known of a message's parts (`insert_attachments`).
fn insert_parts(c: &Connection, pk: i64, parts: &[Attachment]) -> rusqlite::Result<()> {
    let mut insert = c.prepare_cached(
        "INSERT INTO attachments(message_pk,part_id,filename,content_type,size,content_id,is_inline) \
         VALUES(?,?,?,?,?,?,?)",
    )?;
    for a in parts {
        insert.execute(params![
            pk,
            a.part_id.to_string(),
            a.filename,
            a.content_type,
            i64::try_from(a.size).unwrap_or(i64::MAX),
            a.content_id,
            i64::from(a.is_inline),
        ])?;
    }
    Ok(())
}

/// Keep a message's body with what opening it records (`_keep_body`): `store_body`, its parts in place of any kept,
/// `mark_has_attachments`, `set_recipients_text`, `set_recipients` and `fts_set_body`. The caller commits.
pub(crate) fn keep_body(c: &Connection, pk: i64, body: &KeptBody) -> rusqlite::Result<()> {
    c.prepare_cached("INSERT OR REPLACE INTO bodies(message_pk,raw,fetched_at) VALUES(?,?,?)")?
        .execute(params![pk, body.raw, body.when])?;
    c.prepare_cached("UPDATE messages SET body_fetched=1 WHERE pk=?")?.execute([pk])?;
    c.prepare_cached("DELETE FROM attachments WHERE message_pk=?")?.execute([pk])?;
    insert_parts(c, pk, &body.parts)?;
    if !body.parts.is_empty() {
        c.prepare_cached("UPDATE messages SET has_attach=1 WHERE pk=?")?.execute([pk])?;
    }
    c.prepare_cached("UPDATE messages SET recipients=? WHERE pk=?")?.execute(params![body.recipients, pk])?;
    c.prepare_cached("DELETE FROM message_recipients WHERE message_pk=?")?.execute([pk])?;
    for addr in body.recipient_addrs.iter().take(RECIPIENT_ROWS_MAX) {
        c.prepare_cached("INSERT OR IGNORE INTO message_recipients(message_pk, addr) VALUES (?,?)")?
            .execute(params![pk, addr])?;
    }
    c.prepare_cached(
        "UPDATE messages SET body_text=?, snippet=CASE WHEN snippet IS NULL OR snippet='' THEN ? ELSE snippet END \
         WHERE pk=?",
    )?
    .execute(params![body.body_text, body.snippet, pk])?;
    crate::mailbox_sync::index_message(c, pk)
}

impl KeptBodies for Database {
    fn keep_body(&self, pk: i64, body: &KeptBody) -> Result<(), Fault> {
        self.write(|c| keep_body(c, pk, body))
    }
}

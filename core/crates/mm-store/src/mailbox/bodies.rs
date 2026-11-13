//! A message as stored, to open (`mailbox.adapters.sqlite.messages` and `.contents`): the row of a copy, its raw
//! bytes and what is kept of its parts.

use mm_content::mime::Attachment;
use mm_kernel::errors::Fault;
use mm_mailbox::ports::{Row, StoredBodies};
use rusqlite::{OptionalExtension, params};

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
        self.write(|c| {
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
        })
    }
}

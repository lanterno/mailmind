//! A body fetched as its message opens, kept as the Python app's `_keep_body` keeps it: its bytes, its parts in place
//! of any kept before, who it went to, and its text, which search finds and a list without a snippet shows.

#![allow(clippy::unwrap_used)]

use std::path::Path;
use std::sync::Arc;

use mm_kernel::seams::{Outside, Tripped};
use mm_mailbox::fetch_ports::{KeptBodies, KeptBody};
use mm_mailbox::ports::StoredBodies;
use mm_store::{Database, DbKey, Store};

#[derive(Debug)]
struct Open;

impl Outside for Open {
    fn open(&self, _path: &Path) -> Result<(), Tripped> {
        Ok(())
    }

    fn reach(&self, _host: &str) -> Result<(), Tripped> {
        Ok(())
    }

    fn run(&self, _program: &str) -> Result<(), Tripped> {
        Ok(())
    }
}

/// The tables a body is kept in, as the newest schema golden has them, and two messages: one whose sync kept a
/// snippet, one whose didn't.
const TABLES: &str = "
CREATE TABLE attachments ( id INTEGER PRIMARY KEY AUTOINCREMENT, message_pk INTEGER REFERENCES messages(pk) ON DELETE
  CASCADE, part_id TEXT, filename TEXT, content_type TEXT, size INTEGER, sha256 TEXT, is_inline INTEGER DEFAULT 0,
  content_id TEXT );
CREATE TABLE bodies ( message_pk INTEGER PRIMARY KEY REFERENCES messages(pk) ON DELETE CASCADE, raw BLOB,
  fetched_at TEXT );
CREATE TABLE message_recipients( message_pk INTEGER NOT NULL REFERENCES messages(pk) ON DELETE CASCADE,
  addr TEXT NOT NULL, PRIMARY KEY(message_pk, addr));
CREATE VIRTUAL TABLE msg_fts USING fts5(subject, from_name, from_addr, recipients, snippet, body_text,
  tokenize='unicode61 remove_diacritics 2', content='', contentless_delete=1);
INSERT INTO messages (pk, account, kind, folder, uid, subject, snippet, message_key) VALUES
  (1, 'ada@example.test', 'imap', 'Archive', 7, 'Lunch', '', 1),
  (2, 'ada@example.test', 'imap', 'INBOX', 8, 'Swatches', 'Kept at sync', 2);
INSERT INTO attachments (message_pk, part_id, filename) VALUES (1, '9', 'stale.bin');
INSERT INTO message_recipients VALUES (1, 'gone@example.test');
";

const RAW: &[u8] = b"From: Lin <lin@example.test>\r\nTo: Ada <ada@example.test>\r\nCc: bo@example.test\r\n\
Subject: Lunch\r\nContent-Type: multipart/mixed; boundary=b\r\n\r\n--b\r\nContent-Type: text/plain\r\n\r\n\
Herringbone lunch at noon?\r\n--b\r\nContent-Type: application/pdf; name=menu.pdf\r\nContent-Disposition: \
attachment; filename=menu.pdf\r\n\r\nPDF\r\n--b--\r\n";

fn database(dir: &Path) -> Database {
    let key = DbKey::from_bytes([0x5e; 32]);
    let path = dir.join("mailmind.db");
    Store::open(&path, &key).unwrap().create_list_tables().unwrap();
    let database = Database::new(path, key, Arc::new(Open));
    database.read(|c| c.execute_batch(TABLES)).unwrap();
    database
}

fn found(database: &Database, word: &str) -> Vec<i64> {
    database
        .read(|c| {
            let mut found = c.prepare("SELECT rowid FROM msg_fts WHERE msg_fts MATCH ? ORDER BY rowid")?;
            found.query_map([word], |r| r.get(0))?.collect()
        })
        .unwrap()
}

#[test]
fn a_body_is_kept_with_its_parts_its_recipients_and_its_text() {
    let dir = tempfile::tempdir().unwrap();
    let database = database(dir.path());
    database.keep_body(1, &KeptBody::of(RAW.to_vec(), "2026-11-02T12:00:00+00:00".to_owned())).unwrap();

    assert_eq!(database.raw(1).unwrap().unwrap(), RAW);
    let row = database.message(1).unwrap().unwrap();
    assert_eq!(row["body_fetched"], 1);
    assert_eq!(row["has_attach"], 1);
    assert_eq!(row["recipients"], "Ada <ada@example.test> bo@example.test");
    assert_eq!(row["body_text"], "Herringbone lunch at noon?");
    assert_eq!(row["snippet"], "Herringbone lunch at noon?");
    let parts: Vec<(String, String)> = database
        .read(|c| {
            let mut parts = c.prepare("SELECT part_id, filename FROM attachments WHERE message_pk=1")?;
            parts.query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?.collect()
        })
        .unwrap();
    assert_eq!(parts, [("2".to_owned(), "menu.pdf".to_owned())]);
    let went_to: Vec<String> = database
        .read(|c| {
            let mut addrs = c.prepare("SELECT addr FROM message_recipients WHERE message_pk=1 ORDER BY addr")?;
            addrs.query_map([], |r| r.get(0))?.collect()
        })
        .unwrap();
    assert_eq!(went_to, ["ada@example.test", "bo@example.test"]);
    assert_eq!(found(&database, "herringbone"), [1]);
    assert_eq!(found(&database, "bo"), [1]);
}

#[test]
fn a_snippet_the_sync_kept_stays() {
    let dir = tempfile::tempdir().unwrap();
    let database = database(dir.path());
    database.keep_body(2, &KeptBody::of(RAW.to_vec(), "2026-11-02T12:00:00+00:00".to_owned())).unwrap();
    let row = database.message(2).unwrap().unwrap();
    assert_eq!(row["snippet"], "Kept at sync");
    assert_eq!(row["body_text"], "Herringbone lunch at noon?");
}

#[test]
fn a_body_kept_again_replaces_what_was_kept() {
    let dir = tempfile::tempdir().unwrap();
    let database = database(dir.path());
    let body = KeptBody::of(RAW.to_vec(), "2026-11-02T12:00:00+00:00".to_owned());
    database.keep_body(1, &body).unwrap();
    database.keep_body(1, &body).unwrap();
    let counts: (i64, i64) = database
        .read(|c| {
            c.query_row(
                "SELECT (SELECT COUNT(*) FROM bodies), (SELECT COUNT(*) FROM attachments WHERE message_pk=1)",
                [],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
        })
        .unwrap();
    assert_eq!(counts, (1, 1));
}

//! What a sync asks of the store as a person's filters judge new mail, as the Python app's `is_known`, `fits` and
//! `queue_flags` answer: whether a message was seen before, whether a stored one is one a filter's search finds, and
//! what a filter asks queued only where the row doesn't hold it already.

#![allow(clippy::unwrap_used)]

use std::path::Path;
use std::sync::Arc;

use mm_content::mime;
use mm_discovery::query::QuerySpec;
use mm_kernel::seams::{Outside, Tripped};
use mm_mailbox::sync_ports::{Incoming, SyncAccount, SyncStore, Verdict};
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

/// The tables storing a copy and queueing its flags touch, as the newest schema golden has them.
const TABLES: &str = "
CREATE TABLE mail_commands(id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, message_id TEXT, src_folder TEXT NOT NULL, dest TEXT,
  params TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt_at INTEGER NOT NULL DEFAULT 0, applied INTEGER NOT NULL DEFAULT 0, begun INTEGER NOT NULL DEFAULT 0,
  outcome TEXT, last_error TEXT, created_at TEXT NOT NULL, done_at TEXT);
CREATE TABLE sync_state (account TEXT NOT NULL, folder TEXT NOT NULL DEFAULT 'INBOX', uidvalidity INTEGER,
  PRIMARY KEY(account, folder));
CREATE TABLE message_recipients( message_pk INTEGER NOT NULL REFERENCES messages(pk) ON DELETE CASCADE,
  addr TEXT NOT NULL, PRIMARY KEY(message_pk, addr));
CREATE TABLE assessments( message_key INTEGER PRIMARY KEY REFERENCES message_identity(key) ON DELETE CASCADE,
  summary TEXT, importance INTEGER, category TEXT, action TEXT, model TEXT, created_at TEXT, served_by TEXT,
  prompt_version INTEGER, outcome TEXT);
CREATE VIRTUAL TABLE msg_fts USING fts5(subject, from_name, from_addr, recipients, snippet, body_text,
  tokenize='unicode61 remove_diacritics 2', content='', contentless_delete=1);
INSERT INTO sync_state VALUES ('ada@example.test', 'INBOX', 7);
";

const LUNCH: &[u8] = b"From: Lin <lin@example.test>\r\nTo: Ada <ada@example.test>\r\nSubject: Lunch on Thursday\r\n\
Date: Mon, 02 Nov 2026 09:00:00 +0000\r\nMessage-ID: <lunch-1@example.test>\r\n\r\n";

const WHEN: &str = "2026-11-02T12:00:00+00:00";

fn database(dir: &Path) -> Database {
    let key = DbKey::from_bytes([0x5e; 32]);
    let path = dir.join("mailmind.db");
    Store::open(&path, &key).unwrap().create_list_tables().unwrap();
    let database = Database::new(path, key, Arc::new(Open));
    database.read(|c| c.execute_batch(TABLES)).unwrap();
    database
}

fn ada() -> SyncAccount {
    SyncAccount { email: "ada@example.test".to_owned(), kind: "imap".to_owned(), gmail_threads: false }
}

/// The lunch as a sync stores it, with the flags the server said; its pk.
fn stored(database: &Database, uid: u32, flags: [i64; 5]) -> i64 {
    let incoming = Incoming {
        uid,
        flags,
        fields: mime::fields(LUNCH),
        gmail_thread: None,
        gmail_labels: None,
        verdict: Verdict {
            bucket: "personal".to_owned(),
            confidence: "high".to_owned(),
            reason: String::new(),
            is_junk: false,
        },
        snippet: "Shall we say noon?".to_owned(),
    };
    database.store_copy(&ada(), "INBOX", &incoming).unwrap().0
}

fn fits(database: &Database, pk: i64, q: &str) -> bool {
    database.read(|c| mm_store::discovery::fits(c, pk, &QuerySpec::parse(q, |_| None))).unwrap()
}

fn queued(database: &Database) -> Vec<(String, String)> {
    database
        .read(|c| {
            let mut rows =
                c.prepare("SELECT idempotency_key, params FROM mail_commands WHERE kind='flag' ORDER BY id")?;
            rows.query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?.collect()
        })
        .unwrap()
}

#[test]
fn a_message_is_known_once_any_copy_of_it_is_stored() {
    let dir = tempfile::tempdir().unwrap();
    let database = database(dir.path());
    let fields = mime::fields(LUNCH);
    assert!(!database.is_known("ada@example.test", &fields).unwrap());
    stored(&database, 5, [0; 5]);
    assert!(database.is_known("ada@example.test", &fields).unwrap());
    assert!(!database.is_known("bo@example.test", &fields).unwrap());
}

#[test]
fn a_filters_search_is_asked_of_the_one_message_just_stored() {
    let dir = tempfile::tempdir().unwrap();
    let database = database(dir.path());
    let pk = stored(&database, 5, [0; 5]);
    assert!(fits(&database, pk, "from:lin@example.test"));
    assert!(fits(&database, pk, "thursday"));
    assert!(fits(&database, pk, "noon"));
    assert!(fits(&database, pk, "is:unread"));
    assert!(!fits(&database, pk, "is:read"));
    assert!(!fits(&database, pk, "from:lin@example.test -subject:thursday"));
    assert!(!fits(&database, pk, "-subject:thursday"));
    assert!(fits(&database, pk, "-subject:friday"));
    assert!(!fits(&database, pk + 1, "thursday"));
}

#[test]
fn a_search_sqlite_rejects_finds_nothing() {
    let dir = tempfile::tempdir().unwrap();
    let database = database(dir.path());
    let pk = stored(&database, 5, [0; 5]);
    database.read(|c| c.execute_batch("DROP TABLE assessments")).unwrap();
    assert!(!fits(&database, pk, "is:unread"));
}

#[test]
fn only_what_the_row_lacks_is_queued_and_shown_at_once() {
    let dir = tempfile::tempdir().unwrap();
    let database = database(dir.path());
    let pk = stored(&database, 5, [1, 0, 0, 0, 0]);
    assert!(database.queue_filtered(pk, &[("flagged", true), ("seen", true)], WHEN).unwrap());
    let [(key, params)] = &queued(&database)[..] else { panic!("one change queued") };
    assert_eq!(key, "flag:ada@example.test:<lunch-1@example.test>:filter");
    let params: serde_json::Value = serde_json::from_str(params).unwrap();
    assert_eq!(params["flags"], serde_json::json!({"flagged": true}));
    assert_eq!(params["was"], serde_json::json!({"flagged": false}));
    let row = database.read(|c| {
        c.query_row("SELECT seen, flagged FROM messages WHERE pk=?", [pk], |r| {
            Ok((r.get::<_, i64>(0)?, r.get::<_, i64>(1)?))
        })
    });
    assert_eq!(row.unwrap(), (1, 1));
}

#[test]
fn flags_it_came_with_ask_nothing_and_a_second_ask_is_the_first() {
    let dir = tempfile::tempdir().unwrap();
    let database = database(dir.path());
    let held = stored(&database, 5, [1, 1, 0, 0, 0]);
    assert!(!database.queue_filtered(held, &[("flagged", true), ("seen", true)], WHEN).unwrap());
    assert!(queued(&database).is_empty());
    let other = stored(&database, 6, [0; 5]);
    assert!(database.queue_filtered(other, &[("flagged", true)], WHEN).unwrap());
    database.read(|c| c.execute("UPDATE messages SET flagged=0 WHERE pk=?", [other])).unwrap();
    assert!(!database.queue_filtered(other, &[("flagged", true)], WHEN).unwrap());
    assert_eq!(queued(&database).len(), 1);
    assert!(!database.queue_filtered(999, &[("flagged", true)], WHEN).unwrap());
}

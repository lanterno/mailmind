//! What the Rust server's counts read through the store: an account's labels, its stored inbox, the rule book and
//! each folder's sync health.

#![allow(clippy::unwrap_used)]

use std::path::Path;
use std::sync::Arc;

use mm_curation::ports::{RuleBook, Stored, StoredInbox};
use mm_kernel::errors::Fault;
use mm_kernel::seams::{Outside, Tripped};
use mm_mailbox::ports::{StoredLabels, StoredSyncs};
use mm_store::{Database, DbKey, Store};
use serde_json::json;

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

const ROWS: &str = r"
CREATE TABLE message_labels (message_pk INTEGER NOT NULL, label TEXT NOT NULL, PRIMARY KEY(message_pk, label));
CREATE TABLE documents (name TEXT PRIMARY KEY, body TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, changed_at TEXT);
CREATE TABLE sync_state (account TEXT, folder TEXT, last_ok_ts TEXT, last_error TEXT, last_error_ts TEXT,
  last_error_kind TEXT, sync_count INTEGER, PRIMARY KEY(account, folder));
INSERT INTO messages (pk, account, kind, folder, uid, from_addr, subject, bucket, list_unsub_raw, deleted, message_key,
  pending_removal) VALUES
  (1, 'ada@example.test', 'gmail', 'INBOX', 1, 'lin@example.test', 'Lunch', 'other/personal-ish', NULL, 0, 1, NULL),
  (2, 'ada@example.test', 'gmail', '[Gmail]/All Mail', 1, 'lin@example.test', 'Lunch', NULL, NULL, 0, 1, NULL),
  (3, 'ada@example.test', 'gmail', 'INBOX', 2, 'news@loom.example.test', NULL, NULL, '<https://loom.example.test/u>', 0,
   2, NULL),
  (4, 'ada@example.test', 'gmail', 'INBOX', 3, 'gone@example.test', 'Old', NULL, NULL, 1, 3, NULL),
  (5, 'ada@example.test', 'gmail', 'INBOX', 4, 'leaving@example.test', 'Moved', NULL, NULL, 0, 4, 1),
  (6, 'bea@example.test', 'imap', 'INBOX', 1, 'ivo@example.test', 'Hi', NULL, NULL, 0, 9, NULL);
INSERT INTO message_labels VALUES (1, 'Receipts'), (2, 'Receipts'), (3, 'apple'), (1, '\Important'), (5, 'Zeta'),
  (6, 'Bea''s');
INSERT INTO sync_state VALUES ('bea@example.test', 'INBOX', '2026-11-01T10:00:00+00:00', NULL, NULL, NULL, 2),
  ('ada@example.test', 'INBOX', NULL, 'TimeoutError', '2026-11-01T11:00:00+00:00', 'TIMEOUT', NULL);
";

fn database() -> (tempfile::TempDir, Database) {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("mailmind.db");
    let key = DbKey::from_bytes([0x5e; 32]);
    Store::open(&path, &key).unwrap().create_list_tables().unwrap();
    let database = Database::new(path, key, Arc::new(Open));
    database.read(|c| c.execute_batch(ROWS)).unwrap();
    (dir, database)
}

#[test]
fn an_account_s_labels_count_each_message_once_and_leave_out_gmail_s_own() {
    let (_dir, database) = database();
    assert_eq!(
        database.labels_of_account("ada@example.test").unwrap(),
        [("apple".to_owned(), 1), ("Receipts".to_owned(), 1)]
    );
    assert_eq!(database.labels_of_account("bea@example.test").unwrap(), [("Bea's".to_owned(), 1)]);
    assert!(database.labels_of_account("cy@example.test").unwrap().is_empty());
}

#[test]
fn a_stored_inbox_leaves_out_what_is_deleted_or_on_its_way_out() {
    let (_dir, database) = database();
    let inbox = database.stored_inbox("ada@example.test").unwrap();
    let senders: Vec<&str> = inbox.iter().map(|s| s.from_addr.as_deref().unwrap()).collect();
    assert_eq!(senders, ["lin@example.test", "news@loom.example.test"]);
    assert_eq!(
        inbox[1],
        Stored {
            from_addr: Some("news@loom.example.test".into()),
            list_unsub_raw: Some("<https://loom.example.test/u>".into()),
            ..Stored::default()
        }
    );
}

#[test]
fn the_rule_book_is_its_document_none_before_a_rule_is_made_and_a_fault_when_it_wont_parse() {
    let (_dir, database) = database();
    assert_eq!(database.rules().unwrap(), None);
    database
        .read(|c| {
            c.execute(
                "INSERT INTO documents(name, body) VALUES('rules', '{\"keep_senders\": [\"a@example.test\"]}')",
                [],
            )
        })
        .unwrap();
    assert_eq!(database.rules().unwrap(), Some(json!({"keep_senders": ["a@example.test"]})));
    database.read(|c| c.execute("UPDATE documents SET body='{' WHERE name='rules'", [])).unwrap();
    assert_eq!(database.rules().unwrap_err(), Fault::new("MalformedRules"));
}

#[test]
fn each_folder_s_sync_health_comes_by_account_then_folder() {
    let (_dir, database) = database();
    let health = database.sync_health().unwrap();
    let named: Vec<(&str, i64, Option<&str>)> =
        health.iter().map(|h| (h.account.as_str(), h.sync_count, h.last_error_kind.as_deref())).collect();
    assert_eq!(named, [("ada@example.test", 0, Some("TIMEOUT")), ("bea@example.test", 2, None)]);
}

//! The encrypted store: keyed before anything reads it, and the list's two queries.

#![allow(clippy::unwrap_used)]

use mm_kernel::Refusal;
use mm_store::{DbKey, NewMessage, Store};

fn key() -> DbKey {
    DbKey::from_bytes([0x42; 32])
}

fn message(account: &str, folder: &str, uid: i64, ts: i64) -> NewMessage {
    NewMessage {
        account: account.into(),
        folder: folder.into(),
        uid,
        message_id: format!("<{uid}.{folder}@{account}>"),
        from_name: "Ada Example".into(),
        from_addr: "ada@example.test".into(),
        subject: format!("Subject {uid}"),
        snippet: "Fictional text.".into(),
        ts,
        seen: uid % 2 == 0,
        flagged: false,
        has_attach: false,
    }
}

fn store() -> Store {
    let mut store = Store::open_in_memory(&key()).unwrap();
    store.create_list_tables().unwrap();
    store
        .add_messages([
            message("a@example.test", "INBOX", 1, 100),
            message("a@example.test", "INBOX", 2, 300),
            message("b@example.test", "INBOX", 1, 200),
            message("a@example.test", "Archive", 3, 400),
            message("c@example.test", "INBOX", 1, 500),
        ])
        .unwrap();
    store
}

#[test]
fn the_file_on_disk_is_unreadable_without_the_key() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("mail.db");
    {
        let mut store = Store::open(&path, &key()).unwrap();
        store.create_list_tables().unwrap();
        store.add_messages([message("a@example.test", "INBOX", 1, 100)]).unwrap();
    }
    let bytes = std::fs::read(&path).unwrap();
    assert!(!bytes.starts_with(b"SQLite format 3"));
    assert!(!bytes.windows(16).any(|w| w == b"ada@example.test"));
    assert_eq!(Store::open(&path, &DbKey::from_bytes([0x43; 32])).err(), Some(Refusal::KeyDoesNotFit));
    assert!(Store::open(&path, &key()).is_ok());
}

#[test]
fn it_runs_sqlcipher() {
    assert!(store().cipher_version().unwrap().starts_with('4'));
}

#[test]
fn a_list_merges_its_accounts_newest_first_and_leaves_out_other_folders() {
    let store = store();
    let accounts = vec!["a@example.test".to_owned(), "b@example.test".to_owned()];
    let order = store.list_order(&accounts, "INBOX").unwrap();
    let subjects: Vec<_> = store.rows(&order).unwrap().into_iter().map(|r| r.unwrap().subject.unwrap()).collect();
    assert_eq!(subjects, ["Subject 2", "Subject 1", "Subject 1"]);
    let dates: Vec<_> = store.rows(&order).unwrap().into_iter().map(|r| r.unwrap().ts.unwrap()).collect();
    assert_eq!(dates, [300, 200, 100]);
}

#[test]
fn a_message_being_removed_is_not_listed() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("mail.db");
    let mut store = Store::open(&path, &key()).unwrap();
    store.create_list_tables().unwrap();
    store
        .add_messages([message("a@example.test", "INBOX", 1, 100), message("a@example.test", "INBOX", 2, 200)])
        .unwrap();
    let all = vec!["a@example.test".to_owned()];
    assert_eq!(store.list_order(&all, "INBOX").unwrap().len(), 2);
    // Another connection marks one, as the Python app's undoable moves do.
    let other = rusqlite::Connection::open(&path).unwrap();
    other.execute_batch(&format!("PRAGMA key = \"x'{}'\";", "42".repeat(32))).unwrap();
    other.execute("UPDATE messages SET pending_removal = 1 WHERE uid = 2", []).unwrap();
    assert_eq!(store.list_order(&all, "INBOX").unwrap().len(), 1);
}

#[test]
fn rows_come_in_the_order_asked_and_a_gone_row_is_none() {
    let store = store();
    let order = store.list_order(&["a@example.test".to_owned()], "INBOX").unwrap();
    let mut asked = order.clone();
    asked.reverse();
    asked.push(9_999);
    let rows = store.rows(&asked).unwrap();
    assert_eq!(rows.len(), 3);
    assert_eq!(rows[0].as_ref().unwrap().ts, Some(100));
    assert!(rows[2].is_none());
}

#[test]
fn every_row_carries_its_durable_key() {
    let store = store();
    let order = store.list_order(&["a@example.test".to_owned(), "b@example.test".to_owned()], "INBOX").unwrap();
    let keys: Vec<_> = store.rows(&order).unwrap().into_iter().map(|r| r.unwrap().key).collect();
    let mut unique = keys.clone();
    unique.sort();
    unique.dedup();
    assert_eq!(unique.len(), keys.len());
}

#[test]
fn a_key_is_64_hex_digits_and_never_prints() {
    assert!(DbKey::from_hex(&"0f".repeat(32)).is_ok());
    assert!(DbKey::from_hex(&"0f".repeat(31)).is_err());
    assert!(DbKey::from_hex(&"zz".repeat(32)).is_err());
    let key = DbKey::from_hex(&"ab".repeat(32)).unwrap();
    assert_eq!(format!("{key:?}"), "DbKey(..)");
}

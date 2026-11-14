//! What a sender's cleanup, its unsubscribe and a sweep keep in the database, as the Python app keeps it
//! (mailmind.curation.adapters.sqlite and audit_events): stored mail by sender, the way out, the attempts, the sweep
//! trail, a block's reversal and the rows a sweep forgets.

#![allow(clippy::unwrap_used)]

use std::path::Path;
use std::sync::Arc;

use mm_curation::cleanup_ports::{Audited, CleanupMail, SweepTrail, SweptRows};
use mm_curation::unsubscribe::{Attempt, list_unsub};
use mm_kernel::seams::{Clock, Moment, Outside, Tripped};
use mm_store::curation::CurationStore;
use mm_store::{Database, DbKey, Store};
use serde_json::{Value, json};

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

#[derive(Debug)]
struct Frozen;

impl Clock for Frozen {
    fn now(&self) -> Moment {
        Moment::from_micros(1_793_620_800_000_000)
    }
}

const LOOM: &str = "news@loom.example.test";
const ADA: &str = "ada@example.test";

const TABLES: &str = "
CREATE TABLE messages(pk INTEGER PRIMARY KEY, message_key INTEGER, account TEXT, folder TEXT, uid INTEGER,
  message_id TEXT, from_addr TEXT, subject TEXT, seen INTEGER DEFAULT 0, ts INTEGER, pending_removal INTEGER,
  list_unsub_raw TEXT, list_unsub_method TEXT);
CREATE TABLE bodies(message_pk INTEGER PRIMARY KEY, raw BLOB, fetched_at TEXT);
CREATE TABLE message_identity(key INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL, identity TEXT NOT NULL,
  gone_at INTEGER, UNIQUE(account, identity));
CREATE VIRTUAL TABLE msg_fts USING fts5(subject, content='', contentless_delete=1);
CREATE TABLE sender_unsub(from_addr TEXT PRIMARY KEY, raw TEXT NOT NULL, accounts TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE unsub_log(id INTEGER PRIMARY KEY, from_addr TEXT, method TEXT, status TEXT, http_status INTEGER, ts TEXT,
  idempotency_key TEXT UNIQUE, accounts TEXT);
CREATE TABLE audit_events(id INTEGER PRIMARY KEY, trail TEXT NOT NULL, ts TEXT NOT NULL, body TEXT NOT NULL);
CREATE TABLE undo_entries(id INTEGER PRIMARY KEY AUTOINCREMENT, grp TEXT, entry TEXT NOT NULL, created_at TEXT NOT NULL);
";

/// A database with the Loom's three issues, the newest first by `ts`: one in the INBOX on its way out, one in the
/// INBOX, and one in the Archive with its body stored; and a row of Lin's.
fn store() -> (tempfile::TempDir, CurationStore, Arc<Database>) {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("mailmind.db");
    let key = DbKey::from_bytes([0x5e; 32]);
    Store::open(&path, &key).unwrap();
    let database = Arc::new(Database::new(path, key, Arc::new(Open)));
    database
        .write(|c| {
            c.execute_batch(TABLES)?;
            c.execute_batch(
                "INSERT INTO message_identity(key, account, identity) VALUES (1, 'ada@example.test', 'a'),
                   (2, 'ada@example.test', 'b'), (3, 'ada@example.test', 'c');
                 INSERT INTO messages(pk, message_key, account, folder, uid, message_id, from_addr, subject, seen, ts,
                   pending_removal) VALUES
                   (1, 1, 'ada@example.test', 'INBOX', 11, '<218@loom.example.test>', 'News@Loom.example.test',
                    'Issue 218', 0, 300, 1),
                   (2, 2, 'ada@example.test', 'INBOX', 12, '<217@loom.example.test>', 'news@loom.example.test',
                    'Issue 217', 1, 200, NULL),
                   (3, 3, 'bea@example.test', 'Archive', 13, '', 'news@loom.example.test', 'Issue 216', 0, 100, NULL),
                   (4, NULL, 'ada@example.test', 'INBOX', 14, '<lunch@example.test>', 'lin@example.test', 'Lunch', 0,
                    400, NULL);",
            )?;
            c.execute(
                "INSERT INTO bodies(message_pk, raw) VALUES (3, ?)",
                [&b"List-Unsubscribe: <mailto:out@loom.example.test>\r\n\r\nHello.\r\n"[..]],
            )?;
            Ok(())
        })
        .unwrap();
    (dir, CurationStore::new(database.clone(), Arc::new(Frozen)), database)
}

#[test]
fn a_senders_mail_is_counted_and_its_inbox_copies_named_newest_first() {
    let (_dir, store, _) = store();
    assert_eq!(store.counts(LOOM).unwrap(), (3, 2));
    let targets = store.sweep_targets(LOOM, 10).unwrap();
    // The issue on its way out is left out.
    assert_eq!(targets.iter().map(|t| (t.pk, t.key, t.uid)).collect::<Vec<_>>(), [(2, Some(2), Some(12))]);
    assert!(store.sweep_targets(LOOM, 0).unwrap().is_empty());
}

#[test]
fn the_way_out_is_read_from_a_stored_body_and_kept_on_its_row() {
    let (_dir, store, database) = store();
    let (rows, accounts, info) = store.unsub_stored(LOOM).unwrap();
    assert_eq!(rows.iter().map(|r| r.pk).collect::<Vec<_>>(), [1, 2, 3]);
    assert_eq!(accounts, [ADA, "bea@example.test"]);
    let info = info.unwrap();
    assert_eq!((info.method, info.mailto_to.as_deref()), ("mailto", Some("out@loom.example.test")));
    let kept: (Option<String>, Option<String>) = database
        .read(|c| {
            c.query_row("SELECT list_unsub_raw, list_unsub_method FROM messages WHERE pk=3", [], |r| {
                Ok((r.get(0)?, r.get(1)?))
            })
        })
        .unwrap();
    assert_eq!(kept, (Some("<mailto:out@loom.example.test>".into()), Some("mailto".into())));
}

#[test]
fn the_way_out_is_kept_past_the_senders_mail_until_a_list_offers_none() {
    let (_dir, store, _) = store();
    let way = list_unsub("<https://loom.example.test/u>", "List-Unsubscribe=One-Click");
    store.keep_unsub(LOOM, &way, &[ADA.into()]).unwrap();
    assert_eq!(store.kept_unsub(LOOM).unwrap(), Some((way.raw.clone(), vec![ADA.to_owned()])));
    store.keep_unsub(LOOM, &list_unsub("", ""), &[ADA.into()]).unwrap();
    assert_eq!(store.kept_unsub(LOOM).unwrap(), None);
}

#[test]
fn an_attempt_is_kept_once_under_its_key() {
    let (_dir, store, database) = store();
    let tried = |status: &str| Attempt {
        status: status.into(),
        method: "one-click".into(),
        http_status: Some(200),
        browser_url: None,
    };
    store.record_attempt(LOOM, &tried("unsubscribed_oneclick"), "unsub:x", &[ADA.into()]).unwrap();
    store.record_attempt(LOOM, &tried("failed"), "unsub:x", &[ADA.into()]).unwrap();
    let logged = store.attempt("unsub:x").unwrap().unwrap();
    assert_eq!((logged.status.as_str(), logged.method.as_str()), ("unsubscribed_oneclick", "one-click"));
    assert_eq!(store.attempt("unsub:y").unwrap(), None);
    let row: (String, String, i64) = database
        .read(|c| {
            c.query_row("SELECT ts, accounts, http_status FROM unsub_log", [], |r| {
                Ok((r.get(0)?, r.get(1)?, r.get(2)?))
            })
        })
        .unwrap();
    assert_eq!(row, ("2026-11-02T12:00:00+00:00".into(), "[\"ada@example.test\"]".into(), 200));
}

fn trail(database: &Database) -> Vec<(String, String, Value)> {
    database
        .read(|c| {
            c.prepare("SELECT trail, ts, body FROM audit_events ORDER BY id")?
                .query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get::<_, String>(2)?)))?
                .map(|row| row.map(|(trail, ts, body)| (trail, ts, serde_json::from_str(&body).unwrap())))
                .collect()
        })
        .unwrap()
}

#[test]
fn the_sweep_trail_records_each_message_and_each_cleanup_as_python_writes_them() {
    let (_dir, store, database) = store();
    let audited = Audited {
        uid: "12".into(),
        from: LOOM.into(),
        subject: "é".repeat(150),
        bucket: "newsletters/marketing".into(),
        reason: "List-Unsubscribe header".into(),
    };
    store.swept(ADA, &[audited]).unwrap();
    store.swept(ADA, &[]).unwrap();
    store.cleaned_up(LOOM, LOOM, 2, "skipped").unwrap();
    let ts = "2026-11-02T12:00:00+00:00";
    assert_eq!(
        trail(&database),
        [
            (
                "sweep".to_owned(),
                ts.to_owned(),
                json!({"kind": "sweep", "ts": ts, "account": ADA, "uid": "12", "from": LOOM,
            "subject": "é".repeat(140), "bucket": "newsletters/marketing", "reason": "List-Unsubscribe header"})
            ),
            (
                "sweep".to_owned(),
                ts.to_owned(),
                json!({"kind": "sender_cleanup", "ts": ts, "from": LOOM, "token": LOOM,
            "swept": 2, "status": "skipped"})
            ),
        ]
    );
    let body: String =
        database.read(|c| c.query_row("SELECT body FROM audit_events LIMIT 1", [], |r| r.get(0))).unwrap();
    assert!(body.starts_with("{\"kind\": \"sweep\", \"ts\": "), "json.dumps' spacing: {body}");
    assert!(body.contains("\\u00e9"), "json.dumps escapes what isn't ASCII: {body}");
}

#[test]
fn a_blocks_reversal_gathers_under_its_group() {
    let (_dir, store, database) = store();
    store.push_undo(&json!({"kind": "unblock", "token": LOOM, "label": "Cleaned up"}), "cleanup:1").unwrap();
    store
        .push_undo(&json!({"kind": "unblock", "token": "lin@example.test", "label": "Cleaned up"}), "cleanup:1")
        .unwrap();
    let entries: Vec<(Option<String>, String)> = database
        .read(|c| {
            c.prepare("SELECT grp, entry FROM undo_entries")?.query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?.collect()
        })
        .unwrap();
    assert_eq!(entries.len(), 1);
    let entry: Value = serde_json::from_str(&entries[0].1).unwrap();
    assert_eq!(
        (entries[0].0.as_deref(), entry["kind"].clone(), entry["children"].as_array().unwrap().len()),
        (Some("cleanup:1"), json!("bulk"), 2)
    );
}

#[test]
fn what_a_sweep_moved_is_forgotten_by_message_id_else_by_uid_and_a_straggler_stays() {
    let (_dir, store, database) = store();
    let moved = [
        ("11".to_owned(), "<218@loom.example.test>".to_owned()),
        ("14".to_owned(), String::new()),
        ("12".to_owned(), "<217@loom.example.test>".to_owned()),
    ];
    store.forget_moved(ADA, &moved, &["12".to_owned()]).unwrap();
    let left: Vec<i64> = database
        .read(|c| c.prepare("SELECT pk FROM messages ORDER BY pk")?.query_map([], |r| r.get(0))?.collect())
        .unwrap();
    assert_eq!(left, [2, 3]);
    let gone: Option<i64> =
        database.read(|c| c.query_row("SELECT gone_at FROM message_identity WHERE key=1", [], |r| r.get(0))).unwrap();
    assert_eq!(gone, Some(1_793_620_800));
}

#[test]
fn what_expiry_moved_is_forgotten_by_message_id_alone_and_a_straggler_stays() {
    let (_dir, store, database) = store();
    let moved = [
        ("11".to_owned(), "<218@loom.example.test>".to_owned()),
        // No Message-ID: UID 14 may be another row's now, so the row waits for reconcile.
        ("14".to_owned(), String::new()),
        ("12".to_owned(), "<217@loom.example.test>".to_owned()),
        // Another account's message, and one that isn't stored.
        ("13".to_owned(), String::new()),
        ("15".to_owned(), "<gone@example.test>".to_owned()),
    ];
    store.forget_expired(ADA, &moved, &["12".to_owned()]).unwrap();
    let left: Vec<i64> = database
        .read(|c| c.prepare("SELECT pk FROM messages ORDER BY pk")?.query_map([], |r| r.get(0))?.collect())
        .unwrap();
    assert_eq!(left, [2, 3, 4]);
    let gone: Option<i64> =
        database.read(|c| c.query_row("SELECT gone_at FROM message_identity WHERE key=1", [], |r| r.get(0))).unwrap();
    assert_eq!(gone, Some(1_793_620_800));
}

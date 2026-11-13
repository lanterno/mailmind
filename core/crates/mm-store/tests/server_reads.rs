//! What the Rust server reads through the store: the database's counts, the config file and the page's build, each
//! opened only once `Outside` lets it.

#![allow(clippy::unwrap_used)]

use std::path::Path;
use std::sync::{Arc, Mutex};

use mm_accounts::ports::ConfigFile as _;
use mm_kernel::errors::Fault;
use mm_kernel::seams::{Outside, Tripped};
use mm_mailbox::ports::{StoredLists, StoredMail, ViewCounts};
use mm_store::{ConfigFile, Database, DbKey, PageFiles, Store};
use serde_json::{Value, json};

/// Lets everything through but what is named `refused`, and remembers what it was asked.
#[derive(Debug, Default)]
struct Watching {
    refused: Option<&'static str>,
    asked: Mutex<Vec<String>>,
}

impl Outside for Watching {
    fn open(&self, path: &Path) -> Result<(), Tripped> {
        let name = path.file_name().unwrap().to_string_lossy().into_owned();
        self.asked.lock().unwrap().push(name.clone());
        if self.refused == Some(name.as_str()) { Err(Tripped(name)) } else { Ok(()) }
    }

    fn reach(&self, _host: &str) -> Result<(), Tripped> {
        Ok(())
    }

    fn run(&self, _program: &str) -> Result<(), Tripped> {
        Ok(())
    }
}

fn key() -> DbKey {
    DbKey::from_bytes([0x5e; 32])
}

const ROWS: &str = "
CREATE TABLE sync_state (account TEXT, folder TEXT);
CREATE TABLE snoozes (message_key INTEGER, folder TEXT);
CREATE TABLE muted (account TEXT, thread_id TEXT);
CREATE TABLE followups (account TEXT, thread_id TEXT, state TEXT, kind TEXT);
INSERT INTO messages (account, kind, folder, uid, thread_id, seen, message_key, pending_removal) VALUES
  ('ada@example.test', 'imap', 'INBOX', 1, 't1', 0, 1, NULL),
  ('ada@example.test', 'imap', 'INBOX', 2, 't1', 1, 2, NULL),
  ('ada@example.test', 'imap', 'INBOX', 3, 't2', 0, 3, 1),
  ('ada@example.test', 'imap', 'Archive', 4, 't3', 1, 4, NULL),
  ('bea@example.test', 'imap', 'INBOX', 1, 't9', 0, 9, NULL);
INSERT INTO sync_state VALUES ('ada@example.test', 'INBOX'), ('ada@example.test', 'Junk');
INSERT INTO snoozes VALUES (1, 'INBOX'), (3, 'INBOX'), (9, 'INBOX');
INSERT INTO muted VALUES ('ada@example.test', 't1'), ('ada@example.test', 'gone');
INSERT INTO followups VALUES ('ada@example.test', 't1', 'due', 'reply'), ('ada@example.test', 't3', 'due', 'replylater'),
  ('ada@example.test', 't3', 'done', 'replylater'), ('ada@example.test', 'gone', 'due', 'reply');
";

#[test]
fn the_rail_counts_what_is_stored_and_not_on_its_way_out() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("mailmind.db");
    Store::open(&path, &key()).unwrap().create_list_tables().unwrap();
    let outside = Arc::new(Watching::default());
    let database = Database::new(path, key(), outside.clone());
    database.read(|c| c.execute_batch(ROWS)).unwrap();

    let mut counts = database.stored_folder_counts("ada@example.test").unwrap();
    counts.sort();
    assert_eq!(
        counts,
        [("Archive".to_owned(), Some(0), 1), ("INBOX".to_owned(), Some(1), 2), ("Junk".to_owned(), Some(0), 0)]
    );
    assert_eq!(
        database.view_counts(&["ada@example.test".to_owned()]).unwrap(),
        ViewCounts { snoozed: 1, muted: 1, waiting: 1, replylater: 1 }
    );
    let both = ["ada@example.test".to_owned(), "bea@example.test".to_owned()];
    assert_eq!(database.view_counts(&both).unwrap().snoozed, 2);
    assert!(outside.asked.lock().unwrap().iter().all(|n| n == "mailmind.db"));
}

const GOING: &str = "
CREATE TABLE sync_state (account TEXT, folder TEXT);
CREATE TABLE snoozes (message_key INTEGER, folder TEXT);
CREATE TABLE muted (account TEXT, thread_id TEXT);
CREATE TABLE followups (account TEXT, thread_id TEXT, state TEXT, kind TEXT);
INSERT INTO messages (account, kind, folder, uid, thread_id, ts, message_key, pending_removal) VALUES
  ('ada@example.test', 'imap', 'INBOX', 1, 't1', 100, 1, NULL),
  ('ada@example.test', 'imap', 'INBOX', 2, 't1', 200, 2, 1),
  ('ada@example.test', 'imap', 'INBOX', 3, 't2', 300, 3, 1);
INSERT INTO muted VALUES ('ada@example.test', 't1'), ('ada@example.test', 't2');
";

/// Muted shows a thread by its latest message not on its way out, and its badge counts one only while it has such a
/// message: the latest copy of `t1` is going, and all of `t2` is.
#[test]
fn a_muted_thread_shows_its_latest_message_not_on_its_way_out() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("mailmind.db");
    Store::open(&path, &key()).unwrap().create_list_tables().unwrap();
    let database = Database::new(path, key(), Arc::new(Watching::default()));
    database.read(|c| c.execute_batch(GOING)).unwrap();

    let ada = ["ada@example.test".to_owned()];
    let listed: Vec<(Value, Value)> =
        database.muted(&ada).unwrap().iter().map(|r| (r["uid"].clone(), r["thread_count"].clone())).collect();
    assert_eq!(listed, [(json!(1), json!(1))]);
    assert_eq!(database.view_counts(&ada).unwrap().muted, 1);
}

#[test]
fn a_database_is_opened_only_with_its_key_and_once_outside_lets_it() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("mailmind.db");
    Store::open(&path, &key()).unwrap().create_list_tables().unwrap();
    let wrong = Database::new(path.clone(), DbKey::from_bytes([0x42; 32]), Arc::new(Watching::default()));
    assert_eq!(wrong.read(|c| c.execute_batch("SELECT 1")).unwrap_err(), Fault::new("KeyDoesNotFit"));
    let guarded = Watching { refused: Some("mailmind.db"), ..Watching::default() };
    let refused = Database::new(path, key(), Arc::new(guarded));
    assert_eq!(refused.read(|c| c.execute_batch("SELECT 1")).unwrap_err(), Fault::new("Tripped"));
    let missing = Database::new(dir.path().join("none.db"), key(), Arc::new(Watching::default()));
    assert_eq!(missing.read(|c| c.execute_batch("SELECT 1")).unwrap_err(), Fault::new("StorageError"));
}

#[test]
fn the_config_file_is_read_when_it_changes_and_written_whole() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("mailmind.config.json");
    let file = ConfigFile::new(path.clone(), Arc::new(Watching::default()));
    assert_eq!(Value::Object(file.read().unwrap()), json!({"accounts": []}));

    std::fs::write(&path, r#"{"accounts": [{"email": "ada@example.test", "alias": "Adá"}], "z": 1, "a": 2}"#).unwrap();
    let mut raw = file.read().unwrap();
    assert_eq!(raw.keys().collect::<Vec<_>>(), ["accounts", "z", "a"], "keys stay in the order they came");
    raw.insert("signature_default".into(), json!("— Ada"));
    file.write(&raw).unwrap();
    let text = std::fs::read_to_string(&path).unwrap();
    assert_eq!(
        text,
        "{\n  \"accounts\": [\n    {\n      \"email\": \"ada@example.test\",\n      \"alias\": \"Adá\"\n    }\n  ],\n  \
         \"z\": 1,\n  \"a\": 2,\n  \"signature_default\": \"— Ada\"\n}"
    );
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(std::fs::metadata(&path).unwrap().permissions().mode() & 0o777, 0o600);
    }
    let leftovers: Vec<_> = std::fs::read_dir(dir.path()).unwrap().filter_map(Result::ok).collect();
    assert_eq!(leftovers.len(), 1, "no temporary file is left beside it");

    std::fs::write(&path, r#"{"accounts": [], "edited": true}"#).unwrap();
    assert_eq!(file.read().unwrap()["edited"], true);
    std::fs::write(&path, "[]").unwrap();
    assert_eq!(file.read().unwrap_err(), Fault::new("UnreadableConfig"));
    let mut shapeless = serde_json::Map::new();
    shapeless.insert("accounts".into(), json!({}));
    assert_eq!(file.write(&shapeless).unwrap_err(), Fault::new("BadConfigShape"));
}

#[test]
fn the_page_is_its_index_and_the_files_under_assets() {
    let dir = tempfile::tempdir().unwrap();
    let dist = dir.path().join("dist");
    std::fs::create_dir_all(dist.join("assets/fonts")).unwrap();
    let files = PageFiles::new(dist.clone(), Arc::new(Watching::default()));
    assert!(!files.is_built());
    assert_eq!(files.index().unwrap(), None);

    std::fs::write(dist.join("index.html"), "<meta name=\"csrf\" content=\"__CSRF_TOKEN__\">\r\n<p>\r").unwrap();
    std::fs::write(dist.join("assets/app-1.js"), "1").unwrap();
    std::fs::write(dist.join("assets/fonts/a.woff2"), "2").unwrap();
    std::fs::write(dist.join("assets/LICENSE.txt"), "3").unwrap();
    std::fs::write(dir.path().join("outside.js"), "4").unwrap();
    #[cfg(unix)]
    std::os::unix::fs::symlink(dir.path().join("outside.js"), dist.join("assets/linked.js")).unwrap();

    assert!(files.is_built());
    assert_eq!(files.index().unwrap().unwrap(), "<meta name=\"csrf\" content=\"__CSRF_TOKEN__\">\n<p>\n");
    let served = |name: &str| name.ends_with(".js") || name.ends_with(".woff2");
    let listed: Vec<String> = files.files(&served).unwrap().into_iter().map(|f| f.relative).collect();
    assert_eq!(listed, ["assets/app-1.js", "assets/fonts/a.woff2", "assets/linked.js", "index.html"]);
    assert_eq!(files.file("assets/app-1.js").unwrap(), b"1");

    let (path, stamp) = files.asset(&["fonts", "a.woff2"]).unwrap().unwrap();
    assert_eq!((files.asset_body(&path).unwrap(), stamp.size), (b"2".to_vec(), 1));
    assert!(files.asset(&["nope.js"]).unwrap().is_none());
    #[cfg(unix)]
    assert!(files.asset(&["linked.js"]).unwrap().is_none(), "a link out of the build is no file of it");

    let guarded = Watching { refused: Some("index.html"), ..Watching::default() };
    let refused = PageFiles::new(dist, Arc::new(guarded));
    assert_eq!(refused.index().unwrap_err(), Fault::new("Tripped"));
}

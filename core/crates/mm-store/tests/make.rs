//! A database the core makes: the Python app's schema at the newest version, byte for byte as
//! `tests/golden/schema_v<N>.sql` holds it, with what the Python app's `init` seeds, its pragmas, and readable by its
//! owner only. Every database here is a new one in a temp directory.

#![allow(clippy::unwrap_used)]

use std::path::Path;
use std::sync::Arc;

use mm_kernel::seams::{Clock, Moment, Outside, Tripped};
use mm_store::migrate::{self, BASE, NotReady, Ready};
use mm_store::schema::VERSION;
use mm_store::{Database, DbKey, Store};
use rusqlite::Connection;

#[derive(Debug)]
struct Frozen;

impl Clock for Frozen {
    fn now(&self) -> Moment {
        Moment::from_micros(1_793_620_800_000_000)
    }
}

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
struct Closed;

impl Outside for Closed {
    fn open(&self, _path: &Path) -> Result<(), Tripped> {
        Err(Tripped("closed".into()))
    }

    fn reach(&self, _host: &str) -> Result<(), Tripped> {
        Err(Tripped("closed".into()))
    }

    fn run(&self, _program: &str) -> Result<(), Tripped> {
        Err(Tripped("closed".into()))
    }
}

const KEY: [u8; 32] = [0x5e; 32];

fn database(path: &Path) -> Database {
    Database::new(path.to_path_buf(), DbKey::from_bytes(KEY), Arc::new(Open))
}

/// A bare keyed connection, as the Python app's `database.keyed` opens one.
fn keyed(path: &Path) -> Connection {
    let c = Connection::open(path).unwrap();
    c.execute_batch(&format!("PRAGMA key = \"x'{}'\";", "5e".repeat(32))).unwrap();
    c
}

/// The schema as `tests/unit/platform/test_schema.py`'s `dump` writes it: every table, index, trigger and view in name
/// order, each statement's whitespace collapsed, then the version.
fn dump(path: &Path) -> String {
    let c = keyed(path);
    let mut statement = c
        .prepare(
            "SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' \
             ORDER BY type, name",
        )
        .unwrap();
    let rows: Vec<(String, String, String)> =
        statement.query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?))).unwrap().map(Result::unwrap).collect();
    let version: i64 = c.query_row("PRAGMA user_version", [], |r| r.get(0)).unwrap();
    let out: Vec<String> = rows
        .iter()
        .map(|(kind, name, sql)| {
            format!("-- {kind} {name}\n{};\n", sql.split_whitespace().collect::<Vec<_>>().join(" "))
        })
        .collect();
    format!("{}\nPRAGMA user_version={version};\n", out.join("\n"))
}

fn golden() -> String {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join(format!("../../../tests/golden/schema_v{VERSION}.sql"));
    std::fs::read_to_string(path).unwrap()
}

#[test]
fn a_new_database_has_the_python_apps_schema_byte_for_byte() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("mailmind.db");
    assert_eq!(database(&path).ready(&Frozen), Ok(Ready::Made));
    assert_eq!(dump(&path), golden());
}

#[test]
fn it_holds_what_the_python_apps_init_seeds_and_nothing_else() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("mailmind.db");
    database(&path).ready(&Frozen).unwrap();
    let c = keyed(&path);
    let names: Vec<String> = c
        .prepare("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
        .unwrap()
        .query_map([], |r| r.get(0))
        .unwrap()
        .map(Result::unwrap)
        .collect();
    let held: Vec<(String, i64)> = names
        .iter()
        .map(|name| (name.clone(), c.query_row(&format!("SELECT count(*) FROM \"{name}\""), [], |r| r.get(0)).unwrap()))
        .filter(|(_, n)| *n > 0)
        .collect();
    // msg_fts's own bookkeeping, as a new index holds it, and the sequence migration 39 began.
    let expected = [("msg_fts_config", 1), ("msg_fts_data", 2), ("sqlite_sequence", 1)];
    assert_eq!(held, expected.map(|(name, n)| (name.to_owned(), n)));
    let sequence: (String, i64) =
        c.query_row("SELECT name, seq FROM sqlite_sequence", [], |r| Ok((r.get(0)?, r.get(1)?))).unwrap();
    assert_eq!(sequence, ("mail_commands".to_owned(), 0));
    let config: (String, i64) =
        c.query_row("SELECT k, v FROM msg_fts_config", [], |r| Ok((r.get(0)?, r.get(1)?))).unwrap();
    assert_eq!(config, ("version".to_owned(), 4));
}

#[test]
fn it_is_its_owners_alone_with_its_log_and_writes_ahead() {
    use std::os::unix::fs::PermissionsExt;
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("mailmind.db");
    database(&path).ready(&Frozen).unwrap();
    let mode: String = keyed(&path).query_row("PRAGMA journal_mode", [], |r| r.get(0)).unwrap();
    assert_eq!(mode, "wal");
    for each in ["mailmind.db", "mailmind.db-wal", "mailmind.db-shm"] {
        let found = dir.path().join(each);
        if found.exists() {
            assert_eq!(std::fs::metadata(&found).unwrap().permissions().mode() & 0o777, 0o600, "{each}");
        }
    }
}

#[test]
fn one_made_before_mailmind_saw_to_it_is_taken_back() {
    use std::os::unix::fs::PermissionsExt;
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("mailmind.db");
    Store::open(&path, &DbKey::from_bytes(KEY)).unwrap().set_version(VERSION).unwrap();
    std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o644)).unwrap();
    assert_eq!(database(&path).ready(&Frozen), Ok(Ready::AsItWas));
    assert_eq!(std::fs::metadata(&path).unwrap().permissions().mode() & 0o777, 0o600);
}

#[test]
fn a_database_at_the_newest_version_is_left_alone() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("mailmind.db");
    database(&path).ready(&Frozen).unwrap();
    keyed(&path)
        .execute("INSERT INTO muted(account, thread_id, muted_at) VALUES ('ada@example.test', 't1', 'now')", [])
        .unwrap();
    let before = dump(&path);
    assert_eq!(database(&path).ready(&Frozen), Ok(Ready::AsItWas));
    assert_eq!(dump(&path), before);
    let kept: String = keyed(&path).query_row("SELECT thread_id FROM muted", [], |r| r.get(0)).unwrap();
    assert_eq!(kept, "t1");
}

#[test]
fn an_empty_file_is_a_new_database() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("mailmind.db");
    std::fs::write(&path, b"").unwrap();
    assert_eq!(database(&path).ready(&Frozen), Ok(Ready::Made));
    assert_eq!(dump(&path), golden());
}

#[test]
fn one_older_than_the_base_is_the_python_apps_to_upgrade() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("mailmind.db");
    Store::open(&path, &DbKey::from_bytes(KEY)).unwrap().set_version(BASE - 1).unwrap();
    assert_eq!(database(&path).ready(&Frozen), Err(NotReady::Older(BASE - 1)));
    let version: i64 = keyed(&path).query_row("PRAGMA user_version", [], |r| r.get(0)).unwrap();
    assert_eq!(version, BASE - 1);
}

#[test]
fn tables_with_no_version_were_made_outside_the_migrations_and_are_left_as_they_are() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("mailmind.db");
    Store::open(&path, &DbKey::from_bytes(KEY)).unwrap().create_list_tables().unwrap();
    let before = dump(&path);
    assert_eq!(database(&path).ready(&Frozen), Err(NotReady::Older(0)));
    assert_eq!(dump(&path), before);
}

#[test]
fn one_newer_than_the_core_is_one_it_is_behind() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("mailmind.db");
    Store::open(&path, &DbKey::from_bytes(KEY)).unwrap().set_version(VERSION + 1).unwrap();
    assert_eq!(database(&path).ready(&Frozen), Err(NotReady::Newer(VERSION + 1)));
}

#[test]
fn another_key_opens_nothing_and_changes_nothing() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("mailmind.db");
    database(&path).ready(&Frozen).unwrap();
    let other = Database::new(path.clone(), DbKey::from_bytes([0x11; 32]), Arc::new(Open));
    assert_eq!(other.ready(&Frozen), Err(NotReady::KeyDoesNotFit));
    assert_eq!(dump(&path), golden());
}

#[test]
fn a_tripwire_refuses_before_a_file_is_made() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("mailmind.db");
    let closed = Database::new(path.clone(), DbKey::from_bytes(KEY), Arc::new(Closed));
    assert_eq!(closed.ready(&Frozen), Err(NotReady::Tripped));
    assert!(!path.exists());
}

#[test]
fn sqlite_older_than_the_search_index_needs_is_told_apart() {
    assert!(migrate::sqlite_fits(rusqlite::version()));
    assert!(migrate::sqlite_fits("3.43.0") && migrate::sqlite_fits("3.54.1") && migrate::sqlite_fits("4.0"));
    assert!(!migrate::sqlite_fits("3.42.9") && !migrate::sqlite_fits("2.99") && !migrate::sqlite_fits("three"));
}

//! The core's migrations from 52 on: each pending one runs, in order, in a transaction of its own that sets its
//! version, so one that fails leaves the database as it was before it and the next start runs it again. The
//! migrations here exist only in these tests; every database is a new one in a temp directory.

#![allow(clippy::unwrap_used)]

use std::path::Path;
use std::sync::Arc;

use mm_kernel::seams::{Outside, Tripped};
use mm_store::migrate::{BASE, MIGRATIONS, Migration, NotReady, Ready};
use mm_store::schema::VERSION;
use mm_store::{Database, DbKey, Store};
use rusqlite::Connection;

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

const KEY: [u8; 32] = [0x5e; 32];

fn database(path: &Path) -> Database {
    Database::new(path.to_path_buf(), DbKey::from_bytes(KEY), Arc::new(Open))
}

fn keyed(path: &Path) -> Connection {
    let c = Connection::open(path).unwrap();
    c.execute_batch(&format!("PRAGMA key = \"x'{}'\";", "5e".repeat(32))).unwrap();
    c
}

fn version(path: &Path) -> i64 {
    keyed(path).query_row("PRAGMA user_version", [], |r| r.get(0)).unwrap()
}

/// The versions the migrations here have run, in the order they ran.
fn ran(path: &Path) -> Vec<i64> {
    let c = keyed(path);
    let there: i64 = c.query_row("SELECT count(*) FROM sqlite_master WHERE name = 'ran'", [], |r| r.get(0)).unwrap();
    if there == 0 {
        return Vec::new();
    }
    c.prepare("SELECT v FROM ran ORDER BY rowid")
        .unwrap()
        .query_map([], |r| r.get(0))
        .unwrap()
        .map(Result::unwrap)
        .collect()
}

fn schema(path: &Path) -> Vec<(String, String)> {
    keyed(path)
        .prepare("SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type, name")
        .unwrap()
        .query_map([], |r| Ok((r.get(0)?, r.get(1)?)))
        .unwrap()
        .map(Result::unwrap)
        .collect()
}

fn note(c: &Connection, version: i64) -> rusqlite::Result<()> {
    c.execute_batch("CREATE TABLE IF NOT EXISTS ran(v INTEGER NOT NULL)")?;
    c.execute("INSERT INTO ran(v) VALUES (?1)", [version]).map(|_| ())
}

fn m52(c: &Connection) -> rusqlite::Result<()> {
    note(c, 52)?;
    c.execute_batch("CREATE TABLE IF NOT EXISTS labels_seen(label TEXT PRIMARY KEY)")
}

fn m53(c: &Connection) -> rusqlite::Result<()> {
    note(c, 53)?;
    let has: i64 =
        c.query_row("SELECT count(*) FROM pragma_table_info('labels_seen') WHERE name = 'seen_at'", [], |r| r.get(0))?;
    if has == 0 {
        c.execute_batch("ALTER TABLE labels_seen ADD COLUMN seen_at INTEGER")?;
    }
    Ok(())
}

/// Migration 53 halfway: what it does, then the disk fills up.
fn m53_fails_halfway(c: &Connection) -> rusqlite::Result<()> {
    m53(c)?;
    Err(rusqlite::Error::InvalidQuery)
}

/// A migration that ends its own transaction, which a migration never does.
fn m53_commits(c: &Connection) -> rusqlite::Result<()> {
    m53(c)?;
    c.execute_batch("COMMIT")
}

const TWO: &[Migration] = &[Migration::new(52, m52), Migration::new(53, m53)];

/// A database the core made at the base, as it stands before any migration after it.
fn at_base(dir: &Path) -> std::path::PathBuf {
    let path = dir.join("mailmind.db");
    assert_eq!(database(&path).ready_with(&[]), Ok(Ready::Made));
    path
}

#[test]
fn each_pending_migration_runs_in_order_and_sets_its_version() {
    let dir = tempfile::tempdir().unwrap();
    let path = at_base(dir.path());
    assert_eq!(database(&path).ready_with(TWO), Ok(Ready::Upgraded { from: BASE, to: 53 }));
    assert_eq!(version(&path), 53);
    assert_eq!(ran(&path), vec![52, 53]);
}

#[test]
fn only_those_it_hasnt_run_run() {
    let dir = tempfile::tempdir().unwrap();
    let path = at_base(dir.path());
    assert_eq!(database(&path).ready_with(&TWO[..1]), Ok(Ready::Upgraded { from: BASE, to: 52 }));
    assert_eq!(database(&path).ready_with(TWO), Ok(Ready::Upgraded { from: 52, to: 53 }));
    assert_eq!(database(&path).ready_with(TWO), Ok(Ready::AsItWas));
    assert_eq!(ran(&path), vec![52, 53]);
}

#[test]
fn a_new_database_is_made_at_the_base_and_migrated_from_it() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("mailmind.db");
    assert_eq!(database(&path).ready_with(TWO), Ok(Ready::Made));
    assert_eq!(version(&path), 53);
    assert_eq!(ran(&path), vec![52, 53]);
}

#[test]
fn a_migration_that_fails_leaves_the_database_as_it_was_before_it_and_the_next_start_runs_it_again() {
    let dir = tempfile::tempdir().unwrap();
    let path = at_base(dir.path());
    let failing = [Migration::new(52, m52), Migration::new(53, m53_fails_halfway)];
    assert_eq!(database(&path).ready_with(&failing), Err(NotReady::Failed(53)));
    // 52 went whole, and nothing of 53 is left.
    assert_eq!(version(&path), 52);
    assert_eq!(ran(&path), vec![52]);
    let stopped = tempfile::tempdir().unwrap();
    let only_52 = at_base(stopped.path());
    database(&only_52).ready_with(&TWO[..1]).unwrap();
    assert_eq!(schema(&path), schema(&only_52));
    // Fixed, the next start runs it.
    assert_eq!(database(&path).ready_with(TWO), Ok(Ready::Upgraded { from: 52, to: 53 }));
    assert_eq!(ran(&path), vec![52, 53]);
}

#[test]
fn a_migration_that_ends_its_own_transaction_is_refused() {
    let dir = tempfile::tempdir().unwrap();
    let path = at_base(dir.path());
    let committing = [Migration::new(52, m52), Migration::new(53, m53_commits)];
    assert_eq!(database(&path).ready_with(&committing), Err(NotReady::Failed(53)));
    assert_eq!(version(&path), 52);
}

#[test]
fn a_database_newer_than_the_migrations_is_one_the_core_is_behind() {
    let dir = tempfile::tempdir().unwrap();
    let path = at_base(dir.path());
    database(&path).ready_with(TWO).unwrap();
    assert_eq!(database(&path).ready_with(&TWO[..1]), Err(NotReady::Newer(53)));
    assert_eq!(version(&path), 53);
}

#[test]
fn a_database_older_than_the_base_is_still_the_python_apps_to_upgrade() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("mailmind.db");
    Store::open(&path, &DbKey::from_bytes(KEY)).unwrap().set_version(BASE - 1).unwrap();
    assert_eq!(database(&path).ready_with(TWO), Err(NotReady::Older(BASE - 1)));
    assert_eq!(version(&path), BASE - 1);
}

#[test]
fn a_migration_is_idempotent_at_its_place_in_the_history() {
    // As the core's own must be: run again over what it left, it leaves the same.
    let dir = tempfile::tempdir().unwrap();
    let path = at_base(dir.path());
    database(&path).ready_with(TWO).unwrap();
    let once = schema(&path);
    let c = keyed(&path);
    c.execute_batch("BEGIN IMMEDIATE").unwrap();
    m53(&c).unwrap();
    c.execute_batch("COMMIT").unwrap();
    assert_eq!(schema(&path), once);
}

#[test]
fn the_cores_migrations_follow_the_base_one_version_each() {
    let versions: Vec<i64> = MIGRATIONS.iter().map(|m| m.version).collect();
    let expected: Vec<i64> = (BASE + 1..=BASE + versions.len() as i64).collect();
    assert_eq!(versions, expected);
    assert_eq!(VERSION, versions.last().copied().unwrap_or(BASE));
}

#[test]
fn each_of_the_cores_migrations_runs_twice_at_its_place_in_the_history_and_leaves_the_same() {
    for (n, migration) in MIGRATIONS.iter().enumerate() {
        let dir = tempfile::tempdir().unwrap();
        let path = at_base(dir.path());
        database(&path).ready_with(&MIGRATIONS[..=n]).unwrap();
        let once = schema(&path);
        let c = keyed(&path);
        c.execute_batch("BEGIN IMMEDIATE").unwrap();
        (migration.run)(&c).unwrap();
        c.execute_batch("COMMIT").unwrap();
        assert_eq!(schema(&path), once, "migration {}", migration.version);
    }
}

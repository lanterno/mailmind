//! The copy taken before an upgrade: in `backups/` beside the database, named for its version and the time, keyed
//! as the database is, its owner's alone, the newest 3 kept, and no upgrade without one. The migrations here exist
//! only in these tests; every database is a new one in a temp directory, holding fictional rows.

#![allow(clippy::unwrap_used)]

use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::{Duration, SystemTime};

use mm_kernel::seams::{Clock, Moment, Outside, Tripped};
use mm_store::migrate::{BASE, Migration, NotReady, Ready};
use mm_store::{Database, DbKey};
use rusqlite::Connection;

/// 2026-11-02 12:00:00 UTC.
#[derive(Debug)]
struct Frozen;

impl Clock for Frozen {
    fn now(&self) -> Moment {
        Moment::from_micros(1_793_620_800_000_000)
    }
}

const STAMP: &str = "20261102T120000Z";

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

/// A tripwire that guards the backups' folder.
#[derive(Debug)]
struct GuardsBackups;

impl Outside for GuardsBackups {
    fn open(&self, path: &Path) -> Result<(), Tripped> {
        if path.components().any(|part| part.as_os_str() == "backups") {
            return Err(Tripped("backups".into()));
        }
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

fn has_table(path: &Path, name: &str) -> bool {
    let n: i64 =
        keyed(path).query_row("SELECT count(*) FROM sqlite_master WHERE name = ?1", [name], |r| r.get(0)).unwrap();
    n > 0
}

fn m52(c: &Connection) -> rusqlite::Result<()> {
    c.execute_batch("CREATE TABLE IF NOT EXISTS labels_seen(label TEXT PRIMARY KEY)")
}

const ONE: &[Migration] = &[Migration::new(52, m52)];

/// A database the core made at the base, holding a muted thread.
fn at_base(dir: &Path) -> PathBuf {
    let path = dir.join("mailmind.db");
    assert_eq!(database(&path).ready_with(&Frozen, &[]), Ok(Ready::Made));
    keyed(&path)
        .execute("INSERT INTO muted(account, thread_id, muted_at) VALUES ('ada@example.test', 't1', 'now')", [])
        .unwrap();
    path
}

/// The copies in `dir`'s backups folder, by name.
fn copies(dir: &Path) -> Vec<String> {
    let Ok(entries) = fs::read_dir(dir.join("backups")) else {
        return Vec::new();
    };
    let mut names: Vec<String> = entries.map(|e| e.unwrap().file_name().into_string().unwrap()).collect();
    names.sort();
    names
}

fn mode(path: &Path) -> u32 {
    fs::metadata(path).unwrap().permissions().mode() & 0o777
}

/// A copy from an earlier upgrade, last changed `age` ago.
fn older_copy(dir: &Path, name: &str, age: Duration) {
    changed_at(dir, name, SystemTime::now() - age);
}

/// A copy from an earlier upgrade, last changed `at`.
fn changed_at(dir: &Path, name: &str, at: SystemTime) {
    let folder = dir.join("backups");
    fs::create_dir_all(&folder).unwrap();
    let file = fs::File::create(folder.join(name)).unwrap();
    file.set_modified(at).unwrap();
}

#[test]
fn an_upgrade_backs_the_database_up_first_keyed_and_its_owners_alone() {
    let dir = tempfile::tempdir().unwrap();
    let path = at_base(dir.path());
    assert_eq!(database(&path).ready_with(&Frozen, ONE), Ok(Ready::Upgraded { from: BASE, to: 52 }));
    let name = format!("mailmind-v{BASE}-{STAMP}.db");
    assert_eq!(copies(dir.path()), vec![name.clone()]);
    let copy = dir.path().join("backups").join(&name);
    // The database as it was before the upgrade, opened with its key.
    assert_eq!(version(&copy), BASE);
    assert!(!has_table(&copy, "labels_seen") && has_table(&path, "labels_seen"));
    let kept: String = keyed(&copy).query_row("SELECT thread_id FROM muted", [], |r| r.get(0)).unwrap();
    assert_eq!(kept, "t1");
    assert_eq!(mode(&copy), 0o600);
    assert_eq!(mode(&dir.path().join("backups")), 0o700);
    // Another key opens nothing in it.
    let other = Connection::open(&copy).unwrap();
    other.execute_batch(&format!("PRAGMA key = \"x'{}'\";", "11".repeat(32))).unwrap();
    assert!(other.query_row("SELECT count(*) FROM sqlite_master", [], |r| r.get::<_, i64>(0)).is_err());
}

#[test]
fn what_the_log_holds_and_no_checkpoint_has_moved_is_in_the_copy() {
    let dir = tempfile::tempdir().unwrap();
    let path = at_base(dir.path());
    // A writer that keeps its log: the row is in the -wal file, not yet in the database's own pages.
    let writer = keyed(&path);
    writer.execute_batch("PRAGMA wal_autocheckpoint = 0;").unwrap();
    writer
        .execute("INSERT INTO muted(account, thread_id, muted_at) VALUES ('ada@example.test', 't2', 'now')", [])
        .unwrap();
    database(&path).ready_with(&Frozen, ONE).unwrap();
    let copy = dir.path().join("backups").join(format!("mailmind-v{BASE}-{STAMP}.db"));
    let n: i64 = keyed(&copy).query_row("SELECT count(*) FROM muted", [], |r| r.get(0)).unwrap();
    assert_eq!(n, 2);
    drop(writer);
}

#[test]
fn a_new_database_and_one_at_the_newest_version_take_no_copy() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("mailmind.db");
    assert_eq!(database(&path).ready_with(&Frozen, ONE), Ok(Ready::Made));
    assert_eq!(database(&path).ready_with(&Frozen, ONE), Ok(Ready::AsItWas));
    assert!(!dir.path().join("backups").exists());
}

#[test]
fn only_the_newest_three_are_kept() {
    let dir = tempfile::tempdir().unwrap();
    let path = at_base(dir.path());
    let day = Duration::from_secs(86_400);
    older_copy(dir.path(), "mailmind-v48-20261020T090000Z.db", 3 * day);
    older_copy(dir.path(), "mailmind-v49-20261025T090000Z.db", 2 * day);
    older_copy(dir.path(), "mailmind-v50-20261030T090000Z.db", day);
    // Not a copy: left be.
    older_copy(dir.path(), "notes.txt", 4 * day);
    database(&path).ready_with(&Frozen, ONE).unwrap();
    assert_eq!(
        copies(dir.path()),
        vec![
            "mailmind-v49-20261025T090000Z.db".to_owned(),
            "mailmind-v50-20261030T090000Z.db".to_owned(),
            format!("mailmind-v{BASE}-{STAMP}.db"),
            "notes.txt".to_owned(),
        ]
    );
}

#[test]
fn a_copy_named_in_the_same_second_as_another_is_numbered() {
    let dir = tempfile::tempdir().unwrap();
    let path = at_base(dir.path());
    let stem = format!("mailmind-v{BASE}-{STAMP}");
    older_copy(dir.path(), &format!("{stem}.db"), Duration::from_secs(1));
    database(&path).ready_with(&Frozen, ONE).unwrap();
    assert_eq!(copies(dir.path()), vec![format!("{stem}-1.db"), format!("{stem}.db")]);
    assert_eq!(version(&dir.path().join("backups").join(format!("{stem}-1.db"))), BASE);
}

#[test]
fn of_copies_changed_in_the_same_moment_the_lowest_number_goes_first() {
    let dir = tempfile::tempdir().unwrap();
    let path = at_base(dir.path());
    let stem = format!("mailmind-v{BASE}-{STAMP}");
    let then = SystemTime::now() - Duration::from_secs(3600);
    // By name, -10 would come before -2.
    for n in [10, 2, 9] {
        changed_at(dir.path(), &format!("{stem}-{n}.db"), then);
    }
    database(&path).ready_with(&Frozen, ONE).unwrap();
    assert_eq!(copies(dir.path()), vec![format!("{stem}-10.db"), format!("{stem}-9.db"), format!("{stem}.db")]);
}

#[test]
fn without_a_copy_nothing_is_migrated() {
    let dir = tempfile::tempdir().unwrap();
    let path = at_base(dir.path());
    // Where the folder would go, a file.
    fs::write(dir.path().join("backups"), b"").unwrap();
    assert_eq!(database(&path).ready_with(&Frozen, ONE), Err(NotReady::NoBackup));
    assert_eq!(version(&path), BASE);
    assert!(!has_table(&path, "labels_seen"));
}

#[test]
fn a_tripwire_on_the_backups_refuses_the_upgrade() {
    let dir = tempfile::tempdir().unwrap();
    let path = at_base(dir.path());
    let guarded = Database::new(path.clone(), DbKey::from_bytes(KEY), Arc::new(GuardsBackups));
    assert_eq!(guarded.ready_with(&Frozen, ONE), Err(NotReady::NoBackup));
    assert_eq!(version(&path), BASE);
    assert!(!dir.path().join("backups").exists());
}

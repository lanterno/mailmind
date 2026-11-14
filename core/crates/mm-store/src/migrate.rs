//! Making the database and bringing it to the newest schema (`mailmind.platform.db.migrate`). Over a data directory
//! with none, the core makes one at the version the Python app's last migration brought its own to (`BASE`), with the
//! schema and the pragmas the Python app's `init` and `database.connect` give it: keyed as every database is, and
//! readable by its owner only. Then it runs its own migrations, from 52 on (`MIGRATIONS`).
//!
//! The base is the Python app's schema at 51 (`schema_v51.sql`), made in one write transaction that sets the version
//! too. Each migration after it runs in a transaction of its own (BEGIN IMMEDIATE) that sets its version as well, so
//! one that fails leaves the database exactly as it was before it, at the version before it, and the next start runs
//! it again. A database older than the base is the Python app's to upgrade, and one newer than the core's newest
//! migration is one the core is behind.
//!
//! Migrations are append-only, as the Python app's are: the version is the on-disk contract, so one is never
//! renumbered or removed, and a retired feature leaves a migration that does nothing behind. Each is idempotent at its
//! place in the history, since a start that stopped halfway runs it again.

use std::fs;
use std::path::{Path, PathBuf};

use mm_kernel::Refusal;
use rusqlite::Connection;

use crate::Store;
use crate::db::Database;

/// The version a database the core makes starts at: the Python app's last migration before the core took them over.
pub const BASE: i64 = 51;

/// The Python app's schema at `BASE`, each statement as its database stores it.
const BASE_SCHEMA: &str = include_str!("schema_v51.sql");

/// What the Python app's `init` leaves in a new database beside its schema: migration 39 carried an empty queue over
/// into `mail_commands`, which started its sequence at 0.
const BASE_SEEDS: &str = "INSERT INTO sqlite_sequence(name, seq) VALUES ('mail_commands', 0);";

/// The oldest SQLite the schema runs on: the search index's `contentless_delete` (migration 38) came in 3.43.
pub const SQLITE_NEEDED: (u32, u32) = (3, 43);

/// One migration after the base: what it does to the schema, and what the upgrade check (`db check-upgrade`) is to
/// make of what it does to the rows. Those are the Python app's `RETIRED`, `REBUILT`, `FILLED` and `TRIMMED`, each said
/// here of the one migration.
#[derive(Clone, Copy, Debug)]
pub struct Migration {
    /// The version it brings a database to: one after the migration before it's.
    pub version: i64,
    /// What it does, inside the write transaction that sets its version. It never commits or rolls back, and it is
    /// idempotent at its place in the history: run again over what it left, it leaves the same.
    pub run: fn(&Connection) -> rusqlite::Result<()>,
    /// The tables it ends, which the check reports as retired rather than lost.
    pub retires: &'static [&'static str],
    /// The tables it builds afresh, whose count may change: the check reports them as rebuilt.
    pub rebuilds: &'static [&'static str],
    /// The tables it may add rows to, carrying over what it ends: their growth is carried over, their shrinking still
    /// a loss.
    pub fills: &'static [&'static str],
    /// The rows it takes on purpose: a table, and the query that counts how many it will take from it, asked
    /// beforehand. The check passes exactly that many gone, and fails one more.
    pub trims: &'static [(&'static str, &'static str)],
}

impl Migration {
    /// A migration to `version` that declares nothing to the check: `Migration { retires: &["notes"],
    /// ..Migration::new(52, m52) }` for one that does.
    pub const fn new(version: i64, run: fn(&Connection) -> rusqlite::Result<()>) -> Migration {
        Migration { version, run, retires: &[], rebuilds: &[], fills: &[], trims: &[] }
    }
}

/// The core's migrations after the base, in order. Only ever grows at the end, and keeps step with the Python app's
/// until the Python app goes: its migration 52 is this list's first.
pub const MIGRATIONS: &[Migration] = &[];

/// What became of the database as it was made ready.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Ready {
    /// There was none, and now there is, at the newest version.
    Made,
    /// It was at `from`, and its migrations since have brought it to `to`.
    Upgraded { from: i64, to: i64 },
    /// It was at the newest version already, and was left alone.
    AsItWas,
}

/// Why the database isn't ready. Nothing here names a path or a value in the database.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum NotReady {
    /// This build's SQLite, older than `SQLITE_NEEDED`. Nothing was changed.
    SqliteTooOld(String),
    /// At a version before `BASE`, or at none with tables in it (0), made outside the migrations: the Python app's
    /// migrations upgrade it.
    Older(i64),
    /// At a version after the core's newest: the core is behind it.
    Newer(i64),
    /// The key opens nothing in the file.
    KeyDoesNotFit,
    /// The tripwire refused the file.
    Tripped,
    /// The file couldn't be made, opened, read or written.
    Storage,
    /// The migration to this version failed: the database is as it was before it, and the next start tries it again.
    Failed(i64),
}

/// Whether SQLite `version` (as `sqlite_version()` says it, `3.54.1`) is `SQLITE_NEEDED` or newer.
pub fn sqlite_fits(version: &str) -> bool {
    let mut parts = version.split('.').map(|part| part.parse::<u32>().ok());
    match (parts.next().flatten(), parts.next().flatten()) {
        (Some(major), Some(minor)) => (major, minor) >= SQLITE_NEEDED,
        _ => false,
    }
}

impl Database {
    /// Make the database if there is none, and run every migration it hasn't run yet. Safe on every start: a
    /// database at the newest version is left alone.
    pub fn ready(&self) -> Result<Ready, NotReady> {
        self.ready_with(MIGRATIONS)
    }

    /// `ready`, with `migrations` in place of the core's own (a test's).
    pub fn ready_with(&self, migrations: &[Migration]) -> Result<Ready, NotReady> {
        let found = rusqlite::version();
        if !sqlite_fits(found) {
            return Err(NotReady::SqliteTooOld(found.to_owned()));
        }
        let path = self.path();
        self.outside().open(path).map_err(|_| NotReady::Tripped)?;
        keep_private(self, path);
        make_private(path)?;
        let store = Store::open(path, self.key()).map_err(|refusal| match refusal {
            Refusal::KeyDoesNotFit => NotReady::KeyDoesNotFit,
            _ => NotReady::Storage,
        })?;
        let c = &store.conn;
        let mut made = false;
        if version(c)? == 0 {
            if tables(c)? > 0 {
                return Err(NotReady::Older(0));
            }
            // Read again inside the write: another process may have made it while this one waited for the lock.
            made = in_transaction(c, |c| {
                let empty = version_of(c)? == 0 && tables_of(c)? == 0;
                if empty {
                    c.execute_batch(BASE_SCHEMA)?;
                    c.execute_batch(BASE_SEEDS)?;
                    set_version(c, BASE)?;
                }
                Ok(empty)
            })
            .map_err(|_| NotReady::Storage)?;
        }
        let from = version(c)?;
        let newest = migrations.last().map_or(BASE, |last| last.version);
        if from < BASE {
            return Err(NotReady::Older(from));
        }
        if from > newest {
            return Err(NotReady::Newer(from));
        }
        let pending: Vec<&Migration> = migrations.iter().filter(|m| m.version > from).collect();
        if pending.is_empty() {
            return Ok(if made { Ready::Made } else { Ready::AsItWas });
        }
        for migration in pending {
            run(c, migration).map_err(|_| NotReady::Failed(migration.version))?;
        }
        Ok(if made { Ready::Made } else { Ready::Upgraded { from, to: newest } })
    }
}

/// One migration in its own write transaction, which sets its version too. Read inside the write: another process
/// may have run it while this one waited for the lock.
fn run(c: &Connection, migration: &Migration) -> rusqlite::Result<()> {
    in_transaction(c, |c| {
        if version_of(c)? >= migration.version {
            return Ok(());
        }
        (migration.run)(c)?;
        if c.is_autocommit() {
            // It ended the transaction itself, so what it did can no longer go with its version.
            return Err(rusqlite::Error::InvalidQuery);
        }
        set_version(c, migration.version)
    })
}

/// `work` in one write transaction (BEGIN IMMEDIATE), committed when it returns and rolled back when it fails.
fn in_transaction<T>(c: &Connection, work: impl FnOnce(&Connection) -> rusqlite::Result<T>) -> rusqlite::Result<T> {
    c.execute_batch("BEGIN IMMEDIATE")?;
    match work(c).and_then(|value| c.execute_batch("COMMIT").map(|()| value)) {
        Ok(value) => Ok(value),
        Err(e) => {
            if !c.is_autocommit() {
                let _ = c.execute_batch("ROLLBACK");
            }
            Err(e)
        }
    }
}

fn version_of(c: &Connection) -> rusqlite::Result<i64> {
    c.query_row("PRAGMA user_version", [], |r| r.get(0))
}

fn tables_of(c: &Connection) -> rusqlite::Result<i64> {
    c.query_row("SELECT count(*) FROM sqlite_master", [], |r| r.get(0))
}

fn version(c: &Connection) -> Result<i64, NotReady> {
    version_of(c).map_err(|_| NotReady::Storage)
}

fn tables(c: &Connection) -> Result<i64, NotReady> {
    tables_of(c).map_err(|_| NotReady::Storage)
}

fn set_version(c: &Connection, version: i64) -> rusqlite::Result<()> {
    c.execute_batch(&format!("PRAGMA user_version = {version}"))
}

/// The file beside the database SQLite names with `suffix` (`-wal`, `-shm`).
pub(crate) fn beside(path: &Path, suffix: &str) -> PathBuf {
    let mut name = path.as_os_str().to_owned();
    name.push(suffix);
    PathBuf::from(name)
}

/// The database holds every message, so only its owner may read it, nor its log and index, which SQLite makes with the
/// database's own mode only when they are new (`database.keep_private`). One made before MailMind saw to it is taken
/// back; what isn't there, or can't be changed, is left to SQLite to say.
fn keep_private(database: &Database, path: &Path) {
    for each in [path.to_path_buf(), beside(path, "-wal"), beside(path, "-shm")] {
        if database.outside().open(&each).is_ok() {
            #[cfg(unix)]
            let _ = fs::set_permissions(&each, std::os::unix::fs::PermissionsExt::from_mode(0o600));
        }
    }
}

/// The database's file, made readable by its owner only if it isn't there.
fn make_private(path: &Path) -> Result<(), NotReady> {
    let mut options = fs::OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    std::os::unix::fs::OpenOptionsExt::mode(&mut options, 0o600);
    match options.open(path) {
        Ok(_) => Ok(()),
        Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => Ok(()),
        Err(_) => Err(NotReady::Storage),
    }
}

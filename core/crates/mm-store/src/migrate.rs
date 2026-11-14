//! Making the database (`mailmind.platform.db.migrate`). Over a data directory with none, the core makes one at the
//! version the Python app's last migration brought its own to (`BASE`), with the schema and the pragmas the Python
//! app's `init` and `database.connect` give it: keyed as every database is, and readable by its owner only.
//!
//! The base is the Python app's schema at 51 (`schema_v51.sql`), made in one write transaction that sets the version
//! too, so a database is either made whole or not at all. A database older than the base is the Python app's to
//! upgrade, and one newer than the core is one it is behind.

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

/// What became of the database as it was made ready.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Ready {
    /// There was none, and now there is, at the newest version.
    Made,
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
    /// Make the database if there is none, at the newest version, and otherwise check it is at that version. Safe on
    /// every start: a database at the newest version is left alone.
    pub fn ready(&self) -> Result<Ready, NotReady> {
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
            })?;
        }
        match version(c)? {
            v if v < BASE => Err(NotReady::Older(v)),
            v if v > crate::schema::VERSION => Err(NotReady::Newer(v)),
            _ if made => Ok(Ready::Made),
            _ => Ok(Ready::AsItWas),
        }
    }
}

/// `work` in one write transaction (BEGIN IMMEDIATE), committed when it returns and rolled back when it fails.
fn in_transaction<T>(c: &Connection, work: impl FnOnce(&Connection) -> rusqlite::Result<T>) -> Result<T, NotReady> {
    c.execute_batch("BEGIN IMMEDIATE").map_err(|_| NotReady::Storage)?;
    match work(c).and_then(|value| c.execute_batch("COMMIT").map(|()| value)) {
        Ok(value) => Ok(value),
        Err(_) => {
            let _ = c.execute_batch("ROLLBACK");
            Err(NotReady::Storage)
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

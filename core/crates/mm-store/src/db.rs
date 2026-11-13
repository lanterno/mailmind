//! The Python app's database as the Rust server reads it: a file it doesn't make, opened through `Outside`, a keyed
//! connection for each block of work (the Python app's `UnitOfWork`), so no connection outlives what it was for.

use std::fmt;
use std::path::PathBuf;
use std::sync::Arc;

use mm_kernel::errors::Fault;
use mm_kernel::seams::Outside;
use rusqlite::{Connection, OpenFlags};

use crate::{DbKey, Store};

const TRIPPED: Fault = Fault::new("Tripped");
const STORAGE: Fault = Fault::new("StorageError");
const KEY: Fault = Fault::new("KeyDoesNotFit");

/// The database at a path, opened afresh for each block of work.
pub struct Database {
    path: PathBuf,
    key: DbKey,
    outside: Arc<dyn Outside>,
}

impl fmt::Debug for Database {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("Database(..)")
    }
}

impl Database {
    pub fn new(path: PathBuf, key: DbKey, outside: Arc<dyn Outside>) -> Database {
        Database { path, key, outside }
    }

    /// Run `work` on a keyed connection to the database, which must already be there. What SQLite says goes nowhere
    /// but the fault's class: its messages name tables and columns, and a value could follow.
    pub fn read<T>(&self, work: impl FnOnce(&Connection) -> rusqlite::Result<T>) -> Result<T, Fault> {
        self.outside.open(&self.path).map_err(|_| TRIPPED)?;
        let flags = OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_NO_MUTEX;
        let conn = Connection::open_with_flags(&self.path, flags).map_err(|_| STORAGE)?;
        let store = Store::keyed(conn, &self.key, true).map_err(|refusal| match refusal {
            mm_kernel::Refusal::KeyDoesNotFit => KEY,
            _ => STORAGE,
        })?;
        work(&store.conn).map_err(|_| STORAGE)
    }

    /// The key it opens with, for a copy made with it (an export).
    pub(crate) fn key(&self) -> &DbKey {
        &self.key
    }

    /// Run `work` holding the database's one write lock (`UnitOfWork.write`): BEGIN IMMEDIATE, committed when `work`
    /// returns, rolled back when it fails. A refusal `work` decides on goes in its value, so what it read stays true
    /// until it has decided.
    pub fn write<T>(&self, work: impl FnOnce(&Connection) -> rusqlite::Result<T>) -> Result<T, Fault> {
        self.read(|c| {
            c.execute_batch("BEGIN IMMEDIATE")?;
            match work(c) {
                Ok(value) => c.execute_batch("COMMIT").map(|()| value),
                Err(e) => {
                    let _ = c.execute_batch("ROLLBACK");
                    Err(e)
                }
            }
        })
    }
}

//! The encrypted database, and the only place the core opens a file.
//!
//! A connection is keyed before anything reads it, with SQLCipher 4's defaults and the same raw 32-byte key the
//! Python app uses, so each can open what the other wrote. One `Store` is one connection.
//!
//! Step 0 reads only what a message list shows. The tables it creates for fixtures are the Python app's own
//! (`schema::LIST_TABLES`, copied from a version-51 database), so what is measured here is the real table.
//!
//! For the Rust server, the store implements the context crates' ports: `Database` (`db`) is the Python app's
//! database, the SQL each context reads is in a module of its name (`mailbox`), `config` is
//! `mailmind.config.json` and `page` the page's build. Every file goes through `Outside` before it is opened.

// What the Assistant reads: what calls cost.
mod assistant;
// What Compose keeps: the outbox and the drafts' journal, the drafts it tracks, and the snippets.
pub mod compose;
pub mod config;
// What Curation reads of each stored inbox.
mod curation_inbox;
pub mod db;
// What the Document Hub (Discovery's) reads: the attachments stored.
mod documents;
// What an export would hold, and a snapshot of the database as a download.
pub mod export;
mod key;
// A Gmail account's labels.
mod labels;
mod mailbox;
mod mailbox_sync;
pub mod page;
// The page's own state, and what the app says of itself.
pub mod places;
mod rule_book;
pub mod schema;
// What Discovery reads of a sender as a message opens.
mod senders;
mod sync_state;
// Discovery's, Curation's and Attention's SQL, and the write block, documents and undo stack they share.
pub mod blocks;
pub mod copies;
pub mod curation;
pub mod discovery;
pub mod zone_file;

use std::collections::HashMap;
use std::fmt;
use std::path::Path;

use mm_kernel::{MessageKey, Refusal};
use rusqlite::{Connection, ToSql, params};

pub use config::ConfigFile;
pub use db::Database;
pub use key::DbKey;
pub use page::PageFiles;

/// One open, keyed database.
pub struct Store {
    conn: Connection,
}

impl fmt::Debug for Store {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("Store(..)")
    }
}

/// What a message list shows of one message, as the store holds it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ListRow {
    pub key: MessageKey,
    pub from_name: Option<String>,
    pub from_addr: Option<String>,
    pub subject: Option<String>,
    pub snippet: Option<String>,
    pub ts: Option<i64>,
    pub seen: bool,
    pub flagged: bool,
    pub has_attach: bool,
}

/// A message to add, for fixtures and tests.
#[derive(Clone, Debug)]
pub struct NewMessage {
    pub account: String,
    pub folder: String,
    pub uid: i64,
    pub message_id: String,
    pub from_name: String,
    pub from_addr: String,
    pub subject: String,
    pub snippet: String,
    pub ts: i64,
    pub seen: bool,
    pub flagged: bool,
    pub has_attach: bool,
}

impl Store {
    /// The database at `path`, made if it is not there, keyed with `key`.
    pub fn open(path: &Path, key: &DbKey) -> Result<Store, Refusal> {
        let conn = Connection::open(path).map_err(storage)?;
        Store::keyed(conn, key, true)
    }

    /// A database that lives only in memory, for tests.
    pub fn open_in_memory(key: &DbKey) -> Result<Store, Refusal> {
        let conn = Connection::open_in_memory().map_err(storage)?;
        Store::keyed(conn, key, false)
    }

    fn keyed(conn: Connection, key: &DbKey, on_disk: bool) -> Result<Store, Refusal> {
        conn.execute_batch(&key.pragma()).map_err(storage)?;
        // A wrong key fails on the first read of the file, as "not a database": SQLCipher cannot tell a wrong key
        // from a file that is not one. Any other failure there (a lock, a read, a journal this build cannot open)
        // is the storage's, and saying the key is wrong would send the person after the wrong cause.
        match conn.query_row("SELECT count(*) FROM sqlite_master", [], |r| r.get::<_, i64>(0)) {
            Ok(_) => {}
            Err(rusqlite::Error::SqliteFailure(e, _)) if e.code == rusqlite::ErrorCode::NotADatabase => {
                return Err(Refusal::KeyDoesNotFit);
            }
            Err(e) => return Err(storage(e)),
        }
        if on_disk {
            journal(&conn)?;
        }
        conn.execute_batch("PRAGMA foreign_keys=ON; PRAGMA temp_store=MEMORY; PRAGMA busy_timeout=60000;")
            .map_err(storage)?;
        Ok(Store { conn })
    }

    /// The SQLCipher build this store runs, such as `4.6.1 community`.
    pub fn cipher_version(&self) -> Result<String, Refusal> {
        self.conn.query_row("PRAGMA cipher_version", [], |r| r.get(0)).map_err(storage)
    }

    /// Make the tables a message list reads, if they are not there. For fixtures and tests: the real schema
    /// comes from the migrations.
    pub fn create_list_tables(&self) -> Result<(), Refusal> {
        self.conn.execute_batch(schema::LIST_TABLES).map_err(storage)
    }

    /// The messages of `folder` in `accounts`, newest first, as row numbers. The numbers stay inside the core.
    pub fn list_order(&self, accounts: &[String], folder: &str) -> Result<Vec<i64>, Refusal> {
        if accounts.is_empty() {
            return Ok(Vec::new());
        }
        let mut statement = self.conn.prepare_cached(&list_order_sql(accounts.len())).map_err(storage)?;
        let mut values: Vec<&dyn ToSql> = accounts.iter().map(|a| a as &dyn ToSql).collect();
        values.push(&folder);
        let rows = statement.query_map(values.as_slice(), |r| r.get::<_, i64>(0)).map_err(storage)?;
        rows.collect::<Result<Vec<_>, _>>().map_err(storage)
    }

    /// What SQLite does to answer `list_order`, one step a line, as `EXPLAIN QUERY PLAN` says it. For tests: the
    /// order must come from an index (m51's `idx_msg_listed`), never from sorting the folder.
    pub fn list_order_plan(&self, accounts: &[String], folder: &str) -> Result<Vec<String>, Refusal> {
        if accounts.is_empty() {
            return Ok(Vec::new());
        }
        let sql = format!("EXPLAIN QUERY PLAN {}", list_order_sql(accounts.len()));
        let mut statement = self.conn.prepare(&sql).map_err(storage)?;
        let mut values: Vec<&dyn ToSql> = accounts.iter().map(|a| a as &dyn ToSql).collect();
        values.push(&folder);
        let rows = statement.query_map(values.as_slice(), |r| r.get::<_, String>(3)).map_err(storage)?;
        rows.collect::<Result<Vec<_>, _>>().map_err(storage)
    }

    /// The rows with these row numbers, in the order asked. `None` where a row is gone.
    pub fn rows(&self, pks: &[i64]) -> Result<Vec<Option<ListRow>>, Refusal> {
        if pks.is_empty() {
            return Ok(Vec::new());
        }
        let sql = format!(
            "SELECT pk, message_key, from_name, from_addr, subject, snippet, ts, seen, flagged, has_attach \
             FROM messages WHERE pk IN ({})",
            placeholders(pks.len())
        );
        let mut statement = self.conn.prepare_cached(&sql).map_err(storage)?;
        let mut found = HashMap::with_capacity(pks.len());
        let mut rows = statement.query(rusqlite::params_from_iter(pks.iter())).map_err(storage)?;
        while let Some(r) = rows.next().map_err(storage)? {
            let pk: i64 = r.get(0).map_err(storage)?;
            let key: Option<i64> = r.get(1).map_err(storage)?;
            let key = key.ok_or_else(|| Refusal::storage("a listed message has no key"))?;
            found.insert(
                pk,
                ListRow {
                    key: MessageKey(key),
                    from_name: r.get(2).map_err(storage)?,
                    from_addr: r.get(3).map_err(storage)?,
                    subject: r.get(4).map_err(storage)?,
                    snippet: r.get(5).map_err(storage)?,
                    ts: r.get(6).map_err(storage)?,
                    seen: flag(r.get(7).map_err(storage)?),
                    flagged: flag(r.get(8).map_err(storage)?),
                    has_attach: flag(r.get(9).map_err(storage)?),
                },
            );
        }
        Ok(pks.iter().map(|pk| found.remove(pk)).collect())
    }

    /// Add messages in one transaction, each with its identity. For fixtures and tests.
    pub fn add_messages(&mut self, messages: impl IntoIterator<Item = NewMessage>) -> Result<u32, Refusal> {
        let tx = self.conn.transaction().map_err(storage)?;
        let mut added = 0u32;
        {
            let mut identity = tx
                .prepare_cached("INSERT INTO message_identity(account, identity) VALUES (?1, ?2)")
                .map_err(storage)?;
            let mut message = tx
                .prepare_cached(
                    "INSERT INTO messages(account, kind, folder, uid, message_id, from_name, from_addr, subject, \
                     snippet, ts, seen, flagged, has_attach, message_key) \
                     VALUES (?1, 'imap', ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13)",
                )
                .map_err(storage)?;
            for m in messages {
                identity.execute(params![m.account, m.message_id]).map_err(storage)?;
                let key = tx.last_insert_rowid();
                message
                    .execute(params![
                        m.account,
                        m.folder,
                        m.uid,
                        m.message_id,
                        m.from_name,
                        m.from_addr,
                        m.subject,
                        m.snippet,
                        m.ts,
                        m.seen,
                        m.flagged,
                        m.has_attach,
                        key
                    ])
                    .map_err(storage)?;
                added += 1;
            }
        }
        tx.commit().map_err(storage)?;
        Ok(added)
    }

    /// Bytes SQLite holds in memory right now, across every connection in this process.
    #[allow(unsafe_code)]
    pub fn sqlite_memory_used() -> i64 {
        // SAFETY: sqlite3_memory_used takes no arguments and only reads SQLite's own counter.
        unsafe { rusqlite::ffi::sqlite3_memory_used() }
    }
}

#[cfg(not(target_arch = "wasm32"))]
fn journal(conn: &Connection) -> Result<(), Refusal> {
    // As the Python app runs it: write-ahead logging, and each commit on the disk itself before it returns.
    let mode: String = conn.query_row("PRAGMA journal_mode=WAL", [], |r| r.get(0)).map_err(storage)?;
    if !mode.eq_ignore_ascii_case("wal") {
        return Err(Refusal::storage("the database refused write-ahead logging"));
    }
    conn.execute_batch("PRAGMA synchronous=FULL; PRAGMA fullfsync=ON;").map_err(storage)
}

#[cfg(target_arch = "wasm32")]
fn journal(conn: &Connection) -> Result<(), Refusal> {
    // The browser's file system has no shared memory, so no write-ahead log: a rollback journal instead.
    conn.execute_batch("PRAGMA synchronous=FULL;").map_err(storage)
}

// The order a list shows: newest first, a tie by the newer row. `idx_msg_listed` holds exactly this order.
fn list_order_sql(accounts: usize) -> String {
    format!(
        "SELECT pk FROM messages WHERE account IN ({}) AND folder = ? AND pending_removal IS NULL \
         ORDER BY ts DESC, pk DESC",
        placeholders(accounts)
    )
}

fn placeholders(n: usize) -> String {
    let mut s = String::with_capacity(n * 2);
    for i in 0..n {
        if i > 0 {
            s.push(',');
        }
        s.push('?');
    }
    s
}

fn flag(value: Option<i64>) -> bool {
    value.unwrap_or(0) != 0
}

fn storage(e: rusqlite::Error) -> Refusal {
    // SQLite's messages name tables, columns and constraints, never the values in them.
    Refusal::storage(e.to_string())
}

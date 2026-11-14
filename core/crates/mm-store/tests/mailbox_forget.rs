//! What the forget round lets go of, as the store does it: keys whose last copy went before the cutoff, with their
//! assessments and snoozes, and changes done before theirs; never a key gone since, one with no time gone, or a
//! change still open or waiting for a person.

#![allow(clippy::unwrap_used)]

use std::path::Path;
use std::sync::Arc;

use mm_kernel::seams::{Outside, Tripped};
use mm_mailbox::forgetting::{ForgetStore, Forgotten};
use mm_store::{Database, DbKey, Store};

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

/// The tables the round touches beyond the list's, as the newest schema golden has them.
const TABLES: &str = "
CREATE TABLE mail_commands(id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, message_id TEXT, src_folder TEXT NOT NULL, dest TEXT,
  params TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt_at INTEGER NOT NULL DEFAULT 0, applied INTEGER NOT NULL DEFAULT 0, begun INTEGER NOT NULL DEFAULT 0,
  outcome TEXT, last_error TEXT, created_at TEXT NOT NULL, done_at TEXT);
CREATE TABLE assessments( message_key INTEGER PRIMARY KEY REFERENCES message_identity(key) ON DELETE CASCADE,
  summary TEXT, importance INTEGER, category TEXT, action TEXT, model TEXT, created_at TEXT, served_by TEXT,
  prompt_version INTEGER, outcome TEXT);
CREATE TABLE snoozes( message_key INTEGER NOT NULL REFERENCES message_identity(key) ON DELETE CASCADE,
  folder TEXT NOT NULL, until INTEGER NOT NULL, PRIMARY KEY(message_key, folder));
";

/// 2026-10-03 12:00:00 UTC: thirty days before 2026-11-02 12:00:00.
const CUTOFF: i64 = 1_791_028_800;

fn database(dir: &Path) -> Database {
    let key = DbKey::from_bytes([0x5e; 32]);
    let path = dir.join("mailmind.db");
    Store::open(&path, &key).unwrap().create_list_tables().unwrap();
    let database = Database::new(path, key, Arc::new(Open));
    database.read(|c| c.execute_batch(TABLES)).unwrap();
    database
}

fn ints(database: &Database, sql: &str) -> Vec<i64> {
    database.read(|c| c.prepare(sql)?.query_map([], |r| r.get(0))?.collect::<rusqlite::Result<Vec<i64>>>()).unwrap()
}

#[test]
fn keys_gone_before_the_cutoff_go_with_what_hangs_on_them() {
    let dir = tempfile::tempdir().unwrap();
    let database = database(dir.path());
    database
        .write(|c| {
            for (key, gone_at) in [(1, Some(CUTOFF - 1)), (2, Some(CUTOFF)), (3, Some(CUTOFF + 86_400)), (4, None)] {
                c.execute(
                    "INSERT INTO message_identity (key, account, identity, gone_at) VALUES (?, 'ada@example.test', ?, ?)",
                    rusqlite::params![key, format!("<m{key}@example.test>"), gone_at],
                )?;
                c.execute("INSERT INTO assessments (message_key, summary) VALUES (?, 'A receipt.')", [key])?;
                c.execute("INSERT INTO snoozes VALUES (?, 'INBOX', 1793620800)", [key])?;
            }
            Ok(())
        })
        .unwrap();
    let forgotten = database.forget(CUTOFF, "2026-10-03T12:00:00+00:00").unwrap();
    assert_eq!(forgotten, Forgotten { keys: 1, commands: 0 });
    assert_eq!(ints(&database, "SELECT key FROM message_identity ORDER BY key"), [2, 3, 4]);
    assert_eq!(ints(&database, "SELECT message_key FROM assessments ORDER BY 1"), [2, 3, 4]);
    assert_eq!(ints(&database, "SELECT message_key FROM snoozes ORDER BY 1"), [2, 3, 4]);
}

#[test]
fn changes_done_before_the_cutoff_go_and_any_still_open_stay() {
    let dir = tempfile::tempdir().unwrap();
    let database = database(dir.path());
    database
        .write(|c| {
            for (id, state, done_at) in [
                (1, "done", Some("2026-10-03T11:59:59.999999+00:00")),
                (2, "done", Some("2026-10-03T12:00:00+00:00")),
                (3, "done", Some("2026-10-03T12:00:00.000001+00:00")),
                (4, "done", Some("2026-11-01T09:00:00+00:00")),
                (5, "error", None),
                (6, "queued", None),
                (7, "running", None),
            ] {
                c.execute(
                    "INSERT INTO mail_commands (id, account, idempotency_key, kind, message_id, src_folder, params, \
                     state, created_at, done_at) VALUES (?, 'ada@example.test', ?, 'trash', '<m@example.test>', \
                     'INBOX', '{}', ?, '2026-09-01T12:00:00+00:00', ?)",
                    rusqlite::params![id, format!("trash:{id}"), state, done_at],
                )?;
            }
            Ok(())
        })
        .unwrap();
    let forgotten = database.forget(CUTOFF, "2026-10-03T12:00:00+00:00").unwrap();
    assert_eq!(forgotten, Forgotten { keys: 0, commands: 1 });
    assert_eq!(ints(&database, "SELECT id FROM mail_commands ORDER BY id"), [2, 3, 4, 5, 6, 7]);
}

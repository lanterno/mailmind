//! A database file that isn't whole, isn't encrypted or isn't a database (cut short by a full disk, a page damaged,
//! a stranger's file, a folder where the file should be) is refused when the app opens it, never a panic, and the
//! refusal leaves its bytes as they were: what a person might still recover isn't made worse by trying to open it.
//! Every file here is made in a temp directory.

#![allow(clippy::unwrap_used)]

use std::path::Path;
use std::sync::Arc;

use mm_kernel::seams::{Clock, Moment, Outside, Tripped};
use mm_store::migrate::{NotReady, Ready};
use mm_store::{Database, DbKey};
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

const KEY: [u8; 32] = [0x5e; 32];

fn database(path: &Path) -> Database {
    Database::new(path.to_path_buf(), DbKey::from_bytes(KEY), Arc::new(Open))
}

/// A whole database at the newest version, with something of a person's in it, checkpointed into its one file.
fn made(path: &Path) -> Vec<u8> {
    assert_eq!(database(path).ready(&Frozen), Ok(Ready::Made));
    let c = Connection::open(path).unwrap();
    c.execute_batch(&format!("PRAGMA key = \"x'{}'\";", "5e".repeat(32))).unwrap();
    for n in 0..200 {
        c.execute(
            "INSERT INTO muted(account, thread_id, muted_at) VALUES ('ada@example.test', ?1, 'now')",
            [format!("thread-{n}-{}", "x".repeat(200))],
        )
        .unwrap();
    }
    c.execute_batch("PRAGMA wal_checkpoint(TRUNCATE);").unwrap();
    drop(c);
    std::fs::read(path).unwrap()
}

/// Opening `path` is refused, as one of `allowed`, and its bytes are as they were.
fn refused_and_untouched(path: &Path, allowed: &[NotReady]) {
    let before = std::fs::read(path).ok();
    let said = database(path).ready(&Frozen);
    match &said {
        Err(why) => assert!(allowed.contains(why), "refused as {why:?}, not one of {allowed:?}"),
        Ok(ready) => panic!("a damaged file was opened as {ready:?}"),
    }
    assert_eq!(std::fs::read(path).ok(), before, "the refusal changed the file");
}

#[test]
fn bytes_that_are_no_database_are_refused_and_left_as_they_were() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("mailmind.db");
    let mut state = 0x2545_f491_4f6c_dd1d_u64;
    let noise: Vec<u8> = (0..16_384)
        .map(|_| {
            state ^= state << 13;
            state ^= state >> 7;
            state ^= state << 17;
            state as u8
        })
        .collect();
    std::fs::write(&path, &noise).unwrap();
    refused_and_untouched(&path, &[NotReady::KeyDoesNotFit, NotReady::Storage]);
}

#[test]
fn a_database_that_was_never_encrypted_is_refused_and_left_as_it_was() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("mailmind.db");
    let c = Connection::open(&path).unwrap();
    c.execute_batch(
        "CREATE TABLE muted(account TEXT, thread_id TEXT); INSERT INTO muted VALUES ('ada@example.test', 't');",
    )
    .unwrap();
    drop(c);
    refused_and_untouched(&path, &[NotReady::KeyDoesNotFit, NotReady::Storage]);
}

#[test]
fn a_database_cut_short_is_refused_and_left_as_it_was() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("mailmind.db");
    let whole = made(&path);
    for keep in [whole.len() / 2, 4096 + 17, 1000, 100] {
        std::fs::write(&path, &whole[..keep]).unwrap();
        let _ = std::fs::remove_file(dir.path().join("mailmind.db-wal"));
        let _ = std::fs::remove_file(dir.path().join("mailmind.db-shm"));
        refused_and_untouched(&path, &[NotReady::KeyDoesNotFit, NotReady::Storage]);
    }
}

#[test]
fn a_damaged_first_page_is_refused_and_a_damaged_later_one_never_panics_or_writes() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("mailmind.db");
    let whole = made(&path);
    assert!(whole.len() > 4096 * 8, "the database has pages past its first: {} bytes", whole.len());

    let mut first = whole.clone();
    first[100..140].iter_mut().for_each(|b| *b ^= 0xa5);
    std::fs::write(&path, &first).unwrap();
    refused_and_untouched(&path, &[NotReady::KeyDoesNotFit, NotReady::Storage]);

    // A page no opening reads may go unnoticed until something reads it; either way the file is left as it was.
    let mut later = whole;
    let page = later.len() - 4096 * 2;
    later[page + 50..page + 90].iter_mut().for_each(|b| *b ^= 0x5a);
    std::fs::write(&path, &later).unwrap();
    let said = database(&path).ready(&Frozen);
    assert!(matches!(said, Ok(Ready::AsItWas) | Err(NotReady::Storage | NotReady::KeyDoesNotFit)), "{said:?}");
    assert_eq!(std::fs::read(&path).unwrap(), later, "opening it changed the file");
}

#[test]
fn a_folder_where_the_database_should_be_is_refused() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("mailmind.db");
    std::fs::create_dir(&path).unwrap();
    assert_eq!(database(&path).ready(&Frozen), Err(NotReady::Storage));
    assert!(path.is_dir());
}

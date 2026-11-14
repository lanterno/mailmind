//! The database's version: what the Python app's migrations set, read back, and none before there is a database.

#![allow(clippy::unwrap_used)]

use std::path::Path;
use std::sync::Arc;

use mm_kernel::seams::{Outside, Tripped};
use mm_store::schema::VERSION;
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

#[test]
fn there_is_no_version_before_there_is_a_database() {
    let dir = tempfile::tempdir().unwrap();
    let database = Database::new(dir.path().join("mailmind.db"), DbKey::from_bytes(KEY), Arc::new(Open));
    assert_eq!(database.version(), Ok(None));
    // Asking makes no file.
    assert!(!dir.path().join("mailmind.db").exists());
}

#[test]
fn the_version_the_migrations_set_is_read_back() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("mailmind.db");
    let store = Store::open(&path, &DbKey::from_bytes(KEY)).unwrap();
    assert_eq!(Database::new(path.clone(), DbKey::from_bytes(KEY), Arc::new(Open)).version(), Ok(Some(0)));
    store.set_version(VERSION).unwrap();
    drop(store);
    assert_eq!(Database::new(path, DbKey::from_bytes(KEY), Arc::new(Open)).version(), Ok(Some(VERSION)));
}

#[test]
fn another_key_is_told_apart_from_a_database_that_cant_be_read() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("mailmind.db");
    Store::open(&path, &DbKey::from_bytes(KEY)).unwrap().set_version(VERSION).unwrap();
    let other = Database::new(path, DbKey::from_bytes([0x11; 32]), Arc::new(Open));
    assert_eq!(other.version().unwrap_err().class, "KeyDoesNotFit");
}

#[test]
fn a_tripwire_refuses_before_the_file_is_looked_at() {
    let dir = tempfile::tempdir().unwrap();
    let database = Database::new(dir.path().join("mailmind.db"), DbKey::from_bytes(KEY), Arc::new(Closed));
    assert_eq!(database.version().unwrap_err().class, "Tripped");
}

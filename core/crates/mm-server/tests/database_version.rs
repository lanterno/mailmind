//! The Rust server starts only over a database at the version it reads, and otherwise says what to do.

#![allow(clippy::unwrap_used)]

use std::path::Path;
use std::sync::Arc;

use mm_kernel::seams::{Clock, Keychain, KeychainProblem, Moment, Outside, Secret, Tripped};
use mm_server::environment::{Environment, Settings};
use mm_server::{App, Seams};
use mm_store::schema::VERSION;
use mm_store::{DbKey, Store};

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
struct Empty;

impl Keychain for Empty {
    fn find(&self, _item: &str) -> Result<Option<Secret>, KeychainProblem> {
        Ok(None)
    }
}

/// What starting the server over `home` says: nothing when it starts.
fn start(home: &Path) -> Result<(), String> {
    std::fs::write(home.join("mailmind.config.json"), r#"{"accounts": []}"#).unwrap();
    let environment = Environment::from_pairs([("MAILMIND_DB_KEY", "5e".repeat(32).as_str())]);
    let settings = Settings { home: home.to_path_buf() };
    let seams = Seams { clock: Arc::new(Frozen), outside: Arc::new(Open), keychain: Arc::new(Empty) };
    App::assemble(&environment, &settings, home.join("dist"), 8721, &seams).map(|_| ())
}

/// A database at `version` in `home`, keyed with `key`.
fn database(home: &Path, key: [u8; 32], version: i64) {
    Store::open(&home.join("mailmind.db"), &DbKey::from_bytes(key)).unwrap().set_version(version).unwrap();
}

#[test]
fn it_starts_over_a_database_at_its_version() {
    let home = tempfile::tempdir().unwrap();
    database(home.path(), [0x5e; 32], VERSION);
    assert_eq!(start(home.path()), Ok(()));
}

#[test]
fn with_no_database_it_says_to_start_the_python_app_to_make_one_and_makes_none() {
    let home = tempfile::tempdir().unwrap();
    let said = start(home.path()).unwrap_err();
    assert!(said.contains("no database yet") && said.contains("start the Python app"), "{said}");
    assert!(!home.path().join("mailmind.db").exists());
}

#[test]
fn over_an_older_database_it_says_to_upgrade_it_with_the_python_app() {
    let home = tempfile::tempdir().unwrap();
    database(home.path(), [0x5e; 32], VERSION - 1);
    let said = start(home.path()).unwrap_err();
    assert!(said.contains(&format!("version {}", VERSION - 1)) && said.contains("to upgrade it"), "{said}");
}

#[test]
fn over_a_database_the_python_app_never_upgraded_it_says_to_upgrade_it() {
    // A file with tables but no version, as a fixture made outside the migrations is.
    let home = tempfile::tempdir().unwrap();
    database(home.path(), [0x5e; 32], 0);
    let said = start(home.path()).unwrap_err();
    assert!(said.contains("version 0") && said.contains("to upgrade it"), "{said}");
}

#[test]
fn over_a_newer_database_it_says_it_is_behind_and_opens_nothing() {
    let home = tempfile::tempdir().unwrap();
    database(home.path(), [0x5e; 32], VERSION + 1);
    let said = start(home.path()).unwrap_err();
    assert!(said.contains(&format!("version {}", VERSION + 1)) && said.contains("must catch up"), "{said}");
}

#[test]
fn a_key_that_doesnt_open_it_is_told_apart() {
    let home = tempfile::tempdir().unwrap();
    database(home.path(), [0x11; 32], VERSION);
    assert_eq!(start(home.path()), Err("the database key doesn't open the database".to_owned()));
}

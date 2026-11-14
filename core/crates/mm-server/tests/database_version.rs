//! The Rust server makes its database over a data directory with none, with a key when there is none either, starts
//! over one at the version it reads, and otherwise says what to do.

#![allow(clippy::unwrap_used)]

use std::collections::HashMap;
use std::path::Path;
use std::sync::{Arc, Mutex};

use mm_kernel::seams::{Clock, Keychain, KeychainProblem, Moment, Outside, Secret, Tripped};
use mm_server::environment::{Environment, Settings};
use mm_server::real::Sanitizing;
use mm_server::{App, Seams};
use mm_store::schema::VERSION;
use mm_store::{Database, DbKey, Store};

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

/// A Keychain in memory, which keeps what it is given unless told not to, and answers nothing when told not to.
#[derive(Debug, Default)]
struct Held {
    items: Mutex<HashMap<String, String>>,
    keeps_nothing: bool,
    answers_nothing: bool,
}

impl Held {
    fn holding(&self, item: &str) -> Option<String> {
        self.items.lock().unwrap().get(item).cloned()
    }
}

impl Keychain for Held {
    fn find(&self, item: &str) -> Result<Option<Secret>, KeychainProblem> {
        if self.answers_nothing {
            return Err(KeychainProblem);
        }
        Ok(self.holding(item).map(Secret::new))
    }

    fn put(&self, item: &str, secret: &Secret) -> Result<(), KeychainProblem> {
        if self.keeps_nothing || self.answers_nothing {
            return Err(KeychainProblem);
        }
        self.items.lock().unwrap().insert(item.to_owned(), secret.expose().to_owned());
        Ok(())
    }
}

/// What starting the server over `home` says, with `environment` and `keychain`: nothing when it starts.
fn start_with(home: &Path, environment: &Environment, keychain: Arc<Held>) -> Result<(), String> {
    std::fs::write(home.join("mailmind.config.json"), r#"{"accounts": []}"#).unwrap();
    let settings = Settings { home: home.to_path_buf() };
    let seams = Seams { clock: Arc::new(Frozen), outside: Arc::new(Open), keychain, sanitizer: Arc::new(Sanitizing) };
    App::assemble(environment, &settings, home.join("dist"), 8721, &seams).map(|_| ())
}

/// What starting the server over `home` with MAILMIND_DB_KEY set says.
fn start(home: &Path) -> Result<(), String> {
    let environment = Environment::from_pairs([("MAILMIND_DB_KEY", "5e".repeat(32).as_str())]);
    start_with(home, &environment, Arc::new(Held::default()))
}

/// A database at `version` in `home`, keyed with `key`.
fn database(home: &Path, key: [u8; 32], version: i64) {
    Store::open(&home.join("mailmind.db"), &DbKey::from_bytes(key)).unwrap().set_version(version).unwrap();
}

/// The version of the database in `home`, opened with `key` given as hex.
fn version_with(home: &Path, key: &str) -> Option<i64> {
    let key = DbKey::from_hex(key).unwrap();
    Database::new(home.join("mailmind.db"), key, Arc::new(Open)).version().unwrap()
}

#[test]
fn it_starts_over_a_database_at_its_version() {
    let home = tempfile::tempdir().unwrap();
    database(home.path(), [0x5e; 32], VERSION);
    assert_eq!(start(home.path()), Ok(()));
}

#[test]
fn with_no_database_it_makes_one_at_its_version_its_owners_alone() {
    use std::os::unix::fs::PermissionsExt;
    let home = tempfile::tempdir().unwrap();
    assert_eq!(start(home.path()), Ok(()));
    assert_eq!(version_with(home.path(), &"5e".repeat(32)), Some(VERSION));
    let mode = std::fs::metadata(home.path().join("mailmind.db")).unwrap().permissions().mode();
    assert_eq!(mode & 0o777, 0o600);
    // And it starts over the one it made.
    assert_eq!(start(home.path()), Ok(()));
}

#[test]
fn with_no_key_and_no_database_it_makes_both_the_key_kept_in_the_keychain_first() {
    let home = tempfile::tempdir().unwrap();
    let keychain = Arc::new(Held::default());
    assert_eq!(start_with(home.path(), &Environment::default(), keychain.clone()), Ok(()));
    let kept = keychain.holding("database-key").unwrap();
    assert_eq!(kept.len(), 64);
    assert_eq!(version_with(home.path(), &kept), Some(VERSION));
    // The next start finds it there, and makes no other.
    assert_eq!(start_with(home.path(), &Environment::default(), keychain.clone()), Ok(()));
    assert_eq!(keychain.holding("database-key"), Some(kept));
}

#[test]
fn a_database_with_no_key_gets_none_made_for_it() {
    let home = tempfile::tempdir().unwrap();
    database(home.path(), [0x5e; 32], VERSION);
    let keychain = Arc::new(Held::default());
    let said = start_with(home.path(), &Environment::default(), keychain.clone()).unwrap_err();
    assert!(said.contains("no database key"), "{said}");
    assert_eq!(keychain.holding("database-key"), None);
}

#[test]
fn a_keychain_that_wont_keep_a_new_key_makes_no_database() {
    let home = tempfile::tempdir().unwrap();
    let keychain = Arc::new(Held { keeps_nothing: true, ..Held::default() });
    let said = start_with(home.path(), &Environment::default(), keychain).unwrap_err();
    assert!(said.contains("wouldn't keep a new one"), "{said}");
    assert!(!home.path().join("mailmind.db").exists());
}

#[test]
fn a_keychain_that_doesnt_answer_gets_no_key_made_in_it() {
    // Locked, it may hold the key already: a new one in its place would leave the database unread.
    let home = tempfile::tempdir().unwrap();
    let keychain = Arc::new(Held { answers_nothing: true, ..Held::default() });
    let said = start_with(home.path(), &Environment::default(), keychain).unwrap_err();
    assert!(said.contains("no database key"), "{said}");
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
    Store::open(&home.path().join("mailmind.db"), &DbKey::from_bytes([0x5e; 32]))
        .unwrap()
        .create_list_tables()
        .unwrap();
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

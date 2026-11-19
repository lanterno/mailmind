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
use mm_store::migrate::BASE;
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
    let seams = Seams {
        clock: Arc::new(Frozen),
        outside: Arc::new(Open),
        keychain,
        sanitizer: Arc::new(Sanitizing),
        mail_hosts: Arc::new(mm_server::mail_hosts::NoLookups),
        local_ai: Arc::new(mm_server::local_ai::NoLocalServers),
    };
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
    assert!(said.contains("key to this Mac's copy of your mail isn't in your Keychain"), "{said}");
    assert!(said.contains("Start Fresh makes a new copy"), "{said}");
    for_a_person(&said);
    assert_eq!(keychain.holding("database-key"), None);
}

#[test]
fn a_keychain_that_wont_keep_a_new_key_makes_no_database() {
    let home = tempfile::tempdir().unwrap();
    let keychain = Arc::new(Held { keeps_nothing: true, ..Held::default() });
    let said = start_with(home.path(), &Environment::default(), keychain).unwrap_err();
    assert!(said.contains("Your Keychain wouldn't keep MailMind's key"), "{said}");
    for_a_person(&said);
    assert!(!home.path().join("mailmind.db").exists());
}

#[test]
fn a_keychain_that_doesnt_answer_gets_no_key_made_in_it() {
    // Locked, it may hold the key already: a new one in its place would leave the database unread.
    let home = tempfile::tempdir().unwrap();
    let keychain = Arc::new(Held { answers_nothing: true, ..Held::default() });
    let said = start_with(home.path(), &Environment::default(), keychain).unwrap_err();
    assert!(said.contains("Your Keychain didn't give MailMind the key") && said.contains("Unlock"), "{said}");
    for_a_person(&said);
    assert!(!home.path().join("mailmind.db").exists());
}

#[test]
fn a_keychain_key_that_isnt_one_is_told_in_a_persons_words() {
    let home = tempfile::tempdir().unwrap();
    database(home.path(), [0x5e; 32], VERSION);
    let keychain = Arc::new(Held::default());
    keychain.items.lock().unwrap().insert("database-key".to_owned(), "not a key".to_owned());
    let said = start_with(home.path(), &Environment::default(), keychain).unwrap_err();
    assert!(said.contains("What your Keychain holds as MailMind's key") && said.contains("Start Fresh"), "{said}");
    for_a_person(&said);
}

#[test]
fn a_variables_key_that_isnt_one_names_the_variable() {
    // Only a developer, a test or the demo sets it: the words are theirs.
    let home = tempfile::tempdir().unwrap();
    let environment = Environment::from_pairs([("MAILMIND_DB_KEY", "5e")]);
    let said = start_with(home.path(), &environment, Arc::new(Held::default())).unwrap_err();
    assert_eq!(said, "MAILMIND_DB_KEY isn't 64 hex digits.");
    assert!(!home.path().join("mailmind.db").exists());
}

#[test]
fn on_the_command_line_a_keys_words_say_where_a_developer_gives_one() {
    let home = tempfile::tempdir().unwrap();
    database(home.path(), [0x5e; 32], VERSION);
    let said = start_with(home.path(), &Environment::default(), Arc::new(Held::default())).unwrap_err();
    let told = mm_server::on_the_command_line(&said);
    assert!(told.starts_with(&said) && told.contains("MAILMIND_DB_KEY"), "{told}");
    // Anything else is said as it is.
    assert_eq!(mm_server::on_the_command_line("can't listen on 127.0.0.1:8721"), "can't listen on 127.0.0.1:8721");
}

/// Words the Mac app's window shows as they are: sentences, as the window sets them, that name no developer's
/// program, command or variable.
fn for_a_person(said: &str) {
    for developers in ["Python", "just run", "`", "mailmind-server", "Rust", "MAILMIND_", "hex", "database"] {
        assert!(!said.contains(developers), "{developers:?} in {said:?}");
    }
    assert!(said.starts_with(char::is_uppercase) && said.ends_with('.'), "not a sentence: {said:?}");
}

#[test]
fn over_an_older_database_it_says_it_left_it_as_it_is() {
    let home = tempfile::tempdir().unwrap();
    // Older than the Python app's schema the core starts from: from there on it upgrades.
    database(home.path(), [0x5e; 32], BASE - 1);
    let said = start(home.path()).unwrap_err();
    assert!(said.contains(&format!("version {}", BASE - 1)) && said.contains("was left as it is"), "{said}");
    for_a_person(&said);
}

#[test]
fn over_a_database_never_upgraded_it_says_it_left_it_as_it_is() {
    // A file with tables but no version, as a fixture made outside the migrations is.
    let home = tempfile::tempdir().unwrap();
    Store::open(&home.path().join("mailmind.db"), &DbKey::from_bytes([0x5e; 32]))
        .unwrap()
        .create_list_tables()
        .unwrap();
    let said = start(home.path()).unwrap_err();
    assert!(said.contains("version 0") && said.contains("was left as it is"), "{said}");
    for_a_person(&said);
}

#[test]
fn over_a_newer_database_it_says_to_install_the_newest_mailmind_and_opens_nothing() {
    let home = tempfile::tempdir().unwrap();
    database(home.path(), [0x5e; 32], VERSION + 1);
    let said = start(home.path()).unwrap_err();
    assert!(said.contains(&format!("version {}", VERSION + 1)), "{said}");
    assert!(said.contains("install the newest MailMind"), "{said}");
    for_a_person(&said);
}

/// The bytes of each file in `home`, by name, the config aside.
fn files(home: &Path) -> Vec<(String, Vec<u8>)> {
    let mut found: Vec<(String, Vec<u8>)> = std::fs::read_dir(home)
        .unwrap()
        .map(|entry| entry.unwrap().path())
        .filter(|path| path.is_file() && !path.ends_with("mailmind.config.json"))
        .map(|path| (path.file_name().unwrap().to_string_lossy().into_owned(), std::fs::read(&path).unwrap()))
        .collect();
    found.sort();
    found
}

#[test]
fn a_file_that_isnt_a_database_is_told_as_one_the_key_doesnt_open_and_left_as_it_was() {
    let home = tempfile::tempdir().unwrap();
    let garbage: Vec<u8> =
        (0..64 * 1024).map(|n: u32| u8::try_from(n.wrapping_mul(2_654_435_761) >> 24).unwrap()).collect();
    std::fs::write(home.path().join("mailmind.db"), &garbage).unwrap();
    let before = files(home.path());
    let said = start(home.path()).unwrap_err();
    assert!(said.starts_with("MailMind's key doesn't open this Mac's copy of your mail"), "{said}");
    assert!(said.contains("Start Fresh"), "{said}");
    for_a_person(&said);
    assert_eq!(files(home.path()), before, "nothing was written over it, and nothing made beside it");
}

#[test]
fn a_database_cut_short_is_left_as_it_was() {
    let home = tempfile::tempdir().unwrap();
    database(home.path(), [0x5e; 32], VERSION);
    assert_eq!(start(home.path()), Ok(()));
    let path = home.path().join("mailmind.db");
    let whole = std::fs::read(&path).unwrap();
    // Its first pages only, as a disk that filled or a copy cut off would leave it.
    std::fs::write(&path, &whole[..whole.len() / 3]).unwrap();
    let before = files(home.path());
    let said = start(home.path()).unwrap_err();
    // SQLCipher can't tell a file cut short from one another key made: either way a new copy is what helps.
    assert!(said.contains("Start Fresh makes a new copy from your mail's servers"), "{said}");
    for_a_person(&said);
    assert_eq!(files(home.path()), before, "nothing was written over it");
}

#[test]
fn a_key_that_doesnt_open_it_is_told_apart() {
    let home = tempfile::tempdir().unwrap();
    database(home.path(), [0x11; 32], VERSION);
    let said = start(home.path()).unwrap_err();
    assert!(said.starts_with("MailMind's key doesn't open this Mac's copy of your mail"), "{said}");
    for_a_person(&said);
}

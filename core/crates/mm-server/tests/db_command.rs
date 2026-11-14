//! `mailmind-server db check-upgrade [--db PATH]`: the core's migrations tried on a copy of the data directory's
//! database, or the one named, with the key the server reads and never a new one; its exit status the verdict. Every
//! database here is a new, fictional one in a temp directory, and every run gives MAILMIND_DB_KEY, so no Keychain
//! is asked but the test's own.

#![allow(clippy::unwrap_used)]

use std::path::Path;
use std::process::Command;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};

use mm_kernel::seams::{Clock, Keychain, KeychainProblem, Moment, Outside, Secret, Tripped};
use mm_server::Seams;
use mm_server::db_command::run;
use mm_server::environment::Environment;
use mm_server::real::Sanitizing;
use mm_store::migrate::BASE;
use mm_store::upgrade_check::Verdict;
use mm_store::{Database, DbKey};

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

/// A Keychain that holds nothing, keeps nothing, and remembers being asked.
#[derive(Debug, Default)]
struct Empty {
    asked: AtomicBool,
    kept: AtomicBool,
}

impl Keychain for Empty {
    fn find(&self, _item: &str) -> Result<Option<Secret>, KeychainProblem> {
        self.asked.store(true, Ordering::SeqCst);
        Ok(None)
    }

    fn put(&self, _item: &str, _secret: &Secret) -> Result<(), KeychainProblem> {
        self.kept.store(true, Ordering::SeqCst);
        Err(KeychainProblem)
    }
}

const KEY: &str = "5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e";

fn seams(keychain: Arc<Empty>) -> Seams {
    Seams { clock: Arc::new(Frozen), outside: Arc::new(Open), keychain, sanitizer: Arc::new(Sanitizing) }
}

/// A database the core made in `home`, holding a muted thread.
fn made(home: &Path) {
    let path = home.join("mailmind.db");
    let database = Database::new(path, DbKey::from_hex(KEY).unwrap(), Arc::new(Open));
    database.ready(&Frozen).unwrap();
    database
        .write(|c| {
            c.execute("INSERT INTO muted(account, thread_id, muted_at) VALUES ('ada@example.test', 't1', 'now')", [])
        })
        .unwrap();
}

fn args(words: &[&str]) -> Vec<String> {
    words.iter().map(|w| (*w).to_owned()).collect()
}

fn checked(words: &[&str], environment: &Environment, keychain: Arc<Empty>) -> (Result<Verdict, String>, String) {
    let mut lines = Vec::new();
    let verdict = run(&args(words), environment, &seams(keychain), &mut |line| lines.push(line.to_owned()));
    (verdict, lines.join("\n"))
}

#[test]
fn it_checks_the_data_directorys_database_with_the_key_the_server_reads() {
    let home = tempfile::tempdir().unwrap();
    made(home.path());
    let home_text = home.path().to_str().unwrap();
    let environment = Environment::from_pairs([("MAILMIND_HOME", home_text), ("MAILMIND_DB_KEY", KEY)]);
    let keychain = Arc::new(Empty::default());
    let (verdict, report) = checked(&["check-upgrade"], &environment, keychain.clone());
    assert_eq!(verdict, Ok(Verdict::Passed), "{report}");
    assert!(
        report.starts_with(&format!("A copy of the database, migrated from version {BASE} to {BASE}:")),
        "{report}"
    );
    assert!(report.contains("every table kept its rows: yes"), "{report}");
    assert!(!keychain.asked.load(Ordering::SeqCst));
}

#[test]
fn it_checks_the_database_named() {
    let home = tempfile::tempdir().unwrap();
    made(home.path());
    let path = home.path().join("mailmind.db");
    let environment = Environment::from_pairs([("MAILMIND_DB_KEY", KEY)]);
    let (verdict, report) =
        checked(&["check-upgrade", "--db", path.to_str().unwrap()], &environment, Arc::new(Empty::default()));
    assert_eq!(verdict, Ok(Verdict::Passed), "{report}");
    // Where it is, and what it holds, are said nowhere.
    assert!(!report.contains(home.path().to_str().unwrap()) && !report.contains("t1"), "{report}");
}

#[test]
fn with_no_database_there_is_nothing_to_check_and_no_key_is_looked_for() {
    let home = tempfile::tempdir().unwrap();
    let environment = Environment::from_pairs([("MAILMIND_HOME", home.path().to_str().unwrap())]);
    let keychain = Arc::new(Empty::default());
    let (verdict, report) = checked(&["check-upgrade"], &environment, keychain.clone());
    assert_eq!(verdict, Ok(Verdict::CannotRun));
    assert_eq!(report, "No database to check.");
    assert!(!keychain.asked.load(Ordering::SeqCst));
    assert!(!home.path().join("mailmind.db").exists());
}

#[test]
fn with_no_key_it_makes_none() {
    let home = tempfile::tempdir().unwrap();
    made(home.path());
    let environment = Environment::from_pairs([("MAILMIND_HOME", home.path().to_str().unwrap())]);
    let keychain = Arc::new(Empty::default());
    let (verdict, _) = checked(&["check-upgrade"], &environment, keychain.clone());
    assert!(verdict.unwrap_err().contains("no database key"));
    assert!(keychain.asked.load(Ordering::SeqCst) && !keychain.kept.load(Ordering::SeqCst));
}

#[test]
fn anything_else_is_told_how_to_ask() {
    let environment = Environment::from_pairs([("MAILMIND_DB_KEY", KEY)]);
    for words in [&[][..], &["compact"], &["check-upgrade", "--dbx", "x"], &["check-upgrade", "--db"]] {
        let (verdict, _) = checked(words, &environment, Arc::new(Empty::default()));
        assert_eq!(verdict, Err("usage: mailmind-server db check-upgrade [--db PATH]".to_owned()), "{words:?}");
    }
}

/// The program itself, over `home`, with the key given.
fn program(home: &Path, words: &[&str]) -> (Option<i32>, String) {
    let done = Command::new(env!("CARGO_BIN_EXE_mailmind-server"))
        .args(words)
        .env("MAILMIND_HOME", home)
        .env("MAILMIND_DB_KEY", KEY)
        .output()
        .unwrap();
    (done.status.code(), String::from_utf8(done.stdout).unwrap())
}

#[test]
fn the_program_exits_with_the_verdict() {
    let home = tempfile::tempdir().unwrap();
    assert_eq!(program(home.path(), &["db", "check-upgrade"]), (Some(2), "No database to check.\n".to_owned()));
    made(home.path());
    let (code, out) = program(home.path(), &["db", "check-upgrade"]);
    assert_eq!(code, Some(0));
    assert!(out.ends_with("  every table kept its rows: yes\n"), "{out}");
    assert_eq!(program(home.path(), &["db", "nonsense"]).0, Some(2));
}

//! The daemon as a script meets it: JSON lines in, JSON lines out, with the key only in the environment.

#![allow(clippy::unwrap_used)]

use std::io::Write;
use std::process::{Command, Stdio};

use mm_fixtures::FIXTURE_KEY_HEX;

fn fixture(dir: &tempfile::TempDir) -> std::path::PathBuf {
    let path = dir.path().join("mail.db");
    let key = mm_store_key();
    mm_fixtures::build(&path, &key, 200, 7).unwrap();
    path
}

fn mm_store_key() -> mm_app::DbKey {
    mm_app::DbKey::from_hex(FIXTURE_KEY_HEX).unwrap()
}

fn run(database: &std::path::Path, key: Option<&str>, input: &str) -> (Vec<serde_json::Value>, String, bool) {
    let mut command = Command::new(env!("CARGO_BIN_EXE_mm-daemon"));
    command.arg(database).stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped());
    command.env_remove("MAILMIND_DB_KEY");
    if let Some(key) = key {
        command.env("MAILMIND_DB_KEY", key);
    }
    let mut child = command.spawn().unwrap();
    child.stdin.take().unwrap().write_all(input.as_bytes()).unwrap();
    let out = child.wait_with_output().unwrap();
    let lines = String::from_utf8(out.stdout).unwrap().lines().map(|l| serde_json::from_str(l).unwrap()).collect();
    (lines, String::from_utf8(out.stderr).unwrap(), out.status.success())
}

const SCRIPT: &str = concat!(
    r#"{"id":1,"method":"open_list","params":{"query":{"accounts":["work@example.test","home@example.test","club@example.test"],"folder":"INBOX"}}}"#,
    "\n",
    r#"{"id":2,"method":"set_visible","params":{"view":1,"first":0,"count":20}}"#,
    "\n",
);

#[test]
fn a_script_gets_every_batch_it_asked_for_before_the_daemon_exits() {
    let dir = tempfile::tempdir().unwrap();
    let (lines, _, ok) = run(&fixture(&dir), Some(FIXTURE_KEY_HEX), SCRIPT);
    assert!(ok);
    assert_eq!(lines[0]["outcome"], serde_json::json!({"kind": "opened", "view": 1, "total": 200}));
    let batch = lines.iter().find(|l| l["kind"] == "batch").expect("no batch before exit");
    assert_eq!(batch["batch"]["held_count"], 100);
}

#[test]
fn without_a_key_in_the_environment_it_opens_nothing() {
    let dir = tempfile::tempdir().unwrap();
    let (lines, err, ok) = run(&fixture(&dir), None, SCRIPT);
    assert!(!ok && lines.is_empty());
    assert!(err.contains("MAILMIND_DB_KEY"));
}

#[test]
fn the_wrong_key_is_named_as_the_problem_and_never_printed() {
    let dir = tempfile::tempdir().unwrap();
    let wrong = "ab".repeat(32);
    let (_, err, ok) = run(&fixture(&dir), Some(&wrong), SCRIPT);
    assert!(!ok);
    assert!(err.contains("key opens nothing"));
    assert!(!err.contains(&wrong));
}

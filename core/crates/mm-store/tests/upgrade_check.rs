//! `db check-upgrade` (`mm_store::upgrade_check`) on fictional databases in temp folders, with migrations that exist
//! only in these tests: it reports what the pending migrations would do, on a copy, in row counts and verdicts only
//! (as `tests/unit/platform/test_db_check.py` holds the Python app's to), and never writes the database it checks.

#![allow(clippy::unwrap_used)]

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use mm_kernel::seams::{Clock, Moment, Outside, Tripped};
use mm_store::migrate::{BASE, Migration, Ready};
use mm_store::upgrade_check::{Verdict, check_upgrade_with};
use mm_store::{Database, DbKey, Store};
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
/// In every row written here; no report may show it.
const MARK: &str = "quilt-pattern-7731";

fn database(path: &Path) -> Database {
    Database::new(path.to_path_buf(), DbKey::from_bytes(KEY), Arc::new(Open))
}

/// A keyed connection that enforces no foreign keys, as an older MailMind wrote.
fn keyed(path: &Path) -> Connection {
    let c = Connection::open(path).unwrap();
    c.execute_batch(&format!("PRAGMA key = \"x'{}'\"; PRAGMA foreign_keys = OFF;", "5e".repeat(32))).unwrap();
    c
}

/// mailmind.db in `folder`, made by the core at the base, holding two indexed messages, a stored body and a muted
/// thread.
fn database_at_base(folder: &Path) -> PathBuf {
    let path = folder.join("mailmind.db");
    assert_eq!(database(&path).ready_with(&Frozen, &[]), Ok(Ready::Made));
    let c = keyed(&path);
    for uid in 1..=2 {
        c.execute(
            "INSERT INTO messages(account, kind, folder, uid, message_id, subject, from_addr) \
             VALUES ('ada@example.test', 'imap', 'INBOX', ?1, ?2, ?3, 'lin@example.test')",
            rusqlite::params![uid, format!("<{uid}@example.test>"), format!("{MARK} {uid}")],
        )
        .unwrap();
        c.execute(
            "INSERT INTO msg_fts(rowid, subject) VALUES (?1, ?2)",
            rusqlite::params![uid, format!("{MARK} {uid}")],
        )
        .unwrap();
    }
    c.execute("INSERT INTO bodies(message_pk, raw) VALUES (1, ?1)", [MARK.as_bytes()]).unwrap();
    c.execute("INSERT INTO muted(account, thread_id, muted_at) VALUES ('ada@example.test', ?1, 'now')", [MARK])
        .unwrap();
    path
}

fn run(path: &Path, migrations: &[Migration]) -> (Verdict, String) {
    let mut lines = Vec::new();
    let verdict = check_upgrade_with(&database(path), &Frozen, migrations, &mut |line| lines.push(line.to_owned()));
    (verdict, lines.join("\n"))
}

/// The report's line for `table`, split at its spaces.
fn line(report: &str, table: &str) -> Vec<String> {
    report
        .lines()
        .map(|l| l.split_whitespace().map(str::to_owned).collect::<Vec<_>>())
        .find(|words| words.first().map(String::as_str) == Some(table))
        .unwrap_or_default()
}

/// Every file in `folder`, by name and content, but for the write-ahead log's shared-memory index, which every reader
/// of the database writes its read locks to, and an empty log, which a reader opens when there is none: SQLite's own,
/// holding nothing of the database.
fn fingerprint(folder: &Path) -> BTreeMap<String, Vec<u8>> {
    std::fs::read_dir(folder)
        .unwrap()
        .map(Result::unwrap)
        .filter(|entry| {
            let name = entry.file_name().into_string().unwrap();
            !name.ends_with("-shm") && !(name.ends_with("-wal") && entry.metadata().unwrap().len() == 0)
        })
        .map(|entry| {
            let path = entry.path();
            let content = if path.is_dir() { Vec::new() } else { std::fs::read(&path).unwrap() };
            (entry.file_name().into_string().unwrap(), content)
        })
        .collect()
}

fn m52_labels(c: &Connection) -> rusqlite::Result<()> {
    c.execute_batch("CREATE TABLE IF NOT EXISTS labels_seen(label TEXT PRIMARY KEY)")
}

fn m52_drops_muted(c: &Connection) -> rusqlite::Result<()> {
    c.execute_batch("DROP TABLE IF EXISTS muted")
}

const BODIES_OF_MAIL_GONE: &str = "SELECT count(*) FROM bodies WHERE message_pk NOT IN (SELECT pk FROM messages)";

fn m52_takes_bodies_of_mail_gone(c: &Connection) -> rusqlite::Result<()> {
    c.execute_batch("DELETE FROM bodies WHERE message_pk NOT IN (SELECT pk FROM messages)")
}

fn m52_takes_every_body(c: &Connection) -> rusqlite::Result<()> {
    c.execute_batch("DELETE FROM bodies")
}

fn m52_keeps_a_document(c: &Connection) -> rusqlite::Result<()> {
    c.execute_batch(
        "INSERT OR IGNORE INTO documents(name, body, changed_at) VALUES ('carried', '{}', '2026-11-02T12:00:00Z')",
    )
}

fn m52_rebuilds_the_index(c: &Connection) -> rusqlite::Result<()> {
    c.execute_batch(
        "DROP TABLE IF EXISTS msg_fts;
         CREATE VIRTUAL TABLE msg_fts USING fts5(subject, from_name, from_addr, recipients, snippet, body_text,
           tokenize='unicode61 remove_diacritics 2', content='', contentless_delete=1);
         INSERT INTO msg_fts(rowid, subject) SELECT pk, coalesce(subject, '') FROM messages;",
    )
}

/// A task naming a message key no message has, and then a foreign key over it, written into the schema directly: no
/// statement SQLite checks would let a migration add a row that breaks one.
fn m52_breaks_a_key(c: &Connection) -> rusqlite::Result<()> {
    c.execute(
        "INSERT INTO tasks(account, message_key, folder, title, created_at) \
         VALUES ('ada@example.test', 98, 'INBOX', ?1, 'now')",
        [MARK],
    )?;
    c.execute_batch(
        "PRAGMA writable_schema = ON;
         UPDATE sqlite_master SET sql = replace(sql, 'message_key INTEGER NOT NULL,',
           'message_key INTEGER NOT NULL REFERENCES message_identity(key),') WHERE name = 'tasks';
         PRAGMA writable_schema = OFF;",
    )
}

fn m52_fails(_: &Connection) -> rusqlite::Result<()> {
    Err(rusqlite::Error::InvalidQuery)
}

const LABELS: &[Migration] = &[Migration::new(52, m52_labels)];

#[test]
fn it_reports_counts_and_verdicts_and_leaves_the_database_alone() {
    for (migrations, newest) in [(&[][..], BASE), (LABELS, 52)] {
        let dir = tempfile::tempdir().unwrap();
        let path = database_at_base(dir.path());
        let before = fingerprint(dir.path());
        let (verdict, report) = run(&path, migrations);
        assert_eq!(verdict, Verdict::Passed, "{report}");
        assert!(report.contains(&format!("migrated from version {BASE} to {newest}")), "{report}");
        assert_eq!(line(&report, "messages"), ["messages", "2", "2"]);
        assert_eq!(line(&report, "bodies"), ["bodies", "1", "1"]);
        assert_eq!(line(&report, "muted"), ["muted", "1", "1"]);
        for verdict in
            ["integrity_check: ok", "foreign_key_check: ok", "full-text index: ok", "every table kept its rows: yes"]
        {
            assert!(report.contains(verdict), "{report}");
        }
        assert!(!report.contains(MARK));
        // Nothing written, nothing left beside it: the copy's folder and its own backup went with the check.
        assert_eq!(fingerprint(dir.path()), before);
    }
}

#[test]
fn a_table_a_migration_makes_is_new() {
    let dir = tempfile::tempdir().unwrap();
    let path = database_at_base(dir.path());
    let (verdict, report) = run(&path, LABELS);
    assert_eq!(verdict, Verdict::Passed, "{report}");
    assert_eq!(line(&report, "labels_seen"), ["labels_seen", "-", "0", "new"]);
}

#[test]
fn the_report_is_laid_out_as_the_python_apps_is() {
    let dir = tempfile::tempdir().unwrap();
    let path = database_at_base(dir.path());
    let (_, report) = run(&path, &[]);
    let lines: Vec<&str> = report.lines().collect();
    assert_eq!(lines[0], format!("A copy of the database, migrated from version {BASE} to {BASE}:"));
    assert_eq!(lines[1], "");
    // The widest table's name sets the column: message_recipients.
    assert_eq!(lines[2], format!("  {:<18}  {:>10}  {:>10}", "table", "before", "after"));
    assert!(lines.contains(&format!("  {:<18}  {:>10}  {:>10}", "messages", 2, 2).as_str()), "{report}");
    assert_eq!(lines[lines.len() - 1], "  every table kept its rows: yes");
}

#[test]
fn a_problem_the_upgrade_adds_fails_the_check_without_saying_what_it_holds() {
    let dir = tempfile::tempdir().unwrap();
    let path = database_at_base(dir.path());
    keyed(&path).execute("INSERT INTO assessments(message_key, summary) VALUES (99, ?1)", [MARK]).unwrap();
    let (verdict, report) = run(&path, &[Migration::new(52, m52_breaks_a_key)]);
    assert_eq!(verdict, Verdict::Problem, "{report}");
    assert!(report.contains("foreign_key_check: 2 problem(s), 1 of them new (tasks 1)"), "{report}");
    assert!(!report.contains(MARK));
}

#[test]
fn a_problem_already_there_is_named_by_table_and_passes() {
    let dir = tempfile::tempdir().unwrap();
    let path = database_at_base(dir.path());
    keyed(&path).execute("INSERT INTO assessments(message_key, summary) VALUES (99, ?1)", [MARK]).unwrap();
    let (verdict, report) = run(&path, LABELS);
    assert_eq!(verdict, Verdict::Passed, "{report}");
    assert!(
        report.contains("foreign_key_check: 1 problem(s), all there before the upgrade (assessments 1)"),
        "{report}"
    );
    assert!(!report.contains(MARK));
}

#[test]
fn without_a_database_there_is_nothing_to_check() {
    let dir = tempfile::tempdir().unwrap();
    let (verdict, report) = run(&dir.path().join("mailmind.db"), LABELS);
    assert_eq!(verdict, Verdict::CannotRun);
    assert!(report.contains("No database"), "{report}");
    assert!(!dir.path().join("mailmind.db").exists());
}

#[test]
fn a_table_a_migration_retires_is_reported_and_is_no_loss() {
    let dir = tempfile::tempdir().unwrap();
    let path = database_at_base(dir.path());
    let retires = Migration { retires: &["muted"], ..Migration::new(52, m52_drops_muted) };
    let (verdict, report) = run(&path, &[retires]);
    assert_eq!(verdict, Verdict::Passed, "{report}");
    assert_eq!(line(&report, "muted"), ["muted", "1", "-", "retired"]);
    assert!(report.contains("every table kept its rows: yes") && !report.contains(MARK));
}

#[test]
fn a_table_no_migration_retired_is_a_loss() {
    let dir = tempfile::tempdir().unwrap();
    let path = database_at_base(dir.path());
    let (verdict, report) = run(&path, &[Migration::new(52, m52_drops_muted)]);
    assert_eq!(verdict, Verdict::Problem);
    assert_eq!(line(&report, "muted"), ["muted", "1", "-", "CHANGED"]);
    assert!(report.contains("every table kept its rows: no (muted)") && !report.contains(MARK));
}

/// A database at the base holding message 1's body and two whose messages are gone.
fn holding_bodies_of_mail_gone(folder: &Path) -> PathBuf {
    let path = database_at_base(folder);
    let c = keyed(&path);
    for pk in [98, 99] {
        c.execute("INSERT INTO bodies(message_pk, raw) VALUES (?1, ?2)", rusqlite::params![pk, MARK.as_bytes()])
            .unwrap();
    }
    path
}

#[test]
fn the_rows_a_migration_trims_on_purpose_are_no_loss() {
    let dir = tempfile::tempdir().unwrap();
    let path = holding_bodies_of_mail_gone(dir.path());
    let trims =
        Migration { trims: &[("bodies", BODIES_OF_MAIL_GONE)], ..Migration::new(52, m52_takes_bodies_of_mail_gone) };
    let (verdict, report) = run(&path, &[trims]);
    assert_eq!(line(&report, "bodies"), ["bodies", "3", "1", "trimmed"]);
    assert_eq!(verdict, Verdict::Passed, "{report}");
    assert!(report.contains("every table kept its rows: yes") && report.contains("foreign_key_check: ok"), "{report}");
    assert!(!report.contains(MARK));
}

#[test]
fn a_row_more_than_a_migration_trims_is_a_loss() {
    let dir = tempfile::tempdir().unwrap();
    let path = holding_bodies_of_mail_gone(dir.path());
    let trims = Migration { trims: &[("bodies", BODIES_OF_MAIL_GONE)], ..Migration::new(52, m52_takes_every_body) };
    let (verdict, report) = run(&path, &[trims]);
    assert_eq!(line(&report, "bodies"), ["bodies", "3", "0", "CHANGED"]);
    assert_eq!(verdict, Verdict::Problem, "{report}");
    assert!(report.contains("every table kept its rows: no (bodies)"), "{report}");
}

#[test]
fn a_database_past_the_trim_has_no_rows_to_lose() {
    // Even the very rows the migration would have taken, since it has run: here the database reached 52 without
    // them going, and a later migration that declares nothing takes them.
    let dir = tempfile::tempdir().unwrap();
    let path = holding_bodies_of_mail_gone(dir.path());
    database(&path).ready_with(&Frozen, LABELS).unwrap();
    let trims =
        Migration { trims: &[("bodies", BODIES_OF_MAIL_GONE)], ..Migration::new(52, m52_takes_bodies_of_mail_gone) };
    let (verdict, report) = run(&path, &[trims, Migration::new(53, m52_takes_bodies_of_mail_gone)]);
    assert!(report.contains("migrated from version 52 to 53"), "{report}");
    assert_eq!(line(&report, "bodies"), ["bodies", "3", "1", "CHANGED"]);
    assert_eq!(verdict, Verdict::Problem, "{report}");
}

#[test]
fn a_table_a_migration_fills_may_grow_and_one_it_doesnt_may_not() {
    let dir = tempfile::tempdir().unwrap();
    let path = database_at_base(dir.path());
    let fills = Migration { fills: &["documents"], ..Migration::new(52, m52_keeps_a_document) };
    let (verdict, report) = run(&path, &[fills]);
    assert_eq!(line(&report, "documents"), ["documents", "0", "1", "carried", "over"]);
    assert_eq!(verdict, Verdict::Passed, "{report}");
    let (verdict, report) = run(&path, &[Migration::new(52, m52_keeps_a_document)]);
    assert_eq!(line(&report, "documents"), ["documents", "0", "1", "CHANGED"]);
    assert_eq!(verdict, Verdict::Problem, "{report}");
}

#[test]
fn a_table_a_migration_rebuilds_may_change_its_count() {
    let dir = tempfile::tempdir().unwrap();
    let path = database_at_base(dir.path());
    // An index row for a message that isn't there: the rebuild leaves it out.
    keyed(&path).execute("INSERT INTO msg_fts(rowid, subject) VALUES (7, ?1)", [MARK]).unwrap();
    let rebuilds = Migration { rebuilds: &["msg_fts"], ..Migration::new(52, m52_rebuilds_the_index) };
    let (verdict, report) = run(&path, &[rebuilds]);
    assert_eq!(line(&report, "msg_fts"), ["msg_fts", "3", "2", "rebuilt"]);
    assert!(report.contains("full-text index: ok"), "{report}");
    assert_eq!(verdict, Verdict::Passed, "{report}");
}

#[test]
fn a_migration_that_fails_on_the_copy_is_a_problem_and_the_database_is_left_alone() {
    let dir = tempfile::tempdir().unwrap();
    let path = database_at_base(dir.path());
    let before = fingerprint(dir.path());
    let (verdict, report) = run(&path, &[Migration::new(52, m52_labels), Migration::new(53, m52_fails)]);
    assert_eq!(verdict, Verdict::Problem);
    assert!(
        report.contains(&format!("migrated from version {BASE}")) && report.contains("version 53 failed"),
        "{report}"
    );
    assert_eq!(fingerprint(dir.path()), before);
}

#[test]
fn a_database_the_core_cant_upgrade_cant_be_checked_here() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("mailmind.db");
    Store::open(&path, &DbKey::from_bytes(KEY)).unwrap().set_version(BASE - 1).unwrap();
    let (verdict, report) = run(&path, LABELS);
    assert_eq!(verdict, Verdict::CannotRun);
    assert!(report.contains(&format!("version {}", BASE - 1)) && report.contains("Python app"), "{report}");
    keyed(&path).execute_batch("PRAGMA user_version = 60").unwrap();
    let (verdict, report) = run(&path, LABELS);
    assert_eq!(verdict, Verdict::CannotRun);
    assert!(report.contains("version 60, newer than the version 52"), "{report}");
}

#[test]
fn another_key_opens_nothing_and_says_so() {
    let dir = tempfile::tempdir().unwrap();
    let path = database_at_base(dir.path());
    let other = Database::new(path, DbKey::from_bytes([0x11; 32]), Arc::new(Open));
    let mut lines = Vec::new();
    let verdict = check_upgrade_with(&other, &Frozen, LABELS, &mut |line| lines.push(line.to_owned()));
    assert_eq!(verdict, Verdict::CannotRun);
    assert_eq!(lines, ["The database key doesn't open the database."]);
}

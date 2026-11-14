//! The forget round as `mailmind-server` starts it: a lane of its own, daily, the first ten minutes in, which lets go
//! of the keys with no copy left and the changes finished a month ago; and none in a process that only reads.

#![allow(clippy::unwrap_used)]

use std::path::Path;
use std::sync::Arc;
use std::time::{Duration, Instant};

use mm_kernel::seams::{Clock, Keychain, KeychainProblem, Moment, Outside, Secret, Tripped};
use mm_server::environment::{Environment, Settings};
use mm_server::real::Sanitizing;
use mm_server::workers::Workers;
use mm_server::{App, Seams};
use mm_store::{Database, DbKey, Store};
use serde_json::{Value, json};

/// 2026-11-02 12:00:00 UTC.
const NOW: i64 = 1_793_620_800;
const DAY: i64 = 86_400;

#[derive(Debug)]
struct Frozen;

impl Clock for Frozen {
    fn now(&self) -> Moment {
        Moment::from_micros(NOW * 1_000_000)
    }
}

/// Files may open; nothing off the machine may be reached, nor a program run.
#[derive(Debug)]
struct Closed;

impl Outside for Closed {
    fn open(&self, _path: &Path) -> Result<(), Tripped> {
        Ok(())
    }

    fn reach(&self, host: &str) -> Result<(), Tripped> {
        Err(Tripped(host.to_owned()))
    }

    fn run(&self, program: &str) -> Result<(), Tripped> {
        Err(Tripped(program.to_owned()))
    }
}

#[derive(Debug)]
struct Empty;

impl Keychain for Empty {
    fn find(&self, _item: &str) -> Result<Option<Secret>, KeychainProblem> {
        Ok(None)
    }
}

/// The change queue, as the newest schema golden has it.
const TABLES: &str = "
CREATE TABLE mail_commands(id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, message_id TEXT, src_folder TEXT NOT NULL, dest TEXT,
  params TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt_at INTEGER NOT NULL DEFAULT 0, applied INTEGER NOT NULL DEFAULT 0, begun INTEGER NOT NULL DEFAULT 0,
  outcome TEXT, last_error TEXT, created_at TEXT NOT NULL, done_at TEXT);
";

/// The app over a data directory with no accounts, two keys with no copy left (one gone 31 days ago, one 29) and two
/// changes done (31 and 29 days ago), its profile `profile`; and the database.
fn app(home: &Path, profile: &str) -> (Arc<App>, Database) {
    std::fs::write(home.join("mailmind.config.json"), "{}").unwrap();
    let (path, key) = (home.join("mailmind.db"), DbKey::from_bytes([0x5e; 32]));
    let store = Store::open(&path, &key).unwrap();
    store.create_list_tables().unwrap();
    // At the version the server reads, as the Python app's migrations leave it.
    store.set_version(mm_store::schema::VERSION).unwrap();
    drop(store);
    let database = Database::new(path, key, Arc::new(Closed));
    let mut rows = String::from(TABLES);
    for (key, days) in [(1, 31), (2, 29)] {
        rows.push_str(&format!(
            "INSERT INTO message_identity (key, account, identity, gone_at) \
             VALUES ({key}, 'ada@example.test', '<m{key}@example.test>', {});",
            NOW - days * DAY
        ));
    }
    for (id, done_at) in [(1, "2026-10-02T12:00:00+00:00"), (2, "2026-10-04T12:00:00+00:00")] {
        rows.push_str(&format!(
            "INSERT INTO mail_commands (id, account, idempotency_key, kind, message_id, src_folder, params, state, \
             created_at, done_at) VALUES ({id}, 'ada@example.test', 'trash:{id}', 'trash', '<m@example.test>', \
             'INBOX', '{{}}', 'done', '2026-09-01T12:00:00+00:00', '{done_at}');"
        ));
    }
    database.write(|c| c.execute_batch(&rows)).unwrap();
    let key = "5e".repeat(32);
    let environment = Environment::from_pairs([("MAILMIND_DB_KEY", key.as_str()), ("MAILMIND_PROFILE", profile)]);
    let settings = Settings { home: home.to_path_buf() };
    let seams = Seams {
        clock: Arc::new(Frozen),
        outside: Arc::new(Closed),
        keychain: Arc::new(Empty),
        sanitizer: Arc::new(Sanitizing),
    };
    (Arc::new(App::assemble(&environment, &settings, home.join("dist"), 0, &seams).unwrap()), database)
}

fn ids(database: &Database, sql: &str) -> Vec<i64> {
    database.read(|c| c.prepare(sql)?.query_map([], |r| r.get(0))?.collect::<Result<Vec<i64>, _>>()).unwrap()
}

/// The forget job as `/api/debug/jobs` shows it.
fn forget_job(app: &App) -> Value {
    app.jobs.snapshot().as_array().unwrap().iter().find(|job| job["name"] == "forget").unwrap().clone()
}

#[test]
fn the_round_runs_daily_and_lets_go_of_what_went_a_month_ago() {
    let home = tempfile::tempdir().unwrap();
    let (app, database) = app(home.path(), "full");
    let workers = Workers::start(&app, false, 0, 0);
    assert_eq!(forget_job(&app)["every"], json!(86_400.0));
    // Its first round is ten minutes in; a kick brings it now.
    let deadline = Instant::now() + Duration::from_secs(3);
    while forget_job(&app)["next_at"].is_null() {
        assert!(Instant::now() < deadline, "the lane never started");
        std::thread::sleep(Duration::from_millis(5));
    }
    assert_eq!(forget_job(&app)["next_at"], json!((NOW + 600) as f64));
    app.jobs.kick("forget");
    while forget_job(&app)["runs"] == json!(0) || forget_job(&app)["running"] == json!(true) {
        assert!(Instant::now() < deadline, "the round never ran");
        std::thread::sleep(Duration::from_millis(5));
    }
    workers.stop();
    assert_eq!(forget_job(&app)["failures"], json!(0));
    assert_eq!(ids(&database, "SELECT key FROM message_identity ORDER BY key"), [2]);
    assert_eq!(ids(&database, "SELECT id FROM mail_commands ORDER BY id"), [2]);
}

#[test]
fn a_process_that_only_reads_lets_go_of_nothing() {
    let home = tempfile::tempdir().unwrap();
    let (app, database) = app(home.path(), "read-only");
    let workers = Workers::start(&app, true, 0, 0);
    assert!(!app.jobs.names().contains(&"forget"), "{:?}", app.jobs.names());
    workers.stop();
    assert_eq!(ids(&database, "SELECT key FROM message_identity ORDER BY key"), [1, 2]);
}

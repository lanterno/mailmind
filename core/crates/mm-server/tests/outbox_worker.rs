//! The outbox's worker and the drafts' push as `mailmind-server` starts them: a send brought forward is taken at once,
//! without waiting for the worker's poll, and a process that only reads starts neither.

#![allow(clippy::unwrap_used)]

use std::path::Path;
use std::sync::Arc;
use std::time::{Duration, Instant};

use mm_compose::spec::MessageSpec;
use mm_kernel::seams::{Clock, Keychain, KeychainProblem, Moment, Outside, Secret, Tripped};
use mm_server::environment::{Environment, Settings};
use mm_server::workers::Workers;
use mm_server::{App, Seams};
use mm_store::{Database, DbKey, Store};
use serde_json::{Value, json};

#[derive(Debug)]
struct Frozen;

impl Clock for Frozen {
    fn now(&self) -> Moment {
        Moment::from_micros(1_793_620_800_000_000)
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

/// The outbox and the drafts, as the newest schema golden has them.
const TABLES: &str = "
CREATE TABLE outbox (id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE, message_id TEXT NOT NULL, envelope TEXT NOT NULL, recipients TEXT NOT NULL,
  subject TEXT, raw BLOB NOT NULL, state TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt_at INTEGER NOT NULL DEFAULT 0, delivered INTEGER NOT NULL DEFAULT 0,
  sent_appended INTEGER NOT NULL DEFAULT 0, answered_set INTEGER NOT NULL DEFAULT 0,
  followup_recorded INTEGER NOT NULL DEFAULT 0, last_error TEXT, draft_id INTEGER,
  draft_deleted INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, sent_at TEXT, orig_account TEXT,
  orig_message_id TEXT, orig_thread_id TEXT, wire_begun INTEGER NOT NULL DEFAULT 0,
  wait_kind TEXT NOT NULL DEFAULT 'grace', original_key INTEGER);
CREATE TABLE drafts (id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL, folder TEXT, uid INTEGER,
  message_id TEXT, uidvalidity INTEGER, mode TEXT, subject TEXT, created_at TEXT, updated_at TEXT, own_text TEXT,
  tail_text TEXT, tail_html TEXT, bcc TEXT, raw_sha TEXT, idempotency_key TEXT, version INTEGER NOT NULL DEFAULT 0,
  saved_by TEXT, pushed_at INTEGER);
CREATE TABLE draft_journal(draft_id INTEGER PRIMARY KEY REFERENCES drafts(id) ON DELETE CASCADE,
  version INTEGER NOT NULL, account TEXT NOT NULL, discard INTEGER NOT NULL DEFAULT 0, message_id TEXT, raw BLOB,
  raw_sha TEXT, own_text TEXT, tail_text TEXT, tail_html TEXT, bcc TEXT, tried TEXT, saved_at INTEGER NOT NULL,
  due_at INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0);
";

/// The app over a data directory with no accounts and an empty outbox, its profile `profile`.
fn app(home: &Path, profile: &str) -> Arc<App> {
    std::fs::write(home.join("mailmind.config.json"), "{}").unwrap();
    let (path, key) = (home.join("mailmind.db"), DbKey::from_bytes([0x5e; 32]));
    // At the version the server reads, as the Python app's migrations leave it.
    Store::open(&path, &key).unwrap().set_version(mm_store::schema::VERSION).unwrap();
    Database::new(path, key, Arc::new(Closed)).read(|c| c.execute_batch(TABLES)).unwrap();
    let key = "5e".repeat(32);
    let environment = Environment::from_pairs([("MAILMIND_DB_KEY", key.as_str()), ("MAILMIND_PROFILE", profile)]);
    let settings = Settings { home: home.to_path_buf() };
    let seams = Seams { clock: Arc::new(Frozen), outside: Arc::new(Closed), keychain: Arc::new(Empty) };
    Arc::new(App::assemble(&environment, &settings, home.join("dist"), 0, &seams).unwrap())
}

/// A send from an account that no longer exists, queued in its Undo Send window: its id.
fn queued(app: &App) -> i64 {
    let spec = MessageSpec {
        account: "ada@example.test".to_owned(),
        account_name: "Ada".to_owned(),
        to: vec!["lin@example.test".to_owned()],
        subject: "Fabric".to_owned(),
        text: "Hi".to_owned(),
        ..MessageSpec::default()
    };
    let built = app.compose.outgoing.build(&spec).unwrap();
    let (id, new) = app.compose.outgoing.send(&built, "worker-1", "Fabric", None, "ada@example.test").unwrap();
    assert!(new);
    id.unwrap()
}

/// The send as the outbox lists it.
fn listed(app: &App, id: i64) -> Value {
    let tray = app.reading.outbox.tray(10).unwrap();
    tray["items"].as_array().unwrap().iter().find(|item| item["id"] == json!(id)).unwrap().clone()
}

#[test]
fn a_send_brought_forward_is_taken_at_once_by_the_worker() {
    let home = tempfile::tempdir().unwrap();
    let app = app(home.path(), "full");
    let workers = Workers::start(&app, false, 0);
    assert!(app.jobs.names().contains(&"draft-push"), "{:?}", app.jobs.names());
    let id = queued(&app);
    // The worker waits out the send's ten-second window, the clock is frozen: only the kick that Send now gives
    // brings the pass that takes it in time.
    assert_eq!(listed(&app, id)["state"], json!("queued"));
    assert!(app.compose.delivery.send_now(id).unwrap());
    let deadline = Instant::now() + Duration::from_secs(3);
    while ["queued", "sending"].map(|state| json!(state)).contains(&listed(&app, id)["state"]) {
        assert!(Instant::now() < deadline, "the worker never took it");
        std::thread::sleep(Duration::from_millis(10));
    }
    let item = listed(&app, id);
    assert_eq!(
        (&item["state"], &item["error"], &item["attempts"]),
        (&json!("error"), &json!("account removed"), &json!(1))
    );
    workers.stop();
}

#[test]
fn a_process_that_only_reads_starts_neither() {
    let home = tempfile::tempdir().unwrap();
    let app = app(home.path(), "read-only");
    let workers = Workers::start(&app, true, 0);
    assert_eq!(app.jobs.names(), ["snooze"]);
    workers.stop();
}

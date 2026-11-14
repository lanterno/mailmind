//! The body backfill as `mailmind-server` starts it (`background.schedule`'s backfill): a lane of its own, every ten
//! minutes, the first 45 seconds in, each round saying when the next is due; none in a process that only reads. And
//! MAILMIND_WARM_FOLDERS, which says which accounts a sync round warms beyond the Inbox, as the environment reads it.

#![allow(clippy::unwrap_used)]

use std::collections::BTreeSet;
use std::path::Path;
use std::sync::Arc;
use std::time::{Duration, Instant};

use mm_kernel::seams::{Clock, Keychain, KeychainProblem, Moment, Outside, Secret, Tripped};
use mm_mailbox::warm::Which;
use mm_server::environment::{Environment, Settings};
use mm_server::real::Sanitizing;
use mm_server::workers::Workers;
use mm_server::{App, Seams};
use mm_store::{DbKey, Store};
use serde_json::{Value, json};

/// 2026-11-02 12:00:00 UTC.
const NOW: i64 = 1_793_620_800;

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

/// The app over a data directory with no accounts, its profile `profile`.
fn app(home: &Path, profile: &str) -> Arc<App> {
    std::fs::write(home.join("mailmind.config.json"), "{}").unwrap();
    let (path, key) = (home.join("mailmind.db"), DbKey::from_bytes([0x5e; 32]));
    let store = Store::open(&path, &key).unwrap();
    store.create_list_tables().unwrap();
    // At the version the server reads, as the Python app's migrations leave it.
    store.set_version(mm_store::schema::VERSION).unwrap();
    drop(store);
    let key = "5e".repeat(32);
    let environment = Environment::from_pairs([("MAILMIND_DB_KEY", key.as_str()), ("MAILMIND_PROFILE", profile)]);
    let settings = Settings { home: home.to_path_buf() };
    let seams = Seams {
        clock: Arc::new(Frozen),
        outside: Arc::new(Closed),
        keychain: Arc::new(Empty),
        sanitizer: Arc::new(Sanitizing),
    };
    Arc::new(App::assemble(&environment, &settings, home.join("dist"), 0, &seams).unwrap())
}

/// The backfill job as `/api/debug/jobs` shows it.
fn backfill_job(app: &App) -> Value {
    app.jobs.snapshot().as_array().unwrap().iter().find(|job| job["name"] == "backfill").unwrap().clone()
}

fn until(what: impl Fn() -> bool, said: &str) {
    let deadline = Instant::now() + Duration::from_secs(5);
    while !what() {
        assert!(Instant::now() < deadline, "{said}");
        std::thread::sleep(Duration::from_millis(5));
    }
}

#[test]
fn the_backfill_runs_on_a_lane_of_its_own_and_its_round_says_when_the_next_is_due() {
    let home = tempfile::tempdir().unwrap();
    let app = app(home.path(), "full");
    let workers = Workers::start(&app, false, 0, 0);
    assert_eq!(backfill_job(&app)["every"], json!(600.0));
    // Its first round is 45 seconds in; a kick brings it now.
    until(|| !backfill_job(&app)["next_at"].is_null(), "the lane never started");
    assert_eq!(backfill_job(&app)["next_at"], json!((NOW + 45) as f64));
    app.jobs.kick("backfill");
    until(|| backfill_job(&app)["runs"] == json!(1) && !backfill_job(&app)["next_at"].is_null(), "it never ran");
    workers.stop();
    // With no mail left to ask for, the next round is ten minutes on.
    assert_eq!(backfill_job(&app)["failures"], json!(0));
    assert_eq!(backfill_job(&app)["next_at"], json!((NOW + 600) as f64));
}

#[test]
fn a_process_that_only_reads_fetches_no_body() {
    let home = tempfile::tempdir().unwrap();
    let app = app(home.path(), "read-only");
    let workers = Workers::start(&app, true, 0, 0);
    assert!(!app.jobs.names().contains(&"backfill"), "{:?}", app.jobs.names());
    workers.stop();
}

#[test]
fn the_variable_says_which_accounts_are_warmed_unset_leaving_it_to_the_file() {
    let warm = |value: &str| Environment::from_pairs([("MAILMIND_WARM_FOLDERS", value)]).warm_folders();
    assert_eq!(Environment::from_pairs([]).warm_folders(), None);
    assert_eq!(warm(" "), None);
    assert_eq!(warm("1"), Some(Which { every: true, listed: BTreeSet::new() }));
    assert_eq!(warm("off"), Some(Which::default()));
    assert_eq!(
        warm("ada@example.test,bea"),
        Some(Which { every: false, listed: ["ada@example.test".to_owned()].into() })
    );
}

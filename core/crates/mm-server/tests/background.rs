//! The assistant's background work and Tidy's run as `mailmind-server` schedules them (`bootstrap.background`): each on
//! a lane of its own at Python's interval, drafting ahead unless MAILMIND_NO_DRAFT_TICK, none in a process that only
//! reads; and with AI off or a preference not turned on, a round that does nothing.

#![allow(clippy::unwrap_used)]

use std::path::Path;
use std::sync::Arc;

use mm_kernel::seams::{Clock, Keychain, KeychainProblem, Moment, Outside, Secret, Tripped};
use mm_server::environment::{Environment, Settings};
use mm_server::real::Sanitizing;
use mm_server::workers::Workers;
use mm_server::{App, Seams};
use mm_store::{Database, DbKey};
use serde_json::{Value, json};

/// Monday 2 November 2026, noon UTC.
const NOW: f64 = 1_793_620_800.0;

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

/// The app over a data directory with no accounts, no key for AI and `config`, with `pairs` in its environment.
fn assembled(home: &Path, config: &Value, pairs: &[(&str, &str)]) -> Arc<App> {
    std::fs::write(home.join("mailmind.config.json"), config.to_string()).unwrap();
    let (path, key) = (home.join("mailmind.db"), DbKey::from_bytes([0x5e; 32]));
    Database::new(path, key, Arc::new(Closed)).ready(&Frozen).unwrap();
    let key = "5e".repeat(32);
    let environment = Environment::from_pairs([("MAILMIND_DB_KEY", key.as_str())].into_iter().chain(pairs.to_vec()));
    let settings = Settings { home: home.to_path_buf() };
    let seams = Seams {
        clock: Arc::new(Frozen),
        outside: Arc::new(Closed),
        keychain: Arc::new(Empty),
        sanitizer: Arc::new(Sanitizing),
    };
    Arc::new(App::assemble(&environment, &settings, home.join("dist"), 0, &seams).unwrap())
}

const BACKGROUND: [&str; 5] = ["assess", "draft-ahead", "brief-ahead", "review-ahead", "tidy"];

/// Each background job scheduled: how often, and when its first round is due.
fn scheduled(app: &App) -> Vec<(String, f64, f64)> {
    let snapshot = app.jobs.snapshot();
    let mut found = Vec::new();
    for job in snapshot.as_array().unwrap() {
        let name = job["name"].as_str().unwrap();
        if BACKGROUND.contains(&name) {
            let next = job["next_at"].as_f64().unwrap_or(f64::NAN);
            found.push((name.to_owned(), job["every"].as_f64().unwrap(), next - NOW));
        }
    }
    found
}

fn until_scheduled(app: &App, n: usize) -> Vec<(String, f64, f64)> {
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
    loop {
        let found = scheduled(app);
        if found.len() == n && found.iter().all(|(_, _, first)| !first.is_nan()) {
            return found;
        }
        assert!(std::time::Instant::now() < deadline, "never scheduled: {found:?}");
        std::thread::sleep(std::time::Duration::from_millis(5));
    }
}

#[test]
fn each_runs_on_a_lane_of_its_own_at_the_python_apps_interval() {
    let home = tempfile::tempdir().unwrap();
    let app = assembled(home.path(), &json!({}), &[("MAILMIND_PROFILE", "full")]);
    let workers = Workers::start(&app, false, 0, 0);
    let found = until_scheduled(&app, 5);
    workers.stop();
    assert_eq!(
        found,
        [
            ("assess".to_owned(), 300.0, 20.0),
            ("draft-ahead".to_owned(), 180.0, 120.0),
            ("brief-ahead".to_owned(), 600.0, 90.0),
            ("review-ahead".to_owned(), 600.0, 100.0),
            ("tidy".to_owned(), 900.0, 120.0),
        ]
    );
}

#[test]
fn drafting_ahead_is_left_out_with_no_draft_tick_and_all_of_them_where_the_process_only_reads() {
    let home = tempfile::tempdir().unwrap();
    let app = assembled(home.path(), &json!({}), &[("MAILMIND_PROFILE", "full"), ("MAILMIND_NO_DRAFT_TICK", "1")]);
    let workers = Workers::start(&app, false, 0, 0);
    let names: Vec<String> = until_scheduled(&app, 4).into_iter().map(|(name, _, _)| name).collect();
    workers.stop();
    assert_eq!(names, ["assess", "brief-ahead", "review-ahead", "tidy"]);

    let home = tempfile::tempdir().unwrap();
    let app = assembled(home.path(), &json!({}), &[("MAILMIND_PROFILE", "read-only")]);
    let workers = Workers::start(&app, true, 0, 0);
    assert!(app.jobs.names().contains(&"snooze"));
    workers.stop();
    assert_eq!(scheduled(&app), []);
}

#[test]
fn with_ai_off_or_nothing_turned_on_a_round_does_nothing() {
    let home = tempfile::tempdir().unwrap();
    let off = json!({"preferences": {"morning_brief": false, "weekly_review": false, "auto_tidy": false}});
    let app = assembled(home.path(), &off, &[("MAILMIND_PROFILE", "full")]);
    let bg = &app.background;
    assert_eq!(
        [bg.assess_round(), bg.draft_ahead_round(), bg.brief_ahead_round(), bg.review_ahead_round(), bg.tidy_round()],
        [Ok(None); 5]
    );
    // Turned on with no key for AI: Tidy reads what the AI said, so it has nothing to go by.
    let on = json!({"preferences": {"morning_brief": true, "weekly_review": true, "auto_tidy": true}});
    std::fs::write(home.path().join("mailmind.config.json"), on.to_string()).unwrap();
    assert_eq!([bg.brief_ahead_round(), bg.review_ahead_round(), bg.tidy_round()], [Ok(None); 3]);
    let database = Database::new(home.path().join("mailmind.db"), DbKey::from_bytes([0x5e; 32]), Arc::new(Closed));
    let commands: i64 = database.read(|c| c.query_row("SELECT COUNT(*) FROM mail_commands", [], |r| r.get(0))).unwrap();
    assert_eq!(commands, 0);
}

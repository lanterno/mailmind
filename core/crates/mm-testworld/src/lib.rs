//! A test world's time and jobs, as the end-to-end framework holds them (docs/test-architecture.md, section 7): a clock
//! that stands until the test moves it, background jobs paused until it pumps them, settling, and the id of the last
//! event the app published. The served suites' launcher (`mm-served`) answers its line protocol's `clock`, `work`,
//! `settle` and `events` here, and the app in a shell's process (`mm-host`) offers the same to a test's home.
//!
//! An app run this way starts no workers: nothing runs in the background but the work a request sets going, and what
//! a job's lane would have run, kicked or come due, runs only when the test pumps it (`pump`, `pump_kicked`).

pub mod clock;
// One pass of a background job, as its lane would run it.
pub mod jobs;

use std::sync::Arc;
use std::time::Duration;

use mm_kernel::seams::Moment;
use mm_server::App;

use clock::{TestClock, parse_iso};

/// What `settle` waits for the requests being answered, at most, when the protocol's line names no time.
pub const SETTLE: Duration = Duration::from_secs(30);

/// The test's hold on an app's time and jobs: the clock the app was assembled with, and the app.
pub struct Controls {
    clock: Arc<TestClock>,
    app: Arc<App>,
}

impl std::fmt::Debug for Controls {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Controls").field("clock", &self.clock).finish_non_exhaustive()
    }
}

impl Controls {
    /// The controls of `app`, which was assembled with `clock` as its seams' clock and runs no workers.
    pub fn new(clock: Arc<TestClock>, app: Arc<App>) -> Controls {
        Controls { clock, app }
    }

    /// Where the clock stands.
    pub fn now(&self) -> Moment {
        mm_kernel::seams::Clock::now(&*self.clock)
    }

    /// Stand the clock at `moment`.
    pub fn set(&self, moment: Moment) {
        self.clock.move_to(moment);
    }

    /// Move the clock on by `micros`: where it stands now. Nothing runs by itself for it: a job that came due runs
    /// when the test pumps it.
    pub fn advance(&self, micros: i64) -> Moment {
        self.clock.advance(micros)
    }

    /// Kick `job`, as the app kicks one: its pass is left for `pump_kicked`.
    pub fn kick(&self, job: &str) {
        self.app.jobs.kick(job);
    }

    /// One pass of `job` with `arguments` (a folder's sync takes the account and the folder, reconcile the account),
    /// as its lane would run it: `failed <job>: <why>` when it didn't, naming only a class, never a value.
    pub fn pump(&self, job: &str, arguments: &[&str]) -> Result<(), String> {
        let listed = serde_json::Value::from(arguments.to_vec());
        ok_or_failed(jobs::work(&self.app, &format!("{job} {listed}")))
    }

    /// A pass of each job kicked since the last, as its lane would have run it then. One that takes arguments (a
    /// folder's sync) is let go.
    pub fn pump_kicked(&self) -> Result<(), String> {
        ok_or_failed(jobs::work(&self.app, "kicked"))
    }

    /// Wait until no request is being answered, nor any work one set going (`/api/refresh`'s sync), for at most
    /// `timeout`: how many still are, 0 once settled.
    pub fn settle(&self, timeout: Duration) -> usize {
        self.app.work.settle(timeout)
    }

    /// The id of the last event the app published (`Hub::high_water`), which a client has applied once it is settled.
    pub fn high_water(&self) -> String {
        self.app.events.high_water()
    }

    /// The protocol's answer to one of the lines it shares with every launcher, None for another: `clock <ISO 8601>`,
    /// `work <job> <JSON>`, `settle` and `events`.
    pub fn answer(&self, command: &str, argument: &str) -> Option<String> {
        Some(match command {
            "clock" => match parse_iso(argument) {
                Some(moment) => {
                    self.set(moment);
                    "ok".to_owned()
                }
                None => "failed clock: not a time".to_owned(),
            },
            "work" => jobs::work(&self.app, argument),
            "settle" => match self.settle(SETTLE) {
                0 => "ok".to_owned(),
                n => format!("busy {n} request(s) still being answered"),
            },
            "events" => format!("events {}", self.high_water()),
            _ => return None,
        })
    }
}

fn ok_or_failed(said: String) -> Result<(), String> {
    if said == "ok" { Ok(()) } else { Err(said) }
}

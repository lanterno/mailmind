//! The background work (`bootstrap.background`, `platform.workers.scheduler` and what `wiring.start` sets going):
//! the change queue's worker, and the jobs, each on a lane of its own that runs its rounds one after another.
//!
//! A job is a name, a round, the seconds between rounds and the delay before the first. A round may say the seconds
//! until its next, when it knows better than the interval (reconcile spreads one cycle across the accounts). One that
//! fails is logged and counted, and the job goes on at its interval. `Jobs` is what the app holds of them: a kick that
//! runs a job's next round now, and what each last did (`snapshot`, which `/api/debug/jobs` shows).
//!
//! Only `mailmind-server` starts them (`Workers::start`). The served suites start none: their `work` line runs one
//! pass of a job at a time, so what a suite sees depends only on what it sends.

use std::sync::{Arc, Mutex, PoisonError};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use mm_kernel::log::{self, Field};
use mm_kernel::seams::Clock;
use mm_mailbox::workers::{Kick, Queue, Stop, run_forever};
use serde_json::{Value, json};

use crate::app::App;

const TAG: &str = "jobs";
/// Seconds between automatic syncs.
pub const SYNC_INTERVAL: i64 = 300;
/// Seconds between the minute's rounds: snoozes woken, follow-ups brought up to date.
pub const CLOCKED_INTERVAL: i64 = 60;

/// What one round came to: the seconds until the next when it knows better than the interval, or the class of what
/// went wrong.
pub type RoundOutcome = Result<Option<i64>, &'static str>;

/// How a job's rounds have gone. Times are the clock's seconds since the epoch.
#[derive(Debug, Default)]
struct Record {
    runs: u64,
    failures: u64,
    running: bool,
    last_started: Option<f64>,
    last_took: Option<f64>,
    last_error: Option<&'static str>,
    /// When its next round is due; None while a round runs.
    next_at: Option<f64>,
}

/// One job, as the app holds it: its timing, its kick and how its rounds went. Its round runs on its lane.
#[derive(Debug)]
pub struct Job {
    name: &'static str,
    every: i64,
    first: i64,
    kicked: Arc<Kick>,
    /// One round at a time.
    turn: Mutex<()>,
    record: Mutex<Record>,
}

impl Job {
    fn record(&self) -> std::sync::MutexGuard<'_, Record> {
        // Each change to a record is whole by the time the lock is let go.
        self.record.lock().unwrap_or_else(PoisonError::into_inner)
    }
}

/// The jobs this process schedules: none until `Workers::start` puts them here.
#[derive(Debug)]
pub struct Jobs {
    clock: Arc<dyn Clock>,
    jobs: Mutex<Vec<Arc<Job>>>,
}

impl Jobs {
    pub fn new(clock: Arc<dyn Clock>) -> Jobs {
        Jobs { clock, jobs: Mutex::new(Vec::new()) }
    }

    fn now(&self) -> f64 {
        self.clock.now().micros() as f64 / 1e6
    }

    fn held(&self) -> std::sync::MutexGuard<'_, Vec<Arc<Job>>> {
        self.jobs.lock().unwrap_or_else(PoisonError::into_inner)
    }

    /// Schedule `name`, every `every` seconds, the first `first` seconds after its lane starts. A second job of a
    /// name is a mistake in the program.
    pub fn add(&self, name: &'static str, every: i64, first: i64) -> Arc<Job> {
        let mut jobs = self.held();
        assert!(!jobs.iter().any(|j| j.name == name), "a job named {name} is already scheduled");
        let job = Arc::new(Job {
            name,
            every,
            first,
            kicked: Arc::new(Kick::default()),
            turn: Mutex::new(()),
            record: Mutex::new(Record::default()),
        });
        jobs.push(job.clone());
        job
    }

    /// The names of the jobs scheduled.
    pub fn names(&self) -> Vec<&'static str> {
        self.held().iter().map(|j| j.name).collect()
    }

    /// Run the job's next round now rather than when it is due, if this process schedules it.
    pub fn kick(&self, name: &str) {
        if let Some(job) = self.held().iter().find(|j| j.name == name) {
            job.kicked.kick();
        }
    }

    /// One round of `job`, now, in this thread, after any round of it already running: the seconds until its next.
    pub fn run(&self, job: &Job, round: &dyn Fn() -> RoundOutcome) -> i64 {
        let _turn = job.turn.lock().unwrap_or_else(PoisonError::into_inner);
        {
            let mut record = job.record();
            record.running = true;
            record.next_at = None;
            record.last_started = Some(self.now());
        }
        let began = Instant::now();
        let outcome = round();
        let mut record = job.record();
        record.runs += 1;
        record.running = false;
        record.last_took = Some(began.elapsed().as_secs_f64());
        match outcome {
            Ok(next) => {
                record.last_error = None;
                next.map_or(job.every, |seconds| seconds.max(0))
            }
            Err(class) => {
                record.failures += 1;
                record.last_error = Some(class);
                log::warning(TAG, "%(job)s failed", &[(Field::Job, job.name)], Some(class));
                job.every
            }
        }
    }

    /// The lane: wait out the delay, or a kick, then a round, until `stop`.
    pub fn lane(&self, job: &Job, round: &dyn Fn() -> RoundOutcome, stop: &Stop) {
        stop.wakes(job.kicked.clone());
        let mut delay = job.first;
        while !stop.is_set() {
            job.record().next_at = Some(self.now() + delay as f64);
            job.kicked.wait(Duration::from_secs(u64::try_from(delay).unwrap_or_default()));
            if stop.is_set() {
                return;
            }
            delay = self.run(job, round);
        }
    }

    /// Each job's interval, runs and failures, when its last round started and how long it took, its last error,
    /// and when its next round is due (`Scheduler.snapshot`).
    pub fn snapshot(&self) -> Value {
        let jobs = self.held();
        Value::Array(
            jobs.iter()
                .map(|job| {
                    let r = job.record();
                    json!({"name": job.name, "every": job.every as f64, "runs": r.runs, "failures": r.failures,
                           "running": r.running, "last_started": r.last_started, "last_took": r.last_took,
                           "last_error": r.last_error, "next_at": r.next_at})
                })
                .collect(),
        )
    }
}

/// What a lane runs each round.
type Round = Box<dyn Fn() -> RoundOutcome + Send>;

/// The assistant's background jobs, each on a lane of its own (`background.schedule`'s assess, draft-ahead,
/// brief-ahead and review-ahead).
fn background_lanes(app: &Arc<App>) -> Vec<(Arc<Job>, Round)> {
    use crate::background::{ASSESS_FIRST, ASSESS_INTERVAL, BRIEF_AHEAD, REVIEW_AHEAD};
    use mm_assistant::prepared_replies::{DRAFT_TICK_INTERVAL, DRAFT_TICK_STARTUP_DELAY};
    type Of = fn(&crate::background::Background) -> RoundOutcome;
    let mut jobs: Vec<(&'static str, (i64, i64), Of)> =
        vec![("assess", (ASSESS_INTERVAL, ASSESS_FIRST), crate::background::Background::assess_round)];
    if app.background.drafts_ahead {
        jobs.push((
            "draft-ahead",
            (DRAFT_TICK_INTERVAL, DRAFT_TICK_STARTUP_DELAY),
            crate::background::Background::draft_ahead_round,
        ));
    }
    jobs.push(("brief-ahead", BRIEF_AHEAD, crate::background::Background::brief_ahead_round));
    jobs.push(("review-ahead", REVIEW_AHEAD, crate::background::Background::review_ahead_round));
    jobs.into_iter()
        .map(|(name, (every, first), round)| {
            let job = app.jobs.add(name, every, first);
            let app = app.clone();
            let round: Round = Box::new(move || round(&app.background));
            (job, round)
        })
        .collect()
}

/// The workers this process started, which a stop ends once each round in progress returns.
#[derive(Debug)]
pub struct Workers {
    stop: Arc<Stop>,
    threads: Vec<JoinHandle<()>>,
}

/// Reconcile's interval and the delay before its first round, from about how long it takes to go round every account
/// (`interval`), or None when that is 0 and it is off.
pub fn reconcile_timing(interval: i64) -> Option<(i64, i64)> {
    (interval > 0).then(|| (interval.max(mm_mailbox::reconcile::EVERY_AT_LEAST), interval.min(120)))
}

/// The expiry rules' interval and the delay before their first round, from the seconds between rounds (`interval`),
/// or None when that is 0 or below and they are off.
pub fn expire_timing(interval: i64) -> Option<(i64, i64)> {
    (interval > 0).then(|| (interval, interval.min(180)))
}

impl Workers {
    /// Start the background work (`wiring.start`): the minute's round always, since it needs no mail server; and,
    /// unless the process only reads, each durable queue's worker (the change queue's), the sync, reconcile, the
    /// expiry rules and IDLE's workers. `reconcile_secs` is about how long reconcile takes to go round every account, and `expire_secs`
    /// the seconds between the expiry rules' rounds, each 0 for never.
    pub fn start(app: &Arc<App>, read_only: bool, reconcile_secs: i64, expire_secs: i64) -> Workers {
        let stop = Arc::new(Stop::default());
        let mut lanes: Vec<(Arc<Job>, Round)> = Vec::new();
        let clocked = app.clone();
        lanes.push((
            app.jobs.add("snooze", CLOCKED_INTERVAL, CLOCKED_INTERVAL),
            Box::new(move || {
                mm_attention::followups::clocked_round(&clocked.attention.snoozing, &clocked.attention.follow_ups);
                Ok(None)
            }),
        ));
        let mut queues: Vec<Arc<dyn Queue>> = Vec::new();
        if !read_only {
            queues.push(app.commands.clone());
            let syncing = app.syncing.clone();
            lanes.push((
                app.jobs.add("sync", SYNC_INTERVAL, 0),
                Box::new(move || {
                    syncing.sync_once();
                    Ok(None)
                }),
            ));
            if let Some((every, first)) = reconcile_timing(reconcile_secs) {
                let reconciling = app.clone();
                lanes.push((
                    app.jobs.add("reconcile", every, first),
                    Box::new(move || Ok(Some(reconciling.reconciler.round(reconcile_secs)))),
                ));
            }
            // The outbox's worker, which a send kicks, and the drafts saved here pushed to their servers.
            queues.push(app.compose.queue());
            let pushing = app.clone();
            lanes.push((
                app.jobs.add("draft-push", mm_compose::draft_journal::PUSH_TICK, 5),
                Box::new(move || pushing.compose.drafting.push_round().map(|_| None).map_err(|fault| fault.class)),
            ));
            if let Some((every, first)) = expire_timing(expire_secs) {
                let expiring = app.clone();
                lanes.push((
                    app.jobs.add("expire", every, first),
                    Box::new(move || {
                        expiring.cleanups.expiry.round().map_err(|fault| fault.class)?;
                        Ok(None)
                    }),
                ));
            }
            // What has gone, let go a month on: keys with no copy left and finished changes, daily. It writes, so
            // a process that only reads leaves it, as the Python app's does.
            let forgetting = app.clone();
            lanes.push((
                app.jobs.add("forget", mm_mailbox::forgetting::EVERY, mm_mailbox::forgetting::FIRST),
                Box::new(move || forgetting.forgetting.round().map(|_| None).map_err(|fault| fault.class)),
            ));
            // The assistant's background work: triage, which a sync with new mail kicks; replies drafted ahead,
            // unless MAILMIND_NO_DRAFT_TICK; and the opt-in rounds, the brief and the review written ahead.
            lanes.extend(background_lanes(app));
        }
        let mut threads = Vec::new();
        for queue in queues {
            let stop = stop.clone();
            let name = format!("worker:{}", queue.tag());
            threads.extend(std::thread::Builder::new().name(name).spawn(move || run_forever(&*queue, &stop)));
        }
        for (job, round) in lanes {
            let (stop, app) = (stop.clone(), app.clone());
            let name = format!("job:{}", job.name);
            threads.extend(std::thread::Builder::new().name(name).spawn(move || app.jobs.lane(&job, &*round, &stop)));
        }
        // IDLE's workers, one for each account, unless the process only reads (`realtime.start`).
        if !read_only {
            threads.extend(crate::idle_wiring::watch(app, &stop));
        }
        if read_only {
            log::info(
                TAG,
                "(auto-sync disabled: the read-only profile writes nothing, so moves wait in their queue; snooze still runs)",
                &[],
            );
        }
        Workers { stop, threads }
    }

    /// End every worker once its round in progress returns, and wait for them.
    pub fn stop(self) {
        self.stop.stop();
        for thread in self.threads {
            let _ = thread.join();
        }
    }
}

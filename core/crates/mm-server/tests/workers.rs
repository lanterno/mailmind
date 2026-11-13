//! The background jobs' timetable: a lane runs its job's rounds one after another, at its interval or when its round
//! says, now when kicked, and a stop ends it; and what each job last did, as `/api/debug/jobs` shows it.

#![allow(clippy::unwrap_used)]

use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::{Duration, Instant};

use mm_kernel::seams::{Clock, Moment};
use mm_mailbox::workers::Stop;
use mm_server::workers::{Jobs, RoundOutcome, reconcile_timing};
use serde_json::json;

#[derive(Debug)]
struct Frozen;

impl Clock for Frozen {
    fn now(&self) -> Moment {
        Moment::from_micros(1_793_620_800_000_000)
    }
}

fn until(what: impl Fn() -> bool) {
    let deadline = Instant::now() + Duration::from_secs(5);
    while !what() {
        assert!(Instant::now() < deadline, "it never came");
        std::thread::sleep(Duration::from_millis(5));
    }
}

#[test]
fn a_round_is_counted_and_says_when_the_next_comes() {
    let jobs = Jobs::new(Arc::new(Frozen));
    let job = jobs.add("reconcile", 1800, 120);
    assert_eq!(jobs.names(), ["reconcile"]);
    // A round that knows better than the interval says so; one that doesn't is at the interval.
    assert_eq!(jobs.run(&job, &|| Ok(Some(600))), 600);
    assert_eq!(jobs.run(&job, &|| Ok(None)), 1800);
    assert_eq!(jobs.run(&job, &|| Ok(Some(-5))), 0);
    // One that failed is counted, named by its class only, and the job goes on at its interval.
    assert_eq!(jobs.run(&job, &|| Err("Unreachable")), 1800);
    let snapshot = jobs.snapshot();
    let said = &snapshot[0];
    assert_eq!(
        (&said["name"], &said["every"], &said["runs"], &said["failures"], &said["running"]),
        (&json!("reconcile"), &json!(1800.0), &json!(4), &json!(1), &json!(false))
    );
    assert_eq!((&said["last_error"], &said["last_started"]), (&json!("Unreachable"), &json!(1_793_620_800.0)));
    assert!(said["last_took"].is_f64());
    // No lane runs it: nothing says when the next is due.
    assert_eq!(said["next_at"], json!(null));
    assert_eq!(said.as_object().unwrap().len(), 9);
    jobs.run(&job, &|| Ok(None));
    assert_eq!(jobs.snapshot()[0]["last_error"], json!(null));
}

#[test]
#[should_panic(expected = "a job named sync is already scheduled")]
fn a_job_is_scheduled_once() {
    let jobs = Jobs::new(Arc::new(Frozen));
    jobs.add("sync", 300, 0);
    jobs.add("sync", 300, 0);
}

#[test]
fn a_lane_runs_its_first_round_after_its_delay_then_whenever_kicked_until_stopped() {
    let jobs = Arc::new(Jobs::new(Arc::new(Frozen)));
    let job = jobs.add("snooze", 60, 60);
    let rounds = Arc::new(AtomicUsize::new(0));
    let stop = Arc::new(Stop::default());
    let lane = {
        let (jobs, job, rounds, stop) = (jobs.clone(), job.clone(), rounds.clone(), stop.clone());
        std::thread::spawn(move || {
            let round = move || -> RoundOutcome {
                rounds.fetch_add(1, Ordering::SeqCst);
                Ok(None)
            };
            jobs.lane(&job, &round, &stop);
        })
    };
    // Waiting out its first minute, and saying when that ends.
    until(|| jobs.snapshot()[0]["next_at"] == json!(1_793_620_860.0));
    assert_eq!(rounds.load(Ordering::SeqCst), 0);
    jobs.kick("snooze");
    until(|| rounds.load(Ordering::SeqCst) == 1);
    // A job this process doesn't schedule is no kick at all.
    jobs.kick("assess");
    jobs.kick("snooze");
    until(|| rounds.load(Ordering::SeqCst) == 2);
    let began = Instant::now();
    stop.stop();
    lane.join().unwrap();
    assert!(began.elapsed() < Duration::from_secs(30));
    assert_eq!(rounds.load(Ordering::SeqCst), 2);
    assert_eq!(jobs.snapshot()[0]["runs"], json!(2));
}

#[test]
fn reconcile_runs_at_most_twice_a_minute_and_first_within_two_minutes_or_not_at_all() {
    assert_eq!(reconcile_timing(1800), Some((1800, 120)));
    assert_eq!(reconcile_timing(10), Some((30, 10)));
    assert_eq!(reconcile_timing(0), None);
    assert_eq!(reconcile_timing(-1), None);
}

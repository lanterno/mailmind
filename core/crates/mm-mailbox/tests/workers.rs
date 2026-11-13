//! What the background workers wait on: a kick that wakes one now, a stop that ends them, and the worker that drains a
//! durable queue.

#![allow(clippy::unwrap_used)]

use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::{Duration, Instant};

use mm_kernel::errors::Fault;
use mm_mailbox::workers::{Kick, Queue, Stop, run_forever};

/// Long enough that a test which waits it out has gone wrong.
const LONG: Duration = Duration::from_secs(30);

fn until(what: impl Fn() -> bool) {
    let deadline = Instant::now() + Duration::from_secs(5);
    while !what() {
        assert!(Instant::now() < deadline, "it never came");
        std::thread::sleep(Duration::from_millis(5));
    }
}

#[test]
fn a_kick_wakes_a_wait_and_is_used_up() {
    let kick = Kick::default();
    // One that comes before the wait isn't missed.
    kick.kick();
    let began = Instant::now();
    assert!(kick.wait(LONG));
    assert!(began.elapsed() < LONG);
    // And it is used up: the next wait waits it out.
    assert!(!kick.wait(Duration::from_millis(20)));
}

#[test]
fn a_kick_from_another_thread_wakes_one_waiting() {
    let kick = Arc::new(Kick::default());
    let waiting = {
        let kick = kick.clone();
        std::thread::spawn(move || kick.wait(LONG))
    };
    std::thread::sleep(Duration::from_millis(20));
    kick.kick();
    assert!(waiting.join().unwrap());
}

#[derive(Debug, Default)]
struct Counted {
    kicked: Arc<Kick>,
    recovered: AtomicUsize,
    drained: AtomicUsize,
    /// What the next drain says went wrong, once.
    failing: AtomicUsize,
}

impl Queue for Counted {
    fn tag(&self) -> &'static str {
        "test"
    }

    fn kicked(&self) -> Arc<Kick> {
        self.kicked.clone()
    }

    fn recover(&self) -> Result<(), Fault> {
        self.recovered.fetch_add(1, Ordering::SeqCst);
        Ok(())
    }

    fn now(&self) -> i64 {
        1_793_620_800
    }

    fn until_due(&self, _looked: i64) -> i64 {
        30
    }

    fn drain(&self) -> Result<(), Fault> {
        self.drained.fetch_add(1, Ordering::SeqCst);
        if self.failing.swap(0, Ordering::SeqCst) > 0 { Err(Fault::new("Failed")) } else { Ok(()) }
    }
}

#[test]
fn the_worker_puts_back_what_a_crash_left_then_drains_each_time_it_is_kicked_until_stopped() {
    let queue = Arc::new(Counted::default());
    let stop = Arc::new(Stop::default());
    let worker = {
        let (queue, stop) = (queue.clone(), stop.clone());
        std::thread::spawn(move || run_forever(&*queue, &stop))
    };
    until(|| queue.recovered.load(Ordering::SeqCst) == 1);
    // Nothing is due for 30 seconds: only a kick brings a pass.
    assert_eq!(queue.drained.load(Ordering::SeqCst), 0);
    queue.failing.store(1, Ordering::SeqCst);
    queue.kicked.kick();
    until(|| queue.drained.load(Ordering::SeqCst) == 1);
    // A pass that failed is logged, and the next kick still brings one.
    queue.kicked.kick();
    until(|| queue.drained.load(Ordering::SeqCst) == 2);
    // A stop wakes it from its wait, and it ends with no pass more.
    let began = Instant::now();
    stop.stop();
    worker.join().unwrap();
    assert!(began.elapsed() < LONG);
    assert_eq!(queue.drained.load(Ordering::SeqCst), 2);
    assert_eq!(queue.recovered.load(Ordering::SeqCst), 1);
}

#[test]
fn a_worker_started_after_the_stop_ends_at_once() {
    let queue = Counted::default();
    let stop = Stop::default();
    stop.stop();
    assert!(stop.is_set());
    run_forever(&queue, &stop);
    assert_eq!(queue.drained.load(Ordering::SeqCst), 0);
}

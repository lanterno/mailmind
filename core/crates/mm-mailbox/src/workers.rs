//! What the Mailbox's background workers wait on (`platform.workers`): a `Kick` that wakes a worker now rather than
//! at its next poll, a `Stop` that ends every worker once its round in progress returns, and the worker a durable
//! queue is drained by (`DurableQueue.run_forever`), the change queue's and any other's.
//!
//! A worker runs on a thread of its own, which the program starts (`mailmind-server`); the served suites start none
//! and run each pass themselves (`work`). Waiting is all a worker does with time here: when a row comes due is the
//! clock's to say.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Condvar, Mutex, PoisonError};
use std::time::Duration;

use mm_kernel::errors::Fault;
use mm_kernel::log;

/// Wakes a worker that waits: a kick that comes while it is busy wakes its next wait at once, so none is missed.
#[derive(Debug, Default)]
pub struct Kick {
    kicked: Mutex<bool>,
    woken: Condvar,
}

impl Kick {
    /// Wake the worker now.
    pub fn kick(&self) {
        *self.kicked.lock().unwrap_or_else(PoisonError::into_inner) = true;
        self.woken.notify_all();
    }

    /// Wait until kicked, or for `timeout`; whether it was kicked. The kick is used up.
    pub fn wait(&self, timeout: Duration) -> bool {
        let kicked = self.kicked.lock().unwrap_or_else(PoisonError::into_inner);
        let (mut kicked, _) =
            self.woken.wait_timeout_while(kicked, timeout, |kicked| !*kicked).unwrap_or_else(PoisonError::into_inner);
        std::mem::take(&mut *kicked)
    }
}

/// Ends the workers: each stops once its round in progress returns, and one waiting wakes to stop.
#[derive(Debug, Default)]
pub struct Stop {
    stopped: AtomicBool,
    /// The kicks of the workers waiting, which a stop wakes.
    waiting: Mutex<Vec<Arc<Kick>>>,
}

impl Stop {
    /// A worker that waits on `kick` is woken by a stop.
    pub fn wakes(&self, kick: Arc<Kick>) {
        self.waiting.lock().unwrap_or_else(PoisonError::into_inner).push(kick);
    }

    pub fn stop(&self) {
        self.stopped.store(true, Ordering::SeqCst);
        for kick in self.waiting.lock().unwrap_or_else(PoisonError::into_inner).iter() {
            kick.kick();
        }
    }

    pub fn is_set(&self) -> bool {
        self.stopped.load(Ordering::SeqCst)
    }
}

/// A queue kept in the database that a worker drains (`DurableQueue`): what it needs of one.
pub trait Queue: Send + Sync {
    /// What its log lines are tagged.
    fn tag(&self) -> &'static str;
    /// What wakes its worker now: something was queued.
    fn kicked(&self) -> Arc<Kick>;
    /// At boot: rows a crash left running go back to the queue.
    fn recover(&self) -> Result<(), Fault>;
    /// The clock's seconds now.
    fn now(&self) -> i64;
    /// Seconds from now until the next row comes due, at most a poll, and none when one has come due since the last
    /// pass began to look (`looked`).
    fn until_due(&self, looked: i64) -> i64;
    /// One pass over what is due.
    fn drain(&self) -> Result<(), Fault>;
}

/// The worker (`DurableQueue.run_forever`): put back what a crash left running, then a pass whenever kicked, when the
/// next row comes due (a retry's backoff ending), and at least every poll, until `stop`. A pass that fails is
/// logged, and the next comes as it would have.
pub fn run_forever(queue: &dyn Queue, stop: &Stop) {
    let kicked = queue.kicked();
    stop.wakes(kicked.clone());
    if let Err(fault) = queue.recover() {
        log::error(queue.tag(), "rows left running weren't put back", &[], Some(fault.class));
    }
    let mut looked = queue.now();
    while !stop.is_set() {
        kicked.wait(Duration::from_secs(u64::try_from(queue.until_due(looked)).unwrap_or_default()));
        if stop.is_set() {
            return;
        }
        looked = queue.now();
        if let Err(fault) = queue.drain() {
            log::error(queue.tag(), "drain error", &[], Some(fault.class));
        }
    }
}

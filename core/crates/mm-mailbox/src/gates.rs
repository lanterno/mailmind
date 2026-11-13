//! The gate each account's live-IMAP work passes (`mailmind.platform.workers.gates`), as the use cases ask for it:
//! a job runs with its account's gate held, so no two jobs talk to one account's server at once. The program keeps
//! the gates (one for every account, or one for each, as MAILMIND_WORKERS says) and implements `Gates`.
//!
//! A job runs on its caller's own thread. A job never waits on a gate itself: one that would is refused, rather than
//! wait forever on its own thread.

use std::fmt;
use std::time::Duration;

/// What passes EVERY_ACCOUNT waits until no gate is held, and holds them all.
pub const EVERY_ACCOUNT: &str = "*";

/// Which job runs first when several wait for one gate.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Priority {
    /// The user waits on it: open a body, file a message, undo, list folders.
    Interactive = 0,
    /// Set going by the user, done after: queued moves, a send's Sent copy.
    AfterUser = 1,
    /// Bringing a folder up to date.
    Sync = 2,
    /// Reconcile, body backfill, prefetch, expiry.
    Maintenance = 3,
}

/// How long a job waits for its gate.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Wait {
    /// Not at all: a held gate is `Busy` at once.
    Not,
    /// At most this long.
    For(Duration),
    /// Until the gate comes free.
    Ever,
}

/// Why a job didn't run.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Refused {
    /// Its gate didn't come free in time (`gates.Busy`).
    Busy,
    /// It would have waited on a gate from inside a job.
    Nested,
}

/// The gates, as the program keeps them.
pub trait Gates: Send + Sync + fmt::Debug {
    /// Run `job` with `account`'s gate held; the gate comes free when it returns.
    fn run(&self, account: &str, priority: Priority, wait: Wait, job: &mut dyn FnMut()) -> Result<(), Refused>;

    /// Whether `account`'s gate is held now, by any job; for EVERY_ACCOUNT, whether any is.
    fn held(&self, account: &str) -> bool;
}

/// Run `job` with `account`'s gate held, and what it answered.
pub fn on_gate<T>(
    gates: &dyn Gates,
    account: &str,
    priority: Priority,
    wait: Wait,
    job: impl FnOnce() -> T,
) -> Result<T, Refused> {
    let mut job = Some(job);
    let mut answer = None;
    gates.run(account, priority, wait, &mut || {
        if let Some(job) = job.take() {
            answer = Some(job());
        }
    })?;
    answer.ok_or(Refused::Busy)
}

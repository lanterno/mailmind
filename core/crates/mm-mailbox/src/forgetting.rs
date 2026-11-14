//! What is kept of mail that has gone, let go once a month has passed (the Mailbox's part of
//! `bootstrap.background.forget_round`): a message key no copy has come back to, and with it what was said of the
//! message, and a change-queue row that finished. Until then a copy that comes back (a move, an Undo, a resync)
//! finds its key and its assessment, and a finished change can still be undone or found resubmitted.
//!
//! The Assistant's part of the round, the batches that finished, is `mm_assistant::batches`.

use std::fmt;
use std::sync::Arc;

use mm_kernel::errors::Fault;
use mm_kernel::seams::{Clock, Moment};
use mm_kernel::when;

/// How long what has gone is kept: thirty days.
pub const KEPT_FOR: i64 = 30 * 86_400;
/// Seconds between rounds: a day.
pub const EVERY: i64 = 86_400;
/// Seconds before the first, once the workers start.
pub const FIRST: i64 = 600;

/// How many of each a round let go.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Forgotten {
    pub keys: usize,
    pub commands: usize,
}

/// What a round lets go of, in one transaction.
pub trait ForgetStore: Send + Sync + fmt::Debug {
    /// The message keys whose last copy went before `gone_before` (seconds since the epoch, as `gone_at` holds
    /// them), with what hangs on them, and the change-queue rows done before `done_before` (as `done_at` is
    /// written). A row still open, or in `error` waiting for a person, stays.
    fn forget(&self, gone_before: i64, done_before: &str) -> Result<Forgotten, Fault>;
}

/// The round, daily.
#[derive(Debug)]
pub struct Forgetting {
    store: Arc<dyn ForgetStore>,
    clock: Arc<dyn Clock>,
}

impl Forgetting {
    pub fn new(store: Arc<dyn ForgetStore>, clock: Arc<dyn Clock>) -> Forgetting {
        Forgetting { store, clock }
    }

    /// Let go of what went more than `KEPT_FOR` ago, timed in the clock's whole seconds as Python's
    /// `int(time.time())` is.
    pub fn round(&self) -> Result<Forgotten, Fault> {
        let before = self.clock.now().whole_seconds() - KEPT_FOR;
        self.store.forget(before, &when::iso(Moment::from_micros(before * 1_000_000)))
    }
}

//! The Assistant's part of the forget round (`answers.batch_forget_finished`): a batch the Python app sent to the
//! Claude API, let go a month after it ended, its items with it. The Rust server sends no batch of its own yet, so
//! these are the Python app's.

use mm_kernel::errors::Fault;
use mm_kernel::seams::Clock;

use crate::background_ports::FinishedBatches;

/// How long a finished batch is kept: thirty days.
pub const KEPT_FOR: i64 = 30 * 86_400;

/// Let go of the batches that finished more than `KEPT_FOR` ago, timed in the clock's whole seconds as Python's
/// `int(time.time())` is: how many went.
pub fn forget_finished(store: &dyn FinishedBatches, clock: &dyn Clock) -> Result<usize, Fault> {
    store.forget_finished(clock.now().whole_seconds() - KEPT_FOR)
}

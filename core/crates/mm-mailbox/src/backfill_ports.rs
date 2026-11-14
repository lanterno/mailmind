//! What the body backfill needs of the world (`mailbox.application.ports` as `backfill_round` uses them): the stored
//! mail with no body text, which the store (`mm-store`) reads, and one body fetched on its account's gate, which
//! `fetching` does.

use std::fmt;

use mm_kernel::errors::Fault;

/// The stored mail whose body has yet to be fetched.
pub trait BodiesWanted: Send + Sync + fmt::Debug {
    /// An account's newest `limit` messages with no body text, none on its way out, but for the pks in `skip`.
    fn bodies_to_fetch(&self, account: &str, skip: &[i64], limit: usize) -> Result<Vec<i64>, Fault>;
    /// Which of these pks still have no body text.
    fn still_without_body(&self, pks: &[i64]) -> Result<Vec<i64>, Fault>;
}

/// What one try at a message's body came to.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Tried {
    /// The try ran: the body was kept, or wasn't given, or the row was gone, or something else went wrong. Whether
    /// the body came is the store's to say.
    Ran,
    /// Its account's gate was held: a person's work is live there (`gates.Busy`).
    Busy,
    /// The connection failed or was lost (`UNREACHABLE`).
    Unreachable,
}

/// One message's body fetched and kept for the backfill: on its account's gate at background priority, never waiting
/// for it.
pub trait BackfillFetch: Send + Sync + fmt::Debug {
    fn try_body(&self, account: &str, pk: i64) -> Tried;
}

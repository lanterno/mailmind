//! What the body backfill needs of the world (`mailbox.application.ports` as `backfill_round` uses them): the stored
//! mail with no body text, which the store (`mm-store`) reads, and a folder's few bodies fetched on their account's
//! gate, which `fetching` does.

use std::fmt;

use mm_kernel::errors::Fault;

/// A message whose body the backfill wants, and where it is on its server.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Wanted {
    pub pk: i64,
    pub folder: String,
    pub uid: u32,
}

/// The stored mail whose body has yet to be fetched.
pub trait BodiesWanted: Send + Sync + fmt::Debug {
    /// An account's newest `limit` messages with no body text, dated `since` or later (Unix seconds), none on its way
    /// out, but for the pks in `skip`: newest first.
    fn bodies_to_fetch(&self, account: &str, skip: &[i64], since: i64, limit: usize) -> Result<Vec<Wanted>, Fault>;
    /// Which of these pks still have no body text.
    fn still_without_body(&self, pks: &[i64]) -> Result<Vec<i64>, Fault>;
}

/// What one try at a folder's few bodies came to.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Tried {
    /// The try ran: each body was kept, or wasn't given, or its row was gone, or something else went wrong. Which
    /// came is the store's to say.
    Ran,
    /// Its account's gate was held: a person's work is live there (`gates.Busy`).
    Busy,
    /// The connection failed or was lost (`UNREACHABLE`).
    Unreachable,
}

/// A few bodies of one folder fetched and kept for the backfill: on their account's gate at background priority,
/// never waiting for it, over the connection its jobs share, several a FETCH.
pub trait BackfillFetch: Send + Sync + fmt::Debug {
    /// `messages` are (pk, UID) of `folder`.
    fn try_bodies(&self, account: &str, folder: &str, messages: &[(i64, u32)]) -> Tried;
}

//! How the body backfill shares its rounds, and what a failure costs (`mailbox.domain.body_backfill`, the
//! BodyBackfillPolicy). Values in and values out: nothing here asks a server, the database or the clock.
//!
//! A round asks each account in turn, each one's newest first, so no account's backlog, outage or busy gate holds
//! another's mail back. A failure sets its message aside, so the next try at that account reaches the mail behind it:
//!
//! - A body the server didn't give (deleted elsewhere and not yet pruned here, or refused) waits `NOT_GIVEN_WAIT`.
//! - A connection lost over a message doesn't say which was at fault. A server that is down, and a message it can't
//!   serve, look alike from here, so each pays a little. The account rests for `UNREACHABLE_REST` and is asked nothing
//!   more until then. The message waits a minute, then twice that each time, up to `NOT_GIVEN_WAIT`, so one that no try
//!   can fetch costs its account a turn less and less often, and an outage costs each message it fell on no more than
//!   that message's wait.
//!
//! The Mailbox keeps what this hands back in memory: a restart tries everything again, which costs a few turns and
//! nothing else. Times are the clock's microseconds since the epoch.

use std::collections::HashMap;

/// How many bodies a round asks for.
pub const ROUND: usize = 12;
/// Seconds to the next round while there is mail left to ask for.
pub const BACKLOG_WAIT: i64 = 8;
/// Seconds to the next round with none: new mail lands body-less.
pub const CAUGHT_UP_WAIT: i64 = 600;
/// Seconds a message waits once its server answered and gave no body.
pub const NOT_GIVEN_WAIT: i64 = 6 * 3600;
/// Seconds an account rests once a connection to it was lost.
pub const UNREACHABLE_REST: i64 = 60;

const MICROS: i64 = 1_000_000;

/// A message a round isn't to ask for until `until`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct SetAside {
    pub until: i64,
    /// In a row, over this message.
    pub connections_lost: u32,
}

/// The server answered, and gave no body.
pub fn not_given(now: i64) -> SetAside {
    SetAside { until: now.saturating_add(NOT_GIVEN_WAIT * MICROS), connections_lost: 0 }
}

/// The connection went while asking for this message's body, `was` being how it stood before.
pub fn connection_lost(was: Option<SetAside>, now: i64) -> SetAside {
    let lost = was.map_or(0, |w| w.connections_lost).saturating_add(1);
    let doubled = 2i64.checked_pow(lost - 1).map_or(i64::MAX, |times| UNREACHABLE_REST.saturating_mul(times));
    SetAside { until: now.saturating_add(doubled.min(NOT_GIVEN_WAIT) * MICROS), connections_lost: lost }
}

/// The messages a round leaves out at `now`. One whose wait is over stays in `set_aside`, which remembers its count.
pub fn still_waiting(set_aside: &HashMap<i64, SetAside>, now: i64) -> Vec<i64> {
    let mut waiting: Vec<i64> = set_aside.iter().filter(|(_, one)| one.until > now).map(|(pk, _)| *pk).collect();
    waiting.sort_unstable();
    waiting
}

/// What a round asks for, in order, of what each account has `waiting`: each one's first, then each one's second, and
/// so on, up to `limit`.
pub fn in_turn<T: Clone>(waiting: &[Vec<T>], limit: usize) -> Vec<T> {
    (0..limit).flat_map(|i| waiting.iter().filter_map(move |rows| rows.get(i).cloned())).take(limit).collect()
}

/// Seconds until the next round. `going`: an account has mail left to ask for and isn't resting. `resting`: one is.
pub fn next_round(going: bool, resting: bool) -> i64 {
    if going {
        BACKLOG_WAIT
    } else if resting {
        UNREACHABLE_REST
    } else {
        CAUGHT_UP_WAIT
    }
}

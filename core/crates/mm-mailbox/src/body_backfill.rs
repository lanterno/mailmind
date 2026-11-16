//! How the body backfill shares its rounds, and what a failure costs (`mailbox.domain.body_backfill`, the
//! BodyBackfillPolicy). Values in and values out: nothing here asks a server, the database or the clock.
//!
//! A round asks each account in turn, each one's newest first, so no account's backlog, outage or busy gate holds
//! another's mail back. It reaches back `REACH` only: older mail is fetched as it is opened. An account's messages of
//! one folder go together, up to `BATCH` a FETCH (`batches`). A failure sets its message aside, so the next try at that
//! account reaches the mail behind it:
//!
//! - A body the server didn't give (deleted elsewhere and not yet pruned here, or refused) waits `NOT_GIVEN_WAIT`.
//! - A connection lost over a message doesn't say which was at fault. A server that is down, and a message it can't
//!   serve, look alike from here, so each pays a little. The account rests for `UNREACHABLE_REST` and is asked nothing
//!   more until then. The message waits a minute, then twice that each time, up to `NOT_GIVEN_WAIT`, so one that no try
//!   can fetch costs its account a turn less and less often, and an outage costs each message it fell on no more than
//!   that message's wait. A batch the connection was lost over sets each of its messages aside, and each goes alone
//!   from then on, so one that no try can fetch costs no other message its turn again.
//!
//! The Mailbox keeps what this hands back in memory: a restart tries everything again, which costs a few turns and
//! nothing else. Times are the clock's microseconds since the epoch.

use std::collections::{HashMap, HashSet};

use crate::backfill_ports::Wanted;

/// About how many bodies a round asks for: the batch that reaches it is the round's last.
pub const ROUND: usize = 48;
/// The most messages a FETCH of their openings asks for.
pub const BATCH: usize = 12;
/// How far back the backfill reaches, in seconds: twelve months.
pub const REACH: i64 = 365 * 86_400;
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

/// A few messages of one folder, asked for in one FETCH: (pk, UID), newest first.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Batch {
    pub folder: String,
    pub messages: Vec<(i64, u32)>,
}

/// One account's `wanted` (newest first) as a round asks for them: a folder's together, up to `BATCH` a FETCH, the
/// batch holding the newest first; but each of `alone`, which a lost connection fell on before, in a batch of its own.
pub fn batches(wanted: &[Wanted], alone: &HashSet<i64>) -> Vec<Batch> {
    let mut out: Vec<Batch> = Vec::new();
    // Each folder's batch that still has room, by where it is in `out`.
    let mut open: HashMap<&str, usize> = HashMap::new();
    for one in wanted {
        let message = (one.pk, one.uid);
        if alone.contains(&one.pk) {
            out.push(Batch { folder: one.folder.clone(), messages: vec![message] });
            continue;
        }
        match open.get(one.folder.as_str()).copied() {
            Some(at) if out[at].messages.len() < BATCH => out[at].messages.push(message),
            _ => {
                open.insert(&one.folder, out.len());
                out.push(Batch { folder: one.folder.clone(), messages: vec![message] });
            }
        }
    }
    out
}

/// What a round asks for, in order, of what each account has `waiting`: each one's first, then each one's second, and
/// so on, until `limit` is reached, each counting `size`; the one that reaches it is the last.
pub fn in_turn<T: Clone>(waiting: &[Vec<T>], limit: usize, size: impl Fn(&T) -> usize) -> Vec<T> {
    let longest = waiting.iter().map(Vec::len).max().unwrap_or(0);
    let mut out = Vec::new();
    let mut asked = 0;
    for i in 0..longest {
        for one in waiting.iter().filter_map(|rows| rows.get(i)) {
            if asked >= limit {
                return out;
            }
            asked += size(one);
            out.push(one.clone());
        }
    }
    out
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

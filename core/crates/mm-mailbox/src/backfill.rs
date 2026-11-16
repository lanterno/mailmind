//! The body backfill (`Syncing.backfill_round`): old mail's bodies fetched a little at a time, so it is full-text
//! searchable. A round asks for about four dozen messages of the last twelve months that have none, each account in
//! turn, newest first, a folder's dozen in a FETCH, a second and a half apart, through the account's gate at background
//! priority and on the connection its jobs share, giving way at once to a person's work there.
//! What each failure costs, and whom, is `body_backfill`'s to say: none of it holds another message or another
//! account back.
//!
//! What a round sets aside, and which accounts rest, is kept here in memory, as the Python app keeps it: a restart
//! tries everything again. One round runs at a time.

use std::collections::{HashMap, HashSet};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use mm_kernel::errors::Fault;
use mm_kernel::seams::Clock;

use crate::backfill_ports::{BackfillFetch, BodiesWanted, Tried};
use crate::body_backfill::{self, Batch, REACH, ROUND, SetAside, UNREACHABLE_REST};
use crate::command_ports::MoveAccounts;

/// Seconds between the job's rounds, when a round says nothing better, and before its first.
pub const EVERY: i64 = 600;
pub const FIRST: i64 = 45;
/// The pause between two batches a round asks for: gentle pacing on the servers.
pub const PAUSE: Duration = Duration::from_millis(1500);

const POISONED: Fault = Fault::new("PoisonedLock");

#[derive(Debug, Default)]
struct Held {
    /// The messages set aside: when a round may ask for each one's body again.
    missed: HashMap<i64, SetAside>,
    /// The accounts resting: when a round may ask anything of each again.
    resting: HashMap<String, i64>,
}

#[derive(Debug)]
pub struct Backfill {
    accounts: Arc<dyn MoveAccounts>,
    wanted: Arc<dyn BodiesWanted>,
    fetch: Arc<dyn BackfillFetch>,
    clock: Arc<dyn Clock>,
    pause: Duration,
    held: Mutex<Held>,
}

/// What wires Backfill.
#[derive(Debug)]
pub struct BackfillParts {
    pub accounts: Arc<dyn MoveAccounts>,
    pub wanted: Arc<dyn BodiesWanted>,
    pub fetch: Arc<dyn BackfillFetch>,
    pub clock: Arc<dyn Clock>,
    /// Between two fetches: `PAUSE`.
    pub pause: Duration,
}

impl Backfill {
    pub fn new(parts: BackfillParts) -> Backfill {
        let BackfillParts { accounts, wanted, fetch, clock, pause } = parts;
        Backfill { accounts, wanted, fetch, clock, pause, held: Mutex::new(Held::default()) }
    }

    /// One round: the seconds until the next.
    pub fn round(&self) -> Result<i64, Fault> {
        let mut held = self.held.lock().map_err(|_| POISONED)?;
        let now = self.clock.now().micros();
        held.resting.retain(|_, until| *until > now);
        let emails: Vec<String> =
            self.accounts.emails()?.into_iter().filter(|email| !held.resting.contains_key(email)).collect();
        // What was set aside and has its body now, opened meanwhile, or is gone, is forgotten.
        let missed: Vec<i64> = held.missed.keys().copied().collect();
        let left: HashSet<i64> = self.wanted.still_without_body(&missed)?.into_iter().collect();
        held.missed.retain(|pk, _| left.contains(pk));
        let skip = body_backfill::still_waiting(&held.missed, now);
        let since = now.div_euclid(1_000_000).saturating_sub(REACH);
        let mut waiting = Vec::with_capacity(emails.len());
        for email in &emails {
            let wanted = self.wanted.bodies_to_fetch(email, &skip, since, ROUND)?;
            // What a lost connection fell on before goes alone.
            let alone: HashSet<i64> = wanted
                .iter()
                .filter(|one| held.missed.get(&one.pk).is_some_and(|missed| missed.connections_lost > 0))
                .map(|one| one.pk)
                .collect();
            let batches = body_backfill::batches(&wanted, &alone);
            waiting.push(batches.into_iter().map(|batch| (email.clone(), batch)).collect::<Vec<(String, Batch)>>());
        }
        let mut tried = Vec::new();
        // The accounts whose turn this round has ended.
        let mut over: HashSet<String> = HashSet::new();
        let mut paced = false;
        for (account, batch) in body_backfill::in_turn(&waiting, ROUND, |(_, batch)| batch.messages.len()) {
            if over.contains(&account) {
                continue;
            }
            if paced {
                std::thread::sleep(self.pause);
            }
            paced = false;
            let pks = batch.messages.iter().map(|(pk, _)| *pk);
            match self.fetch.try_bodies(&account, &batch.folder, &batch.messages) {
                // A person's work is live there: that account's turn ends, and the next round tries again.
                Tried::Busy => {
                    over.insert(account);
                }
                Tried::Unreachable => {
                    let lost_at = self.clock.now().micros();
                    for pk in pks {
                        let was = held.missed.get(&pk).copied();
                        held.missed.insert(pk, body_backfill::connection_lost(was, lost_at));
                    }
                    held.resting.insert(account.clone(), lost_at.saturating_add(UNREACHABLE_REST * 1_000_000));
                    over.insert(account);
                }
                Tried::Ran => {
                    tried.extend(pks);
                    paced = true;
                }
            }
        }
        if !tried.is_empty() {
            let missing: HashSet<i64> = self.wanted.still_without_body(&tried)?.into_iter().collect();
            for pk in tried {
                if missing.contains(&pk) {
                    held.missed.insert(pk, body_backfill::not_given(now));
                } else {
                    held.missed.remove(&pk);
                }
            }
        }
        let going =
            emails.iter().zip(&waiting).any(|(email, rows)| !rows.is_empty() && !held.resting.contains_key(email));
        Ok(body_backfill::next_round(going, !held.resting.is_empty()))
    }
}

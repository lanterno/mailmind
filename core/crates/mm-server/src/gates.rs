//! The IMAP gates (`platform.workers.gates` and `account_worker`): a gate for each account when MAILMIND_WORKERS is
//! `account`, so one account's slow server holds up only its own jobs, and otherwise one gate that every account's
//! jobs share.
//!
//! Jobs waiting for a gate go in priority order, then in the order they came. A job that passes EVERY_ACCOUNT waits
//! until no gate is held, and holds them all; while it waits, a job that would come after it waits too, so it is
//! never starved. A job runs on its caller's own thread, and a thread already holding a gate may not wait for one.

use std::cell::Cell;
use std::collections::HashMap;
use std::sync::{Condvar, Mutex, MutexGuard};
use std::time::Instant;

use mm_mailbox::gates::{EVERY_ACCOUNT, Gates, Priority, Refused, Wait};

thread_local! {
    /// How many gates this thread holds: a job may not wait inside a job.
    static HOLDING: Cell<usize> = const { Cell::new(0) };
}

#[derive(Debug, Default)]
struct State {
    /// The gates held now.
    held: HashMap<String, usize>,
    /// ((priority, arrival), gate) of each job waiting for one.
    waiting: Vec<((Priority, u64), String)>,
    arrival: u64,
}

impl State {
    fn free(&self, gate: &str) -> bool {
        if gate == EVERY_ACCOUNT {
            return self.held.is_empty();
        }
        !self.held.contains_key(gate) && !self.held.contains_key(EVERY_ACCOUNT)
    }

    /// Whether a job waiting before `key` must go first: one for the same gate or for every account, and for an
    /// EVERY_ACCOUNT job, any.
    fn ahead(&self, key: (Priority, u64), gate: &str) -> bool {
        self.waiting.iter().any(|(k, g)| *k < key && (g == gate || g == EVERY_ACCOUNT || gate == EVERY_ACCOUNT))
    }

    fn admissible(&self, key: (Priority, u64), gate: &str) -> bool {
        self.free(gate) && !self.ahead(key, gate)
    }
}

/// The gates of one process.
#[derive(Debug, Default)]
pub struct AccountGates {
    per_account: bool,
    state: Mutex<State>,
    changed: Condvar,
}

impl AccountGates {
    /// A gate for each account, or one for them all.
    pub fn new(per_account: bool) -> AccountGates {
        AccountGates { per_account, ..AccountGates::default() }
    }

    /// The gate a job for `account` passes.
    fn gate_of<'a>(&self, account: &'a str) -> &'a str {
        if self.per_account { account } else { EVERY_ACCOUNT }
    }

    fn state(&self) -> MutexGuard<'_, State> {
        self.state.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
    }

    /// Take `gate`, waiting as `wait` says: false when it didn't come free in time.
    fn take(&self, gate: &str, priority: Priority, wait: Wait) -> bool {
        let mut state = self.state();
        state.arrival += 1;
        let key = (priority, state.arrival);
        if !state.admissible(key, gate) {
            let ends = match wait {
                Wait::Not => return false,
                Wait::For(d) => Some(Instant::now() + d),
                Wait::Ever => None,
            };
            state.waiting.push((key, gate.to_owned()));
            let mut had = true;
            while !state.admissible(key, gate) {
                match ends {
                    None => state = self.changed.wait(state).unwrap_or_else(|poisoned| poisoned.into_inner()),
                    Some(ends) => {
                        let left = ends.saturating_duration_since(Instant::now());
                        if left.is_zero() {
                            had = false;
                            break;
                        }
                        state = self
                            .changed
                            .wait_timeout(state, left)
                            .map(|(state, _)| state)
                            .unwrap_or_else(|poisoned| poisoned.into_inner().0);
                    }
                }
            }
            state.waiting.retain(|(k, _)| *k != key);
            // A job behind this one may go now.
            self.changed.notify_all();
            if !had {
                return false;
            }
        }
        *state.held.entry(gate.to_owned()).or_default() += 1;
        true
    }

    fn give_back(&self, gate: &str) {
        let mut state = self.state();
        if let Some(n) = state.held.get_mut(gate) {
            *n -= 1;
            if *n == 0 {
                state.held.remove(gate);
            }
        }
        self.changed.notify_all();
    }
}

/// Gives a gate back when the job ends, however it ends.
struct Held<'a> {
    gates: &'a AccountGates,
    gate: &'a str,
}

impl Drop for Held<'_> {
    fn drop(&mut self) {
        HOLDING.with(|h| h.set(h.get().saturating_sub(1)));
        self.gates.give_back(self.gate);
    }
}

impl Gates for AccountGates {
    fn run(&self, account: &str, priority: Priority, wait: Wait, job: &mut dyn FnMut()) -> Result<(), Refused> {
        if wait != Wait::Not && HOLDING.with(Cell::get) > 0 {
            return Err(Refused::Nested);
        }
        let gate = self.gate_of(account);
        if !self.take(gate, priority, wait) {
            return Err(Refused::Busy);
        }
        HOLDING.with(|h| h.set(h.get() + 1));
        let _held = Held { gates: self, gate };
        job();
        Ok(())
    }

    fn held(&self, account: &str) -> bool {
        let state = self.state();
        if account == EVERY_ACCOUNT || !self.per_account { !state.held.is_empty() } else { !state.free(account) }
    }
}

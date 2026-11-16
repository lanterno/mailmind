//! Each account's syncs in flight: what is reading its mail into the store now (`mailbox.application.flights`). An
//! account removed while one reads stops it at the next message, and what was let go of its mail then goes again as
//! the last of them lands, so nothing it read after the removal stays.
//!
//! A flight starts before the sync looks its account up, so a removal either finds it in flight, or comes first and
//! the sync finds no account to read.

use std::collections::HashMap;
use std::fmt;
use std::sync::{Mutex, MutexGuard, PoisonError};

/// What runs once an account's last flight has landed.
pub type Then = Box<dyn FnOnce() + Send>;

/// One account's flights.
#[derive(Default)]
struct Flying {
    count: usize,
    stopped: bool,
    then: Vec<Then>,
}

/// The flights of one process, by account (its address in lower case).
#[derive(Default)]
pub struct Flights {
    held: Mutex<HashMap<String, Flying>>,
}

impl fmt::Debug for Flights {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let flying: Vec<(String, usize)> = self.held().iter().map(|(a, f)| (a.clone(), f.count)).collect();
        f.debug_struct("Flights").field("flying", &flying).finish()
    }
}

impl Flights {
    fn held(&self) -> MutexGuard<'_, HashMap<String, Flying>> {
        self.held.lock().unwrap_or_else(PoisonError::into_inner)
    }

    /// A flight of `account`'s, until the guard drops.
    pub fn fly(&self, account: &str) -> Flight<'_> {
        let account = account.to_lowercase();
        self.held().entry(account.clone()).or_default().count += 1;
        Flight { flights: self, account }
    }

    /// The account was removed: each of its flights stops at its next message. None in flight, nothing to stop.
    pub fn stop(&self, account: &str) {
        if let Some(flying) = self.held().get_mut(&account.to_lowercase()) {
            flying.stopped = true;
        }
    }

    /// Whether `account`'s flights were told to stop.
    pub fn stopped(&self, account: &str) -> bool {
        self.held().get(&account.to_lowercase()).is_some_and(|f| f.stopped)
    }

    /// Run `then` once the last of `account`'s flights now in the air has landed: whether one was.
    pub fn after(&self, account: &str, then: Then) -> bool {
        match self.held().get_mut(&account.to_lowercase()) {
            Some(flying) => {
                flying.then.push(then);
                true
            }
            None => false,
        }
    }

    fn landed(&self, account: &str) {
        let then = {
            let mut held = self.held();
            let Some(flying) = held.get_mut(account) else { return };
            flying.count -= 1;
            if flying.count > 0 {
                return;
            }
            held.remove(account).map(|f| f.then).unwrap_or_default()
        };
        for then in then {
            then();
        }
    }
}

/// A flight in the air: it lands as it drops, however the sync ends.
#[derive(Debug)]
pub struct Flight<'a> {
    flights: &'a Flights,
    account: String,
}

impl Flight<'_> {
    /// Whether its account was removed since it started.
    pub fn stopped(&self) -> bool {
        self.flights.stopped(&self.account)
    }
}

impl Drop for Flight<'_> {
    fn drop(&mut self) {
        self.flights.landed(&self.account);
    }
}

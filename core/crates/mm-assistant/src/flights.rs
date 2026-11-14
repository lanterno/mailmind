//! One run at a time for each key, and none for what is already stored (`platform.workers.singleflight`).
//!
//! The AI reads that cost money are each made by the first caller that asks, then stored. A caller that asks while that
//! run is going waits for it and reads what it stored. The caller that takes the lead reads once more before it makes:
//! a run that ended between its own check and its taking the lead may have stored the answer already.

use std::collections::HashMap;
use std::hash::Hash;
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, Instant};

/// Whether a run has ended, and the wait for it to.
#[derive(Debug, Default)]
struct Done {
    ended: Mutex<bool>,
    ended_now: Condvar,
}

#[derive(Debug)]
pub struct SingleFlight<K> {
    runs: Mutex<HashMap<K, Arc<Done>>>,
}

impl<K> Default for SingleFlight<K> {
    fn default() -> Self {
        SingleFlight { runs: Mutex::new(HashMap::new()) }
    }
}

/// Takes the run off the list and wakes whoever waits, however `make` ended.
struct Lead<'a, K: Eq + Hash + Clone> {
    flights: &'a SingleFlight<K>,
    key: K,
    done: Arc<Done>,
}

impl<K: Eq + Hash + Clone> Drop for Lead<'_, K> {
    fn drop(&mut self) {
        self.flights.runs.lock().unwrap_or_else(std::sync::PoisonError::into_inner).remove(&self.key);
        *self.done.ended.lock().unwrap_or_else(std::sync::PoisonError::into_inner) = true;
        self.done.ended_now.notify_all();
    }
}

impl<K: Eq + Hash + Clone> SingleFlight<K> {
    /// What `read()` finds for `key`, else what `make()` gives, with one `make` at a time for each key.
    ///
    /// A caller that finds a run going waits up to `wait` for it to end, then gives `read()`, None when the run stored
    /// nothing. The caller that leads gives what `read()` finds, if anything, else `make()`. `recheck` false skips
    /// that read, for a caller that wants the answer made afresh.
    pub fn run<T>(
        &self,
        key: K,
        make: impl FnOnce() -> T,
        read: impl Fn() -> Option<T>,
        wait: Duration,
        recheck: bool,
    ) -> Option<T> {
        let (done, leads) = {
            let mut runs = self.runs.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
            match runs.get(&key) {
                Some(done) => (done.clone(), false),
                None => {
                    let done = Arc::new(Done::default());
                    runs.insert(key.clone(), done.clone());
                    (done, true)
                }
            }
        };
        if !leads {
            let until = Instant::now() + wait;
            let mut ended = done.ended.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
            while !*ended {
                let left = until.saturating_duration_since(Instant::now());
                if left.is_zero() {
                    break;
                }
                ended = done.ended_now.wait_timeout(ended, left).unwrap_or_else(std::sync::PoisonError::into_inner).0;
            }
            drop(ended);
            return read();
        }
        let _lead = Lead { flights: self, key, done };
        if recheck && let Some(found) = read() {
            return Some(found);
        }
        Some(make())
    }

    /// Whether a run for `key` is going now.
    pub fn busy(&self, key: &K) -> bool {
        self.runs.lock().unwrap_or_else(std::sync::PoisonError::into_inner).contains_key(key)
    }
}

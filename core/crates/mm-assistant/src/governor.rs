//! How much MailMind may spend on AI, and when (`application.governor`). The gateway asks the governor before every
//! call.
//!
//! - Slots. Two interactive calls at a time and, apart from those, two background or speculative ones, so a backlog
//!   never makes a click wait.
//! - Yielding. Background and speculative work doesn't start while an interactive call runs, nor for `YIELD_SECONDS`
//!   after one ends.
//! - Presence. Speculative work runs only while someone has used the app in the last `PRESENCE_SECONDS` (the server
//!   says so with `saw_someone`); scheduled work asks for someone in the last `SCHEDULED_PRESENCE_SECONDS`.
//! - The daily limit, counted across every call by the usage ledger. At the limit, background and speculative work
//!   stops for the day; an interactive call is `OverLimit` instead of being sent, unless the person has given leave to
//!   go over (`allowing_over`).
//! - The breaker. A rejected key (401 or 403) stops every background call at once, and `TRIP_AFTER` permanent
//!   rejections in a row for one feature and model stop that feature's, until the model in use changes. Interactive
//!   calls still go, so the person sees the error.
//!
//! Nothing here is durable: a restart closes the breaker. The day's spending is the ledger's, so the limit holds.

use std::cell::Cell;
use std::collections::HashMap;
use std::fmt;
use std::sync::{Arc, Condvar, Mutex, MutexGuard};

use mm_kernel::errors::Fault;
use mm_kernel::log;
use mm_kernel::seams::Clock;
use serde_json::{Value, json};

use crate::features::Kind;
use crate::ports::{LanguageModel, ModelError};
use crate::refusals::NotSent;

pub const YIELD_SECONDS: f64 = 45.0;
pub const PRESENCE_SECONDS: f64 = 45.0 * 60.0;
/// A day off leaves it on, and a holiday stops it.
pub const SCHEDULED_PRESENCE_SECONDS: f64 = 48.0 * 3600.0;
/// Permanent rejections in a row, for one feature and model.
pub const TRIP_AFTER: u32 = 3;

thread_local! {
    static OVER: Cell<bool> = const { Cell::new(false) };
}

/// `work`, within which an interactive call on this thread may go over the daily limit when `allow` is true: the
/// person said so.
pub fn allowing_over<T>(allow: bool, work: impl FnOnce() -> T) -> T {
    let before = OVER.with(|over| over.replace(allow));
    let out = work();
    OVER.with(|over| over.set(before));
    out
}

/// What the governor reads of the day: what today's calls cost, the daily limit and what batches still out may cost,
/// in micro-dollars.
pub trait Spending: Send + Sync + fmt::Debug {
    fn spent_today(&self) -> Result<i64, Fault>;
    fn limit_micros(&self) -> i64;
    fn reserved(&self) -> Result<i64, Fault>;
}

/// A counting semaphore: `n` holders at a time.
#[derive(Debug)]
struct Slots {
    free: Mutex<usize>,
    freed: Condvar,
}

impl Slots {
    fn new(n: usize) -> Slots {
        Slots { free: Mutex::new(n), freed: Condvar::new() }
    }

    fn take(&self) -> Slot<'_> {
        let mut free = self.free.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
        while *free == 0 {
            free = self.freed.wait(free).unwrap_or_else(std::sync::PoisonError::into_inner);
        }
        *free -= 1;
        Slot(self)
    }
}

struct Slot<'a>(&'a Slots);

impl Drop for Slot<'_> {
    fn drop(&mut self) {
        let mut free = self.0.free.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
        *free += 1;
        self.0.freed.notify_one();
    }
}

/// Where the breaker is open: for every background call, or for one feature on one model.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
enum Scope {
    Every,
    Feature(String, String),
}

impl Scope {
    /// What the snapshot calls it (`_scope_name`).
    fn name(&self) -> String {
        match self {
            Scope::Every => "every feature".to_owned(),
            Scope::Feature(feature, model) => format!("{feature} on {model}"),
        }
    }
}

/// Seconds to a tenth, as Python's `round(seconds, 1)` gives them: the nearest tenth to the float's exact value, a
/// tie to the even one.
fn tenths(seconds: f64) -> f64 {
    format!("{seconds:.1}").parse().unwrap_or(seconds)
}

#[derive(Debug)]
struct State {
    /// Interactive calls in flight.
    running: u32,
    /// When the last interactive call ended.
    last_interactive: Option<f64>,
    last_seen: f64,
    /// (feature, model) -> rejections in a row.
    rejected: HashMap<(String, String), u32>,
    /// Scope -> the model it opened under.
    open: HashMap<Scope, Arc<dyn LanguageModel>>,
}

#[derive(Debug)]
pub struct CostGovernor {
    spending: Arc<dyn Spending>,
    clock: Arc<dyn Clock>,
    interactive: Slots,
    background: Slots,
    state: Mutex<State>,
}

fn same(a: &Arc<dyn LanguageModel>, b: &Arc<dyn LanguageModel>) -> bool {
    std::ptr::addr_eq(Arc::as_ptr(a), Arc::as_ptr(b))
}

impl CostGovernor {
    pub fn new(spending: Arc<dyn Spending>, clock: Arc<dyn Clock>) -> CostGovernor {
        let now = clock.now().seconds();
        CostGovernor {
            spending,
            clock,
            interactive: Slots::new(2),
            background: Slots::new(2),
            // Starting up counts as someone being here.
            state: Mutex::new(State {
                running: 0,
                last_interactive: None,
                last_seen: now,
                rejected: HashMap::new(),
                open: HashMap::new(),
            }),
        }
    }

    fn state(&self) -> MutexGuard<'_, State> {
        self.state.lock().unwrap_or_else(std::sync::PoisonError::into_inner)
    }

    fn now(&self) -> f64 {
        self.clock.now().seconds()
    }

    /// The daily limit, in micro-dollars.
    pub fn limit_micros(&self) -> i64 {
        self.spending.limit_micros()
    }

    /// Someone is using the app now.
    pub fn saw_someone(&self) {
        let now = self.now();
        self.state().last_seen = now;
    }

    /// Why a call of `kind` for `feature` may not start now, or None when it may. An interactive call is never held
    /// back here: only `OverLimit` stops one.
    pub fn held_back(
        &self,
        kind: Kind,
        feature: &str,
        model: &str,
        instance: &Arc<dyn LanguageModel>,
    ) -> Option<&'static str> {
        if kind == Kind::Interactive {
            return None;
        }
        let now = self.now();
        {
            let mut state = self.state();
            if state.running > 0 || state.last_interactive.is_some_and(|last| now - last < YIELD_SECONDS) {
                return Some("yielding");
            }
            if kind == Kind::Speculative && now - state.last_seen > PRESENCE_SECONDS {
                return Some("away");
            }
            if kind == Kind::Scheduled && now - state.last_seen > SCHEDULED_PRESENCE_SECONDS {
                return Some("away");
            }
            if is_open(&mut state, feature, model, instance) {
                return Some("breaker");
            }
        }
        // A ledger that can't be read stops work, not starts it.
        (self.room() <= 0).then_some("limit")
    }

    /// What today's limit leaves for background work, in micro-dollars.
    pub fn room(&self) -> i64 {
        let (spent, limit) = self.today();
        match (spent, self.booked()) {
            (Some(spent), Some(booked)) => (limit - spent - booked).max(0),
            _ => 0,
        }
    }

    /// `work`, holding a slot for one call, or why it can't be made now: `OverLimit` for an interactive call at the
    /// limit without leave to go over, `Deferred` for background work.
    pub fn call<T>(
        &self,
        kind: Kind,
        feature: &str,
        model: &str,
        instance: &Arc<dyn LanguageModel>,
        work: impl FnOnce() -> T,
    ) -> Result<T, NotSent> {
        if kind == Kind::Interactive {
            let (spent, limit) = self.today();
            if let Some(spent) = spent
                && spent >= limit
                && !OVER.with(Cell::get)
            {
                return Err(NotSent::OverLimit { spent, limit });
            }
            let _slot = self.interactive.take();
            self.state().running += 1;
            let out = work();
            let now = self.now();
            let mut state = self.state();
            state.running -= 1;
            state.last_interactive = Some(now);
            return Ok(out);
        }
        if let Some(reason) = self.held_back(kind, feature, model, instance) {
            return Err(NotSent::Deferred(reason));
        }
        let _slot = self.background.take();
        // It may have waited for the slot.
        if let Some(reason) = self.held_back(kind, feature, model, instance) {
            return Err(NotSent::Deferred(reason));
        }
        Ok(work())
    }

    /// How a call went: None for a reply. A reply resets the feature's count of rejections in a row; a transient
    /// failure leaves it as it was.
    pub fn heard(&self, feature: &str, model: &str, instance: &Arc<dyn LanguageModel>, error: Option<&ModelError>) {
        let key = (feature.to_owned(), model.to_owned());
        let mut state = self.state();
        let Some(error) = error else {
            state.rejected.remove(&key);
            return;
        };
        if !error.permanent() {
            return;
        }
        let scope = if matches!(error.status, Some(401 | 403)) {
            Scope::Every
        } else {
            let n = state.rejected.get(&key).copied().unwrap_or(0) + 1;
            if n < TRIP_AFTER {
                state.rejected.insert(key, n);
                return;
            }
            state.rejected.remove(&key);
            Scope::Feature(feature.to_owned(), model.to_owned())
        };
        let opened = !state.open.get(&scope).is_some_and(|under| same(under, instance));
        state.open.insert(scope, instance.clone());
        drop(state);
        if opened {
            log::warning(
                "ai",
                "the API keeps rejecting requests: a scope stops until the key or the model changes",
                &[],
                None,
            );
        }
    }

    /// Back to how a fresh start finds things: no interactive call lately, someone here now, the breaker closed.
    pub fn forget(&self) {
        let now = self.now();
        let mut state = self.state();
        state.last_interactive = None;
        state.last_seen = now;
        state.rejected.clear();
        state.open.clear();
    }

    /// What `/api/debug/ai` shows (`snapshot`): today's spending and the limit, what batches still out may cost, the
    /// interactive calls in flight, the seconds since the last of them ended and since someone was here, and where the
    /// breaker is open, by name and sorted. A spending the ledger can't say is null.
    pub fn snapshot(&self) -> Value {
        let (spent, limit) = self.today();
        let booked = self.booked();
        let now = self.now();
        let state = self.state();
        let mut breaker: Vec<String> = state.open.keys().map(Scope::name).collect();
        breaker.sort();
        json!({
            "spent_today_micros": spent,
            "limit_micros": limit,
            "reserved_micros": booked,
            "interactive_running": state.running,
            "since_interactive": state.last_interactive.map(|last| tenths(now - last)),
            "since_seen": tenths(now - state.last_seen),
            "breaker": breaker,
        })
    }

    fn booked(&self) -> Option<i64> {
        match self.spending.reserved() {
            Ok(booked) => Some(booked),
            Err(fault) => {
                log::warning("ai", "what batches may cost can't be read", &[], Some(fault.class));
                None
            }
        }
    }

    /// (today's spending, or None when the ledger can't be read; the limit).
    fn today(&self) -> (Option<i64>, i64) {
        let spent = match self.spending.spent_today() {
            Ok(spent) => Some(spent),
            Err(fault) => {
                log::warning("ai", "the usage ledger can't be read", &[], Some(fault.class));
                None
            }
        };
        (spent, self.spending.limit_micros())
    }
}

fn is_open(state: &mut State, feature: &str, model: &str, instance: &Arc<dyn LanguageModel>) -> bool {
    for scope in [Scope::Every, Scope::Feature(feature.to_owned(), model.to_owned())] {
        let Some(under) = state.open.get(&scope) else { continue };
        if same(under, instance) {
            return true;
        }
        // The model changed since it opened: try again.
        state.open.remove(&scope);
    }
    false
}

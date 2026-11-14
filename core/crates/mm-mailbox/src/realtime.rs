//! IMAP IDLE (`mailbox.application.realtime`): one worker for each account watches its INBOX, and a dispatcher
//! coalesces a burst of changes into one InboxChanged, which the program answers with a sync of those accounts. The
//! open tabs hear of what the sync found through the event stream, as they do of any sync.
//!
//! The workers follow the configuration (`idle_supervisor` holds how): an account added gets a worker, one removed or
//! disabled loses its own, and one now reached another way (`Reached`) gets a fresh worker in place of the old. The
//! program calls `reconcile` when an account is added, changed or removed, and a worker that finds its account changed
//! under it after an IDLE cycle leaves on its own, so an edit of the file, which says nothing, still reaches it.
//!
//! Bounded: one IDLE connection an account, and `Pace::most` accounts at most; the rest wait for the periodic sync.

use std::collections::BTreeSet;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};
use std::thread::JoinHandle;
use std::time::Duration;

use mm_kernel::log::{self, Field};

use crate::idle_supervisor::{Interrupt, Rests, Supervisor, Watch, Watched};
use crate::realtime_ports::{IdleAccounts, IdleSession, IdleSessions, InboxNews, Reached};
use crate::workers::Kick;

const TAG: &str = "idle";
/// Seconds an IDLE lasts before it is left and asked again: before servers drop one (about 29 minutes).
pub const IDLE_SECONDS: u64 = 24 * 60;
pub const MAX_IDLE_ACCOUNTS: usize = 12;
/// Seconds the dispatcher waits for a change before it looks again.
pub const DEBOUNCE_SECONDS: u64 = 3;

/// How IDLE's workers keep time, and how many there may be.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Pace {
    /// How long one IDLE lasts.
    pub idle: Duration,
    /// How long the dispatcher waits before it looks again.
    pub debounce: Duration,
    /// How many accounts are watched at once.
    pub most: usize,
    /// How long a worker rests after its connection fails.
    pub rests: Rests,
}

impl Default for Pace {
    fn default() -> Pace {
        Pace {
            idle: Duration::from_secs(IDLE_SECONDS),
            debounce: Duration::from_secs(DEBOUNCE_SECONDS),
            most: MAX_IDLE_ACCOUNTS,
            rests: Rests::default(),
        }
    }
}

fn held<T>(lock: &Mutex<T>) -> MutexGuard<'_, T> {
    lock.lock().unwrap_or_else(PoisonError::into_inner)
}

/// What the workers watch, and what they do: the supervisor's `Watched`.
#[derive(Debug)]
struct Idle {
    accounts: Arc<dyn IdleAccounts>,
    sessions: Arc<dyn IdleSessions>,
    news: Arc<dyn InboxNews>,
    pace: Mutex<Pace>,
    /// The accounts whose INBOX changed since the dispatcher last looked.
    dirty: Mutex<BTreeSet<String>>,
    kick: Arc<Kick>,
    stopped: AtomicBool,
}

impl Idle {
    fn pace(&self) -> Pace {
        *held(&self.pace)
    }

    fn trigger_sync(&self, email: &str) {
        held(&self.dirty).insert(email.to_owned());
        self.kick.kick();
    }

    /// Until stopped: wait for a change, or for the debounce, then tell of every account changed since the last look,
    /// at once. The sync it sets going runs here, so changes heard while it runs are told together after it.
    fn dispatch(&self) {
        while !self.stopped.load(Ordering::SeqCst) {
            self.kick.wait(self.pace().debounce);
            if self.stopped.load(Ordering::SeqCst) {
                break;
            }
            let todo = std::mem::take(&mut *held(&self.dirty));
            if !todo.is_empty() {
                self.news.inbox_changed(todo);
            }
        }
    }
}

impl Watched for Idle {
    type Spec = Reached;
    type Conn = Box<dyn IdleSession>;

    fn desired(&self) -> Option<Vec<(String, Reached)>> {
        match self.accounts.accounts() {
            Ok(accounts) => Some(accounts.into_iter().map(|a| (a.email.clone(), a)).collect()),
            Err(fault) => {
                log::warning(TAG, "can't read the accounts; watching as before", &[], Some(fault.class));
                None
            }
        }
    }

    fn same(&self, running: &Reached, wanted: &Reached) -> bool {
        running.way == wanted.way
    }

    /// Is this account still configured, enabled and reached the same way? A configuration that can't be read keeps
    /// the worker watching.
    fn wanted(&self, account: &Reached) -> bool {
        match self.accounts.get(&account.email) {
            Ok(Some(now)) => now.way == account.way,
            Ok(None) => false,
            Err(_) => true,
        }
    }

    fn limit(&self) -> usize {
        self.pace().most
    }

    /// Never the address: a thread's name shows wherever it panics.
    fn name(&self, _account: &Reached) -> String {
        "idle".to_owned()
    }

    fn connect(&self, account: &Reached) -> Result<(Box<dyn IdleSession>, Arc<dyn Interrupt>), &'static str> {
        let mut session = self.sessions.open(&account.email).map_err(|e| e.class())?;
        match session.interrupter() {
            Ok(interrupt) => Ok((session, interrupt)),
            Err(error) => {
                session.logout();
                Err(error.class())
            }
        }
    }

    fn ready(&self, _account: &Reached, session: &mut Box<dyn IdleSession>) -> Result<bool, &'static str> {
        if !session.capabilities().iter().any(|c| c == "IDLE") {
            log::warning(TAG, "an account's server has no IDLE; the periodic sync has it", &[], None);
            // The worker logs out as it ends, as it does of any connection.
            return Ok(false);
        }
        session.select("INBOX", false).map_err(|e| e.class())?;
        Ok(true)
    }

    fn serve(&self, watch: &Watch<Reached>, session: &mut Box<dyn IdleSession>) -> Result<(), &'static str> {
        let account = &watch.spec;
        while !watch.leaving() {
            // It leaves IDLE (DONE) before it asks for a sync, which runs on a connection of its own.
            let changed = session.idle(self.pace().idle).map_err(|e| e.class())?;
            if watch.leaving() {
                break;
            }
            if changed {
                self.trigger_sync(&account.email);
            }
            if !self.wanted(account) {
                // The worker asks again, says so and stops.
                break;
            }
        }
        Ok(())
    }

    fn close(&self, mut session: Box<dyn IdleSession>) {
        session.logout();
    }

    fn say_unwanted(&self, _account: &Reached) {
        log::info(TAG, "an account was removed, disabled or changed; its worker stops", &[]);
    }

    fn say_failed(&self, _account: &Reached, class: &'static str, rest: Duration) {
        let rest = rest.as_secs().to_string();
        log::warning(
            TAG,
            "an account's IDLE failed; it tries again in %(count)s seconds",
            &[(Field::Count, &rest)],
            Some(class),
        );
    }
}

/// What wires Realtime.
#[derive(Debug)]
pub struct RealtimeParts {
    pub accounts: Arc<dyn IdleAccounts>,
    pub sessions: Arc<dyn IdleSessions>,
    pub news: Arc<dyn InboxNews>,
}

/// IDLE's workers and their dispatcher.
#[derive(Debug)]
pub struct Realtime {
    idle: Arc<Idle>,
    supervisor: Supervisor<Idle>,
    dispatcher: Mutex<Option<JoinHandle<()>>>,
}

impl Realtime {
    /// Nothing runs until `start`, and `reconcile` does nothing before it.
    pub fn new(parts: RealtimeParts) -> Realtime {
        let RealtimeParts { accounts, sessions, news } = parts;
        let idle = Arc::new(Idle {
            accounts,
            sessions,
            news,
            pace: Mutex::new(Pace::default()),
            dirty: Mutex::new(BTreeSet::new()),
            kick: Arc::new(Kick::default()),
            stopped: AtomicBool::new(false),
        });
        Realtime { supervisor: Supervisor::new(idle.clone()), idle, dispatcher: Mutex::new(None) }
    }

    /// Start the dispatcher, then one IDLE worker for each enabled account, up to `pace.most`. A second start does
    /// nothing.
    pub fn start(&self, pace: Pace) {
        let mut dispatcher = held(&self.dispatcher);
        if dispatcher.is_some() || self.idle.stopped.load(Ordering::SeqCst) {
            return;
        }
        *held(&self.idle.pace) = pace;
        self.supervisor.begin(pace.rests);
        let idle = self.idle.clone();
        *dispatcher = std::thread::Builder::new().name("idle-dispatch".to_owned()).spawn(move || idle.dispatch()).ok();
        drop(dispatcher);
        let configured = self.reconcile();
        if configured > pace.most {
            let (configured, most) = (configured.to_string(), pace.most.to_string());
            log::warning(
                TAG,
                "%(count)s accounts, past the cap of %(what)s; the rest rely on the periodic sync",
                &[(Field::Count, &configured), (Field::What, &most)],
                None,
            );
        }
        let watching = self.watching().len().to_string();
        log::info(TAG, "watching %(count)s account(s) in real time", &[(Field::Count, &watching)]);
    }

    /// Bring the workers in line with the accounts as they are now (`realtime.reconcile`): what an account added,
    /// changed or removed calls. How many accounts are wanted; 0 before `start` and after `stop`.
    pub fn reconcile(&self) -> usize {
        self.supervisor.reconcile()
    }

    /// The accounts with a worker, each with whether it still runs: one whose server has no IDLE has ended.
    pub fn watching(&self) -> Vec<(String, bool)> {
        self.supervisor.watching()
    }

    /// How many worker threads still run, retired ones on their way out included.
    pub fn running(&self) -> usize {
        self.supervisor.running()
    }

    /// Stop the dispatcher and every worker: each connection is interrupted, and its worker logs out of it.
    pub fn stop(&self) {
        // Before the kick: the dispatcher asks as it wakes.
        self.idle.stopped.store(true, Ordering::SeqCst);
        self.idle.kick.kick();
        self.supervisor.stop();
    }

    /// Wait for the dispatcher and every worker to end, after `stop`. A sync the dispatcher set going ends first.
    pub fn join(&self) {
        self.supervisor.join();
        let dispatcher = held(&self.dispatcher).take();
        if let Some(dispatcher) = dispatcher {
            let _ = dispatcher.join();
        }
    }
}

//! One worker thread for each key, kept in line with what is wanted (`platform.workers.idle_supervisor`).
//!
//! A worker holds a connection open and waits on it. `reconcile` starts one for each key wanted, up to a limit,
//! retires one whose key has gone or is now wanted differently, and leaves the rest be. A worker whose connection
//! fails rests, longer each time, then opens another. One that finds itself no longer wanted leaves on its own, and
//! asks for a reconcile on its way out. What a connection is, and what a worker does with one, is the `Watched`'s to
//! say; this knows nothing of what they watch.
//!
//! Only a worker closes its connection. Anyone else may only interrupt it, which wakes the worker and closes nothing:
//! closing a socket another thread may be about to wait on frees its descriptor's number for the next socket opened,
//! which the worker would then wait on and read from.

use std::fmt;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Condvar, Mutex, MutexGuard, PoisonError};
use std::thread::JoinHandle;
use std::time::Duration;

/// What ends a wait on a connection from another thread.
pub trait Interrupt: Send + Sync + fmt::Debug {
    /// End a wait on this connection now, without closing it.
    fn interrupt(&self);
}

/// What a worker is started for, and what it does: the supervisor's subclass in the Python app. `Spec` is what a
/// worker is started for, `Conn` its connection.
pub trait Watched: Send + Sync + 'static {
    type Spec: Clone + Send + Sync + 'static;
    type Conn: Send + 'static;

    /// Each key wanted now, in order, with what its worker is for. None when that can't be told, and the workers
    /// stay as they are.
    fn desired(&self) -> Option<Vec<(String, Self::Spec)>>;

    /// Whether a worker started for `running` serves `wanted` as it is.
    fn same(&self, running: &Self::Spec, wanted: &Self::Spec) -> bool;

    /// A worker asks: is what it was started for still wanted, as it was?
    fn wanted(&self, spec: &Self::Spec) -> bool;

    /// How many workers may run at once.
    fn limit(&self) -> usize;

    /// Its thread's name.
    fn name(&self, spec: &Self::Spec) -> String;

    /// A new connection, and what interrupts it. A failure, by its class, makes the worker rest and try again.
    fn connect(&self, spec: &Self::Spec) -> Result<(Self::Conn, Arc<dyn Interrupt>), &'static str>;

    /// Make a new connection ready to serve. False: this one never will be, so the worker ends. A failure makes it
    /// rest and try again.
    fn ready(&self, spec: &Self::Spec, conn: &mut Self::Conn) -> Result<bool, &'static str>;

    /// Wait on the connection and act on what it brings, until `watch.leaving()` or something has changed. The worker
    /// then closes it and asks whether it is wanted. A failure makes it rest first.
    fn serve(&self, watch: &Watch<Self::Spec>, conn: &mut Self::Conn) -> Result<(), &'static str>;

    /// Close the connection, as its worker leaves it, however it stands.
    fn close(&self, conn: Self::Conn);

    /// A worker found it isn't wanted, and ends.
    fn say_unwanted(&self, _spec: &Self::Spec) {}

    /// A worker's connection failed while nothing was stopping it, and it rests `rest` before the next.
    fn say_failed(&self, _spec: &Self::Spec, _class: &'static str, _rest: Duration) {}
}

/// How long a worker rests after a failure: `first`, twice that after each failure in a row, up to `longest`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Rests {
    pub first: Duration,
    pub longest: Duration,
}

impl Default for Rests {
    fn default() -> Rests {
        Rests { first: Duration::from_secs(5), longest: Duration::from_secs(300) }
    }
}

/// One key's worker: what it was started for, and what stops it.
#[derive(Debug)]
pub struct Watch<S> {
    pub spec: S,
    /// Retired: its key is gone or now wanted differently.
    quit: AtomicBool,
    /// The supervisor stopped.
    stopping: Arc<AtomicBool>,
    /// What interrupts its connection, while it has one.
    conn: Mutex<Option<Arc<dyn Interrupt>>>,
    /// What it rests if its connection fails now.
    rest: Mutex<Duration>,
    /// Its thread has ended.
    ended: AtomicBool,
}

impl<S> Watch<S> {
    pub fn leaving(&self) -> bool {
        self.quit.load(Ordering::SeqCst) || self.stopping.load(Ordering::SeqCst)
    }

    fn rest(&self) -> Duration {
        *held(&self.rest)
    }

    fn set_rest(&self, rest: Duration) {
        *held(&self.rest) = rest;
    }
}

/// Every lock here guards a value whole between statements, so one a panicking thread let go is still sound.
fn held<T>(lock: &Mutex<T>) -> MutexGuard<'_, T> {
    lock.lock().unwrap_or_else(PoisonError::into_inner)
}

/// Marks a worker ended as its thread leaves, however it leaves.
struct Ending<S>(Arc<Watch<S>>);

impl<S> Drop for Ending<S> {
    fn drop(&mut self) {
        self.0.ended.store(true, Ordering::SeqCst);
    }
}

#[derive(Debug)]
struct Table<S> {
    /// `begin` has run, and `stop` hasn't.
    running: bool,
    /// The workers `begin` and `reconcile` run, by key, in the order they started.
    watchers: Vec<(String, Arc<Watch<S>>)>,
}

struct Inner<W: Watched> {
    watched: Arc<W>,
    stop: Arc<AtomicBool>,
    rests: Mutex<Rests>,
    /// The workers' connections' interrupters, to interrupt on `stop`.
    conns: Mutex<Vec<Arc<dyn Interrupt>>>,
    /// A worker resting waits here; a stop and a retirement wake it.
    wake: Mutex<()>,
    woken: Condvar,
    table: Mutex<Table<W::Spec>>,
    /// Every worker thread started and not yet waited for.
    threads: Mutex<Vec<JoinHandle<()>>>,
}

/// How a connection's turn ended.
enum Turn {
    /// It never will do: the worker ends.
    NeverReady,
    /// It served until something changed.
    Served,
    /// It failed, by this class: the worker rests.
    Failed(&'static str),
}

impl<W: Watched> Inner<W> {
    fn rest(&self, watch: &Watch<W::Spec>, rest: Duration) {
        let waiting = held(&self.wake);
        let _ =
            self.woken.wait_timeout_while(waiting, rest, |_| !watch.leaving()).unwrap_or_else(PoisonError::into_inner);
    }

    fn worker(self: &Arc<Self>, watch: &Arc<Watch<W::Spec>>) {
        let _ending = Ending(watch.clone());
        let watched = &*self.watched;
        while !watch.leaving() {
            if !watched.wanted(&watch.spec) {
                watched.say_unwanted(&watch.spec);
                // Changed with no one to say so: a worker for what's wanted now, if running.
                self.reconcile();
                return;
            }
            let failed = match watched.connect(&watch.spec) {
                Err(class) => class,
                Ok((mut conn, interrupt)) => {
                    let turn = self.turn(watch, &mut conn, interrupt);
                    watched.close(conn);
                    match turn {
                        Turn::NeverReady => return,
                        Turn::Served => continue,
                        Turn::Failed(class) => class,
                    }
                }
            };
            let rest = watch.rest();
            if !watch.leaving() {
                watched.say_failed(&watch.spec, failed, rest);
            }
            self.rest(watch, rest);
            watch.set_rest((rest * 2).min(held(&self.rests).longest));
        }
    }

    /// Ready a connection and serve on it, listed to be interrupted while it serves.
    fn turn(&self, watch: &Watch<W::Spec>, conn: &mut W::Conn, interrupt: Arc<dyn Interrupt>) -> Turn {
        match self.watched.ready(&watch.spec, conn) {
            Err(class) => return Turn::Failed(class),
            Ok(false) => return Turn::NeverReady,
            Ok(true) => {}
        }
        held(&self.conns).push(interrupt.clone());
        // Set before `leaving()` is asked: a retirement sets `quit` first, then interrupts what it finds here.
        *held(&watch.conn) = Some(interrupt.clone());
        watch.set_rest(held(&self.rests).first);
        let served = self.watched.serve(watch, conn);
        *held(&watch.conn) = None;
        held(&self.conns).retain(|listed| !Arc::ptr_eq(listed, &interrupt));
        match served {
            Ok(()) => Turn::Served,
            Err(class) => Turn::Failed(class),
        }
    }

    fn spawn(self: &Arc<Self>, spec: W::Spec) -> Arc<Watch<W::Spec>> {
        let watch = Arc::new(Watch {
            spec,
            quit: AtomicBool::new(false),
            stopping: self.stop.clone(),
            conn: Mutex::new(None),
            rest: Mutex::new(held(&self.rests).first),
            ended: AtomicBool::new(false),
        });
        let (inner, working) = (self.clone(), watch.clone());
        let name = self.watched.name(&watch.spec);
        match std::thread::Builder::new().name(name).spawn(move || inner.worker(&working)) {
            Ok(thread) => {
                let mut threads = held(&self.threads);
                threads.retain(|t| !t.is_finished());
                threads.push(thread);
            }
            // No thread to run it: it shows as ended, and the next reconcile that retires it makes room.
            Err(_) => watch.ended.store(true, Ordering::SeqCst),
        }
        watch
    }

    /// Stop a worker: wake it from a rest, and interrupt its connection (see `stop`).
    fn retire(&self, watch: &Watch<W::Spec>) {
        {
            let _waking = held(&self.wake);
            watch.quit.store(true, Ordering::SeqCst);
            self.woken.notify_all();
        }
        let conn = held(&watch.conn).clone();
        if let Some(conn) = conn {
            conn.interrupt();
        }
    }

    fn reconcile(self: &Arc<Self>) -> usize {
        let mut table = held(&self.table);
        if !table.running || self.stop.load(Ordering::SeqCst) {
            return 0;
        }
        let Some(wanted) = self.watched.desired() else { return 0 };
        let find = |key: &str| wanted.iter().find(|(k, _)| k == key).map(|(_, spec)| spec);
        table.watchers.retain(|(key, watch)| {
            let kept = find(key).is_some_and(|spec| self.watched.same(&watch.spec, spec));
            if !kept {
                self.retire(watch);
            }
            kept
        });
        for (key, spec) in &wanted {
            if table.watchers.iter().all(|(k, _)| k != key) && table.watchers.len() < self.watched.limit() {
                let watch = self.spawn(spec.clone());
                table.watchers.push((key.clone(), watch));
            }
        }
        wanted.len()
    }
}

/// The workers, kept in line with what `W` says is wanted.
pub struct Supervisor<W: Watched> {
    inner: Arc<Inner<W>>,
}

impl<W: Watched> fmt::Debug for Supervisor<W> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Supervisor").field("watching", &self.watching().len()).finish_non_exhaustive()
    }
}

impl<W: Watched> Supervisor<W> {
    pub fn new(watched: Arc<W>) -> Supervisor<W> {
        Supervisor {
            inner: Arc::new(Inner {
                watched,
                stop: Arc::new(AtomicBool::new(false)),
                rests: Mutex::new(Rests::default()),
                conns: Mutex::new(Vec::new()),
                wake: Mutex::new(()),
                woken: Condvar::new(),
                table: Mutex::new(Table { running: false, watchers: Vec::new() }),
                threads: Mutex::new(Vec::new()),
            }),
        }
    }

    /// From now until `stop`, `reconcile` acts, and a worker that fails rests as `rests` says.
    pub fn begin(&self, rests: Rests) {
        *held(&self.inner.rests) = rests;
        held(&self.inner.table).running = true;
    }

    /// Bring the workers in line with what is wanted: retire each whose key has gone or is now wanted differently,
    /// and start one for each key without one, up to the limit. Before `begin` and after `stop` it does nothing.
    /// Returns how many keys are wanted.
    pub fn reconcile(&self) -> usize {
        self.inner.reconcile()
    }

    /// The keys with a worker, each with whether it still runs (one may have ended on a connection that will never
    /// do).
    pub fn watching(&self) -> Vec<(String, bool)> {
        held(&self.inner.table)
            .watchers
            .iter()
            .map(|(key, watch)| (key.clone(), !watch.ended.load(Ordering::SeqCst)))
            .collect()
    }

    /// What `key`'s worker rests if its connection fails now: the first rest, twice as long after each failure in a
    /// row up to the longest, and the first again once a connection serves. None when it has no worker.
    pub fn next_rest(&self, key: &str) -> Option<Duration> {
        held(&self.inner.table).watchers.iter().find(|(k, _)| k == key).map(|(_, watch)| watch.rest())
    }

    /// How many of the worker threads started still run, retired ones on their way out included.
    pub fn running(&self) -> usize {
        held(&self.inner.threads).iter().filter(|t| !t.is_finished()).count()
    }

    /// Whether `stop` has been asked.
    pub fn stopping(&self) -> bool {
        self.inner.stop.load(Ordering::SeqCst)
    }

    /// Stop every worker: wake each that rests, and interrupt each connection, which its worker then closes.
    pub fn stop(&self) {
        self.inner.stop.store(true, Ordering::SeqCst);
        {
            let mut table = held(&self.inner.table);
            table.running = false;
            table.watchers.clear();
        }
        {
            let _waking = held(&self.inner.wake);
            self.inner.woken.notify_all();
        }
        // Wake a worker blocked reading its connection. Never close it from here: the worker may be about to wait on
        // that descriptor, and once closed its number can go to the next socket opened, which the worker would then
        // wait on (and read from). The worker closes its own connection on the way out.
        let conns = held(&self.inner.conns).clone();
        for conn in conns {
            conn.interrupt();
        }
    }

    /// Wait for every worker started to end: after `stop`, as each leaves its connection.
    pub fn join(&self) {
        let threads = std::mem::take(&mut *held(&self.inner.threads));
        for thread in threads {
            let _ = thread.join();
        }
    }
}

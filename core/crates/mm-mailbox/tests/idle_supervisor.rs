//! `idle_supervisor`: one worker thread per key, kept in line with what is wanted (the Python app's
//! tests/unit/platform/test_idle_supervisor.py). Plain threads and a connection that is only a flag; `realtime.rs` holds
//! the same through IDLE itself. What a worker does shows on its connection, which the `Watched` makes.

#![allow(clippy::unwrap_used)]

use std::collections::BTreeSet;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::thread::ThreadId;
use std::time::{Duration, Instant};

use mm_mailbox::idle_supervisor::{Interrupt, Rests, Supervisor, Watch, Watched};

/// A rest of moments, where the app's first is 5 seconds.
const TICK: Duration = Duration::from_millis(1);

fn me() -> String {
    std::thread::current().name().unwrap_or_default().to_owned()
}

fn until(what: impl Fn() -> bool) {
    let deadline = Instant::now() + Duration::from_secs(10);
    while !what() {
        assert!(Instant::now() < deadline, "it never came");
        std::thread::sleep(Duration::from_millis(2));
    }
}

#[derive(Debug, Default)]
struct Signal {
    news: bool,
    shut: bool,
}

/// A connection, which knows the worker that opened it. A worker waits on it until news comes, or until it is
/// interrupted, which makes the wait fail, as a socket shut down from another thread does.
#[derive(Debug)]
struct Line {
    spec: (String, String),
    thread: ThreadId,
    interrupted_by: Mutex<Vec<String>>,
    closed_by: Mutex<Vec<String>>,
    signal: Mutex<Signal>,
    signalled: Condvar,
    /// It takes no notice of the first interrupt.
    deaf: AtomicBool,
    /// Its worker waits on it now.
    waiting: AtomicBool,
}

impl Line {
    fn key(&self) -> &str {
        &self.spec.0
    }

    fn news(&self) {
        self.signal.lock().unwrap().news = true;
        self.signalled.notify_all();
    }

    /// The far end goes, as a server hanging up does.
    fn drop_it(&self) {
        let mut signal = self.signal.lock().unwrap();
        (signal.shut, signal.news) = (true, true);
        self.signalled.notify_all();
    }

    fn shut(&self) -> bool {
        self.signal.lock().unwrap().shut
    }

    fn wait(&self) -> Result<(), &'static str> {
        self.waiting.store(true, Ordering::SeqCst);
        let signal = self.signal.lock().unwrap();
        let (mut signal, _) = self.signalled.wait_timeout_while(signal, Duration::from_secs(300), |s| !s.news).unwrap();
        self.waiting.store(false, Ordering::SeqCst);
        signal.news = false;
        if signal.shut { Err("OSError") } else { Ok(()) }
    }

    fn interrupted_by(&self) -> Vec<String> {
        self.interrupted_by.lock().unwrap().clone()
    }

    fn closed_by(&self) -> Vec<String> {
        self.closed_by.lock().unwrap().clone()
    }
}

impl Interrupt for Line {
    fn interrupt(&self) {
        self.interrupted_by.lock().unwrap().push(me());
        if self.deaf.swap(false, Ordering::SeqCst) {
            return;
        }
        self.drop_it();
    }
}

/// A spec is (key, way). A key is wanted the way `wanted` says.
#[derive(Debug, Default)]
struct Watching {
    wanted: Mutex<Vec<(String, String)>>,
    limit: AtomicUsize,
    lines: Mutex<Vec<Arc<Line>>>,
    /// How many connects fail.
    refuse_connect: Mutex<usize>,
    /// Keys whose connections will never do.
    never_ready: Mutex<BTreeSet<String>>,
    unreadable: AtomicBool,
    /// A connection serves one wait, then ends.
    serve_once: AtomicBool,
    said: Mutex<Vec<String>>,
    /// What each failure rested for.
    rests: Mutex<Vec<Duration>>,
}

impl Watching {
    fn new(keys: &[&str]) -> Arc<Watching> {
        let watching = Watching { limit: AtomicUsize::new(12), ..Watching::default() };
        *watching.wanted.lock().unwrap() = keys.iter().map(|k| ((*k).to_owned(), "first".to_owned())).collect();
        Arc::new(watching)
    }

    fn way(&self, key: &str) -> Option<String> {
        self.wanted.lock().unwrap().iter().find(|(k, _)| k == key).map(|(_, way)| way.clone())
    }

    fn want(&self, key: &str, way: &str) {
        let mut wanted = self.wanted.lock().unwrap();
        match wanted.iter_mut().find(|(k, _)| k == key) {
            Some(entry) => entry.1 = way.to_owned(),
            None => wanted.push((key.to_owned(), way.to_owned())),
        }
    }

    fn forget(&self, key: &str) {
        self.wanted.lock().unwrap().retain(|(k, _)| k != key);
    }

    fn lines(&self) -> Vec<Arc<Line>> {
        self.lines.lock().unwrap().clone()
    }

    fn line_of(&self, key: &str, n: usize) -> Arc<Line> {
        self.lines().into_iter().filter(|l| l.key() == key).nth(n).unwrap()
    }

    fn count(&self, key: &str) -> usize {
        self.lines().iter().filter(|l| l.key() == key).count()
    }

    /// Whether a worker for `key` waits on its connection, and so can be interrupted.
    fn serving(&self, key: &str) -> bool {
        self.lines().iter().any(|l| l.key() == key && l.waiting.load(Ordering::SeqCst))
    }

    fn said(&self) -> Vec<String> {
        self.said.lock().unwrap().clone()
    }
}

impl Watched for Watching {
    type Spec = (String, String);
    type Conn = Arc<Line>;

    fn desired(&self) -> Option<Vec<(String, (String, String))>> {
        if self.unreadable.load(Ordering::SeqCst) {
            return None;
        }
        Some(self.wanted.lock().unwrap().iter().map(|(k, way)| (k.clone(), (k.clone(), way.clone()))).collect())
    }

    fn same(&self, running: &(String, String), wanted: &(String, String)) -> bool {
        running == wanted
    }

    fn wanted(&self, spec: &(String, String)) -> bool {
        self.way(&spec.0).as_ref() == Some(&spec.1)
    }

    fn limit(&self) -> usize {
        self.limit.load(Ordering::SeqCst)
    }

    fn name(&self, spec: &(String, String)) -> String {
        format!("watch:{}", spec.0)
    }

    fn connect(&self, spec: &(String, String)) -> Result<(Arc<Line>, Arc<dyn Interrupt>), &'static str> {
        let mut refusing = self.refuse_connect.lock().unwrap();
        if *refusing > 0 {
            *refusing -= 1;
            return Err("ConnectionRefusedError");
        }
        drop(refusing);
        let line = Arc::new(Line {
            spec: spec.clone(),
            thread: std::thread::current().id(),
            interrupted_by: Mutex::default(),
            closed_by: Mutex::default(),
            signal: Mutex::default(),
            signalled: Condvar::new(),
            deaf: AtomicBool::new(false),
            waiting: AtomicBool::new(false),
        });
        self.lines.lock().unwrap().push(line.clone());
        Ok((line.clone(), line))
    }

    fn ready(&self, spec: &(String, String), _line: &mut Arc<Line>) -> Result<bool, &'static str> {
        Ok(!self.never_ready.lock().unwrap().contains(&spec.0))
    }

    fn serve(&self, watch: &Watch<(String, String)>, line: &mut Arc<Line>) -> Result<(), &'static str> {
        while !watch.leaving() {
            line.wait()?;
            if self.serve_once.load(Ordering::SeqCst) || !self.wanted(&watch.spec) {
                break;
            }
        }
        Ok(())
    }

    fn close(&self, line: Arc<Line>) {
        line.closed_by.lock().unwrap().push(me());
    }

    fn say_unwanted(&self, spec: &(String, String)) {
        self.said.lock().unwrap().push(format!("unwanted {}", spec.0));
    }

    fn say_failed(&self, spec: &(String, String), class: &'static str, rest: Duration) {
        self.said.lock().unwrap().push(format!("failed {} {class}", spec.0));
        self.rests.lock().unwrap().push(rest);
    }
}

/// A supervisor over a `Watching`, stopped as the test ends, its workers seen to have gone.
struct Built {
    sup: Supervisor<Watching>,
    w: Arc<Watching>,
}

impl Drop for Built {
    fn drop(&mut self) {
        self.sup.stop();
        let deadline = Instant::now() + Duration::from_secs(30);
        while self.sup.running() > 0 && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(2));
        }
        let left = self.sup.running();
        self.sup.join();
        if !std::thread::panicking() {
            assert_eq!(left, 0, "a worker didn't stop");
        }
    }
}

fn build(keys: &[&str]) -> Built {
    let w = Watching::new(keys);
    Built { sup: Supervisor::new(w.clone()), w }
}

fn started(b: &Built, rests: Rests) -> usize {
    b.sup.begin(rests);
    b.sup.reconcile()
}

fn keys(b: &Built) -> Vec<String> {
    b.sup.watching().into_iter().map(|(k, _)| k).collect()
}

fn watching(pairs: &[(&str, bool)]) -> Vec<(String, bool)> {
    pairs.iter().map(|(k, running)| ((*k).to_owned(), *running)).collect()
}

/// Wait until the supervisor's worker threads, retired ones included, are down to `n`.
fn running(b: &Built, n: usize) {
    until(|| b.sup.running() == n);
}

#[test]
fn each_key_wanted_gets_a_worker_of_its_own() {
    let b = build(&["a", "b"]);
    assert_eq!(started(&b, Rests::default()), 2);
    assert_eq!(b.sup.watching(), watching(&[("a", true), ("b", true)]));
    until(|| b.w.serving("a") && b.w.serving("b"));
    let (a, bee) = (b.w.line_of("a", 0), b.w.line_of("b", 0));
    assert_ne!(a.thread, bee.thread);
    // Each worker's thread is named for what it watches, and closes its own connection by that name.
    b.sup.stop();
    running(&b, 0);
    assert_eq!((a.closed_by(), bee.closed_by()), (vec!["watch:a".to_owned()], vec!["watch:b".to_owned()]));
}

#[test]
fn a_second_reconcile_with_nothing_changed_leaves_the_workers_be() {
    let b = build(&["a"]);
    started(&b, Rests::default());
    until(|| b.w.serving("a"));
    assert_eq!(b.sup.reconcile(), 1);
    assert_eq!(b.w.lines().len(), 1);
    assert!(b.w.line_of("a", 0).interrupted_by().is_empty());
    assert_eq!(b.sup.running(), 1);
}

#[test]
fn a_key_no_longer_wanted_is_retired_at_once() {
    let b = build(&["a", "b"]);
    started(&b, Rests::default());
    until(|| b.w.serving("a") && b.w.serving("b"));
    b.w.forget("a");
    assert_eq!(b.sup.reconcile(), 1);
    running(&b, 1);
    assert_eq!(keys(&b), ["b"]);
    let a = b.w.line_of("a", 0);
    assert_eq!(a.interrupted_by(), [me()]);
    assert_eq!(a.closed_by(), ["watch:a"]);
}

#[test]
fn a_key_wanted_differently_gets_a_fresh_worker() {
    let b = build(&["a"]);
    started(&b, Rests::default());
    until(|| b.w.serving("a"));
    let old = b.w.line_of("a", 0);
    b.w.want("a", "second");
    b.sup.reconcile();
    until(|| old.closed_by() == ["watch:a"] && b.w.count("a") == 2);
    let new = b.w.line_of("a", 1);
    assert_eq!(new.spec, ("a".to_owned(), "second".to_owned()));
    assert_ne!(new.thread, old.thread);
    assert_eq!(b.sup.watching(), watching(&[("a", true)]));
    running(&b, 1);
}

#[test]
fn workers_start_up_to_the_limit_and_a_retirement_makes_room() {
    let b = build(&["a", "b", "c"]);
    b.w.limit.store(2, Ordering::SeqCst);
    // How many are wanted, not how many run.
    assert_eq!(started(&b, Rests::default()), 3);
    assert_eq!(keys(&b), ["a", "b"]);
    b.w.forget("a");
    assert_eq!(b.sup.reconcile(), 2);
    assert_eq!(keys(&b), ["b", "c"]);
}

#[test]
fn before_begin_and_after_stop_reconcile_starts_nothing() {
    let b = build(&["a"]);
    assert_eq!(b.sup.reconcile(), 0);
    assert!(b.sup.watching().is_empty() && b.w.lines().is_empty());
    started(&b, Rests::default());
    until(|| b.w.serving("a"));
    b.sup.stop();
    running(&b, 0);
    assert_eq!(b.sup.reconcile(), 0);
    assert!(b.sup.watching().is_empty());
    assert_eq!(b.w.lines().len(), 1);
}

#[test]
fn when_what_is_wanted_cant_be_told_the_workers_stay_as_they_are() {
    let b = build(&["a"]);
    started(&b, Rests::default());
    until(|| b.w.serving("a"));
    b.w.unreadable.store(true, Ordering::SeqCst);
    assert_eq!(b.sup.reconcile(), 0);
    assert_eq!(b.sup.watching(), watching(&[("a", true)]));
    assert_eq!(b.w.lines().len(), 1);
    assert!(b.w.line_of("a", 0).interrupted_by().is_empty());
}

#[test]
fn stop_interrupts_each_connection_and_leaves_closing_it_to_its_worker() {
    let b = build(&["a", "b"]);
    started(&b, Rests::default());
    until(|| b.w.serving("a") && b.w.serving("b"));
    let (a, bee) = (b.w.line_of("a", 0), b.w.line_of("b", 0));
    b.sup.stop();
    running(&b, 0);
    assert_eq!((a.interrupted_by(), bee.interrupted_by()), (vec![me()], vec![me()]));
    assert_eq!((a.closed_by(), bee.closed_by()), (vec!["watch:a".to_owned()], vec!["watch:b".to_owned()]));
    assert!(b.sup.watching().is_empty());
    // Each worker took its connection off the list as it went.
    b.sup.stop();
    assert_eq!((a.interrupted_by(), bee.interrupted_by()), (vec![me()], vec![me()]));
}

#[test]
fn a_retired_worker_still_on_its_way_out_is_interrupted_by_stop_too() {
    // A retired worker leaves the table at once, but its connection is listed until it has gone.
    let b = build(&["a"]);
    started(&b, Rests::default());
    until(|| b.w.serving("a"));
    let line = b.w.line_of("a", 0);
    line.deaf.store(true, Ordering::SeqCst);
    b.w.forget("a");
    b.sup.reconcile();
    assert!(b.sup.watching().is_empty());
    assert_eq!(b.sup.running(), 1);
    b.sup.stop();
    running(&b, 0);
    assert_eq!(line.interrupted_by().len(), 2);
    assert_eq!(line.closed_by(), ["watch:a"]);
}

#[test]
fn a_connection_that_never_will_do_ends_its_worker_and_is_closed() {
    let b = build(&["a"]);
    b.w.never_ready.lock().unwrap().insert("a".to_owned());
    started(&b, Rests::default());
    until(|| !b.w.lines().is_empty());
    running(&b, 0);
    let line = b.w.line_of("a", 0);
    assert_eq!(b.sup.watching(), watching(&[("a", false)]));
    assert_eq!(line.closed_by(), ["watch:a"]);
    // It isn't started again.
    b.sup.reconcile();
    assert_eq!(b.sup.watching(), watching(&[("a", false)]));
    assert_eq!(b.w.lines().len(), 1);
    // And it never listed its connection to be interrupted.
    b.sup.stop();
    assert!(line.interrupted_by().is_empty());
}

#[test]
fn a_connection_that_ends_while_still_wanted_is_closed_and_replaced() {
    let b = build(&["a"]);
    b.w.serve_once.store(true, Ordering::SeqCst);
    started(&b, Rests::default());
    until(|| b.w.serving("a"));
    let first = b.w.line_of("a", 0);
    first.news();
    until(|| first.closed_by() == ["watch:a"] && b.w.count("a") == 2);
    let second = b.w.line_of("a", 1);
    until(|| second.waiting.load(Ordering::SeqCst));
    assert_eq!(b.sup.watching(), watching(&[("a", true)]));
    assert!(b.w.said().is_empty());
    // The first is off the list, the second on it.
    b.sup.stop();
    assert!(first.interrupted_by().is_empty());
    assert_eq!(second.interrupted_by(), [me()]);
}

#[test]
fn a_worker_rests_twice_as_long_each_time_up_to_the_longest() {
    let b = build(&["a"]);
    // As 5 seconds is to 300.
    *b.w.refuse_connect.lock().unwrap() = 8;
    started(&b, Rests { first: TICK, longest: TICK * 60 });
    until(|| b.w.serving("a"));
    let rests: Vec<u32> = b.w.rests.lock().unwrap().iter().map(|r| (r.as_micros() / TICK.as_micros()) as u32).collect();
    assert_eq!(rests, [1, 2, 4, 8, 16, 32, 60, 60]);
}

#[test]
fn a_connection_that_serves_starts_the_rests_over() {
    let b = build(&["a"]);
    *b.w.refuse_connect.lock().unwrap() = 3;
    started(&b, Rests { first: TICK, longest: TICK * 60 });
    until(|| b.w.serving("a"));
    assert_eq!(b.sup.next_rest("a"), Some(TICK));
    b.w.line_of("a", 0).drop_it();
    until(|| b.w.count("a") == 2 && b.w.line_of("a", 1).waiting.load(Ordering::SeqCst));
    let rests: Vec<u32> = b.w.rests.lock().unwrap().iter().map(|r| (r.as_micros() / TICK.as_micros()) as u32).collect();
    assert_eq!(rests, [1, 2, 4, 1]);
    assert_eq!(b.w.line_of("a", 0).closed_by(), ["watch:a"]);
    assert_eq!(b.sup.next_rest("b"), None);
}

#[test]
fn a_worker_says_what_failed() {
    let b = build(&["a"]);
    *b.w.refuse_connect.lock().unwrap() = 1;
    started(&b, Rests { first: TICK, longest: TICK });
    until(|| b.w.said() == ["failed a ConnectionRefusedError"]);
}

#[test]
fn a_worker_interrupted_as_it_leaves_says_nothing_failed() {
    let b = build(&["a"]);
    started(&b, Rests::default());
    until(|| b.w.serving("a"));
    let line = b.w.line_of("a", 0);
    b.sup.stop();
    running(&b, 0);
    assert!(line.shut());
    assert!(b.w.said().is_empty());
}

/// One left to rest is still resting when the test gives up on it.
const AN_HOUR: Rests = Rests { first: Duration::from_secs(3600), longest: Duration::from_secs(3600) };

#[test]
fn a_worker_resting_is_woken_at_once_when_retired() {
    let b = build(&["a"]);
    *b.w.refuse_connect.lock().unwrap() = 1;
    started(&b, AN_HOUR);
    until(|| !b.w.said().is_empty());
    b.w.forget("a");
    b.sup.reconcile();
    running(&b, 0);
}

#[test]
fn a_worker_resting_is_woken_at_once_when_stopped() {
    let b = build(&["a"]);
    *b.w.refuse_connect.lock().unwrap() = 1;
    started(&b, AN_HOUR);
    until(|| !b.w.said().is_empty());
    b.sup.stop();
    running(&b, 0);
}

#[test]
fn a_worker_that_finds_itself_unwanted_says_so_and_hands_over() {
    // Changed with no one to say so: it notices for itself, and asks for a reconcile on its way out.
    let b = build(&["a"]);
    started(&b, Rests::default());
    until(|| b.w.serving("a"));
    let old = b.w.line_of("a", 0);
    b.w.want("a", "second");
    old.news();
    until(|| old.closed_by() == ["watch:a"]);
    until(|| b.w.count("a") == 2 && b.w.line_of("a", 1).waiting.load(Ordering::SeqCst));
    assert_eq!(b.w.said(), ["unwanted a"]);
    let new = b.w.line_of("a", 1);
    assert_eq!(new.spec, ("a".to_owned(), "second".to_owned()));
    assert_ne!(new.thread, old.thread);
    running(&b, 1);
}

#[test]
fn it_asks_only_the_standard_library_and_knows_nothing_of_mail() {
    let source = include_str!("../src/idle_supervisor.rs");
    for line in source.lines().filter(|l| l.starts_with("use ")) {
        assert!(line.starts_with("use std::"), "{line}");
    }
    let words: BTreeSet<String> =
        source.split_whitespace().map(|w| w.trim_matches(|c: char| !c.is_alphanumeric()).to_lowercase()).collect();
    for word in ["imap", "mail", "account", "inbox"] {
        assert!(!words.contains(word), "{word}");
    }
}

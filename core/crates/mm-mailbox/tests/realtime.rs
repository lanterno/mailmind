//! IDLE's workers (`realtime`), over sessions that only record what they are sent: one worker for each account, the
//! IDLE loop, the dispatcher that tells of a burst of changes at once, and the workers following the accounts as they
//! change (the Python app's tests/integration/mailbox/test_idle_supervisor.py, tests/characterization/mailbox/
//! test_pool_and_idle.py and the dispatcher's tests in tests/unit/bootstrap/test_bootstrap.py). The Rust server's
//! tests/idle.rs holds the same against an IMAP server on loopback, through the app's logins and account changes.

#![allow(clippy::unwrap_used)]

use std::collections::{BTreeMap, BTreeSet};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::thread::ThreadId;
use std::time::{Duration, Instant};

use mm_kernel::errors::Fault;
use mm_mailbox::idle_supervisor::{Interrupt, Rests};
use mm_mailbox::realtime::{Pace, Realtime, RealtimeParts};
use mm_mailbox::realtime_ports::{IdleAccounts, IdleSession, IdleSessions, InboxNews, Reached};
use mm_mailbox::session::{Selected, ServerError};
use serde_json::json;

const ADA: &str = "ada@example.test";
const BEA: &str = "bea@example.test";
const CY: &str = "cy@example.test";
const DEE: &str = "dee@example.test";

fn until(what: impl Fn() -> bool) {
    let deadline = Instant::now() + Duration::from_secs(10);
    while !what() {
        assert!(Instant::now() < deadline, "it never came");
        std::thread::sleep(Duration::from_millis(2));
    }
}

/// The accounts, as a test changes them.
#[derive(Debug, Default)]
struct Accounts(Mutex<Vec<Reached>>);

impl Accounts {
    fn of(emails: &[&str]) -> Arc<Accounts> {
        let accounts = emails.iter().map(|e| Reached { email: (*e).to_owned(), way: json!("first") }).collect();
        Arc::new(Accounts(Mutex::new(accounts)))
    }

    fn reach(&self, email: &str, way: &str) {
        let mut accounts = self.0.lock().unwrap();
        match accounts.iter_mut().find(|a| a.email == email) {
            Some(account) => account.way = json!(way),
            None => accounts.push(Reached { email: email.to_owned(), way: json!(way) }),
        }
    }

    fn remove(&self, email: &str) {
        self.0.lock().unwrap().retain(|a| a.email != email);
    }
}

impl IdleAccounts for Accounts {
    fn accounts(&self) -> Result<Vec<Reached>, Fault> {
        Ok(self.0.lock().unwrap().clone())
    }

    fn get(&self, email: &str) -> Result<Option<Reached>, Fault> {
        Ok(self.0.lock().unwrap().iter().find(|a| a.email == email).cloned())
    }
}

#[derive(Debug, Default)]
struct State {
    /// What each session was sent, in order: (its number, the account, the command).
    sent: Vec<(usize, String, String)>,
    /// New mail waiting to be said to each account's IDLE.
    news: BTreeMap<String, usize>,
    /// The sessions shut down, by number.
    shut: BTreeSet<usize>,
    /// Each session opened: its account and the thread that opened it.
    opened: Vec<(String, ThreadId)>,
    /// The thread that logged each session out, by number.
    logged_out_by: BTreeMap<usize, ThreadId>,
    interrupted: Vec<String>,
}

/// The servers: they record what each session sends, and say a change to a session that idles.
#[derive(Debug, Default)]
struct Servers {
    state: Mutex<State>,
    changed: Condvar,
    without_idle: AtomicBool,
    /// Opens that fail, by account.
    refusing: Mutex<BTreeMap<String, usize>>,
}

impl Servers {
    fn held(&self) -> std::sync::MutexGuard<'_, State> {
        self.state.lock().unwrap()
    }

    fn deliver(&self, email: &str) {
        *self.held().news.entry(email.to_owned()).or_default() += 1;
        self.changed.notify_all();
    }

    /// What `email`'s sessions were sent.
    fn sent(&self, email: &str) -> Vec<String> {
        self.held().sent.iter().filter(|(_, who, _)| who == email).map(|(_, _, c)| c.clone()).collect()
    }

    fn count(&self, email: &str, command: &str) -> usize {
        self.sent(email).iter().filter(|c| *c == command).count()
    }

    fn idling(&self, email: &str, n: usize) -> bool {
        self.count(email, "IDLE") >= n
    }

    fn opened(&self, email: &str) -> Vec<ThreadId> {
        self.held().opened.iter().filter(|(who, _)| who == email).map(|(_, t)| *t).collect()
    }

    fn interrupted(&self) -> Vec<String> {
        self.held().interrupted.clone()
    }
}

#[derive(Debug)]
struct Session {
    n: usize,
    email: String,
    servers: Arc<Servers>,
}

impl Session {
    fn send(&self, command: &str) {
        self.servers.held().sent.push((self.n, self.email.clone(), command.to_owned()));
    }
}

#[derive(Debug)]
struct Interrupter {
    n: usize,
    email: String,
    servers: Arc<Servers>,
}

impl Interrupt for Interrupter {
    fn interrupt(&self) {
        let mut state = self.servers.held();
        state.interrupted.push(self.email.clone());
        state.shut.insert(self.n);
        drop(state);
        self.servers.changed.notify_all();
    }
}

const LOST: ServerError = ServerError::Unreachable("ConnectionLost");

impl IdleSession for Session {
    fn capabilities(&self) -> Vec<String> {
        let mut capabilities = vec!["IMAP4REV1".to_owned()];
        if !self.servers.without_idle.load(Ordering::SeqCst) {
            capabilities.push("IDLE".to_owned());
        }
        capabilities
    }

    fn select(&mut self, folder: &str, readonly: bool) -> Result<Selected, ServerError> {
        self.send(&format!("{} {folder}", if readonly { "EXAMINE" } else { "SELECT" }));
        Ok(Selected::default())
    }

    fn idle(&mut self, wait: Duration) -> Result<bool, ServerError> {
        self.send("IDLE");
        let state = self.servers.held();
        let (mut state, _) = self
            .servers
            .changed
            .wait_timeout_while(state, wait, |s| {
                !s.shut.contains(&self.n) && s.news.get(&self.email).copied().unwrap_or(0) == 0
            })
            .unwrap();
        if state.shut.contains(&self.n) {
            return Err(LOST);
        }
        let changed = state.news.remove(&self.email).is_some();
        state.sent.push((self.n, self.email.clone(), "DONE".to_owned()));
        Ok(changed)
    }

    fn interrupter(&self) -> Result<Arc<dyn Interrupt>, ServerError> {
        Ok(Arc::new(Interrupter { n: self.n, email: self.email.clone(), servers: self.servers.clone() }))
    }

    fn logout(&mut self) {
        let mut state = self.servers.held();
        state.logged_out_by.insert(self.n, std::thread::current().id());
        if !state.shut.contains(&self.n) {
            state.sent.push((self.n, self.email.clone(), "LOGOUT".to_owned()));
        }
    }
}

#[derive(Debug)]
struct Sessions(Arc<Servers>);

impl IdleSessions for Sessions {
    fn open(&self, email: &str) -> Result<Box<dyn IdleSession>, ServerError> {
        if let Some(left) = self.0.refusing.lock().unwrap().get_mut(email).filter(|n| **n > 0) {
            *left -= 1;
            self.0.held().opened.push((email.to_owned(), std::thread::current().id()));
            return Err(ServerError::Unreachable("LoginRefused"));
        }
        let mut state = self.0.held();
        state.opened.push((email.to_owned(), std::thread::current().id()));
        let n = state.opened.len();
        Ok(Box::new(Session { n, email: email.to_owned(), servers: self.0.clone() }))
    }
}

/// Who hears IDLE's news: each InboxChanged's accounts, in turn. While `holding`, the first to come waits.
#[derive(Debug, Default)]
struct News {
    heard: Mutex<Vec<BTreeSet<String>>>,
    holding: Mutex<bool>,
    let_go: Condvar,
}

impl News {
    fn heard(&self) -> Vec<Vec<&'static str>> {
        let names = [ADA, BEA, CY, DEE];
        self.heard
            .lock()
            .unwrap()
            .iter()
            .map(|set| names.iter().copied().filter(|n| set.contains(*n)).collect())
            .collect()
    }

    fn release(&self) {
        *self.holding.lock().unwrap() = false;
        self.let_go.notify_all();
    }
}

impl InboxNews for News {
    fn inbox_changed(&self, accounts: BTreeSet<String>) {
        self.heard.lock().unwrap().push(accounts);
        let holding = self.holding.lock().unwrap();
        drop(self.let_go.wait_while(holding, |holding| *holding).unwrap());
    }
}

struct World {
    realtime: Realtime,
    accounts: Arc<Accounts>,
    servers: Arc<Servers>,
    news: Arc<News>,
}

impl Drop for World {
    fn drop(&mut self) {
        self.news.release();
        self.realtime.stop();
        let deadline = Instant::now() + Duration::from_secs(30);
        while self.realtime.running() > 0 && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(2));
        }
        let left = self.realtime.running();
        self.realtime.join();
        if !std::thread::panicking() {
            assert_eq!(left, 0, "an IDLE worker didn't stop");
        }
    }
}

fn world(emails: &[&str]) -> World {
    let (accounts, servers, news) = (Accounts::of(emails), Arc::new(Servers::default()), Arc::new(News::default()));
    let realtime = Realtime::new(RealtimeParts {
        accounts: accounts.clone(),
        sessions: Arc::new(Sessions(servers.clone())),
        news: news.clone(),
    });
    World { realtime, accounts, servers, news }
}

/// A pace of moments: the dispatcher looks often, and a failure rests briefly.
fn quick() -> Pace {
    Pace {
        debounce: Duration::from_millis(20),
        rests: Rests { first: Duration::from_millis(2), longest: Duration::from_millis(4) },
        ..Pace::default()
    }
}

fn watching(w: &World) -> Vec<(String, bool)> {
    w.realtime.watching()
}

fn watched(pairs: &[(&str, bool)]) -> Vec<(String, bool)> {
    pairs.iter().map(|(k, running)| ((*k).to_owned(), *running)).collect()
}

#[test]
fn start_watches_every_account_each_on_its_inbox() {
    let w = world(&[ADA, BEA, CY]);
    w.realtime.start(quick());
    until(|| [ADA, BEA, CY].iter().all(|e| w.servers.idling(e, 1)));
    assert_eq!(watching(&w), watched(&[(ADA, true), (BEA, true), (CY, true)]));
    // Its INBOX selected read-write, as the Python app's worker selects it, then IDLE.
    assert_eq!(w.servers.sent(ADA), ["SELECT INBOX", "IDLE"]);
    let threads: std::collections::HashSet<ThreadId> = [ADA, BEA, CY].iter().map(|e| w.servers.opened(e)[0]).collect();
    assert_eq!(threads.len(), 3);
}

#[test]
fn the_worker_leaves_idle_before_it_asks_for_a_sync_on_another_connection() {
    let w = world(&[ADA, BEA]);
    w.realtime.start(quick());
    until(|| w.servers.idling(ADA, 1) && w.servers.idling(BEA, 1));
    w.servers.deliver(ADA);
    until(|| w.servers.idling(ADA, 2));
    until(|| !w.news.heard().is_empty());
    assert_eq!(w.news.heard(), [[ADA]]);
    // DONE, then idling again: nothing of the sync goes on this connection.
    assert_eq!(w.servers.sent(ADA), ["SELECT INBOX", "IDLE", "DONE", "IDLE"]);
    assert_eq!(w.servers.opened(ADA).len(), 1);
}

#[test]
fn a_burst_of_changes_is_told_as_one() {
    let w = world(&[ADA, BEA, CY]);
    *w.news.holding.lock().unwrap() = true;
    w.realtime.start(quick());
    until(|| [ADA, BEA, CY].iter().all(|e| w.servers.idling(e, 1)));
    w.servers.deliver(ADA);
    // The first is told, and its sync runs while more mail comes.
    until(|| w.news.heard().len() == 1);
    for email in [BEA, ADA, CY] {
        w.servers.deliver(email);
    }
    until(|| w.servers.idling(ADA, 3) && w.servers.idling(BEA, 2) && w.servers.idling(CY, 2));
    w.news.release();
    until(|| w.news.heard().len() == 2);
    assert_eq!(w.news.heard(), [vec![ADA], vec![ADA, BEA, CY]]);
}

#[test]
fn a_removed_account_stops_being_watched_at_once() {
    let w = world(&[ADA, BEA, CY]);
    w.realtime.start(quick());
    until(|| [ADA, BEA, CY].iter().all(|e| w.servers.idling(e, 1)));
    w.accounts.remove(BEA);
    assert_eq!(w.realtime.reconcile(), 2);
    // Out of an IDLE it would hold for 24 minutes.
    until(|| w.realtime.running() == 2);
    assert_eq!(watching(&w), watched(&[(ADA, true), (CY, true)]));
    assert_eq!(w.servers.interrupted(), [BEA]);
    // Its worker, and no one else, closed its session.
    let worker = w.servers.opened(BEA)[0];
    assert_eq!(w.servers.held().logged_out_by.values().collect::<Vec<_>>(), [&worker]);
}

#[test]
fn an_account_reached_another_way_gets_a_fresh_worker() {
    let w = world(&[ADA]);
    w.realtime.start(quick());
    until(|| w.servers.idling(ADA, 1));
    let old = w.servers.opened(ADA)[0];
    w.accounts.reach(ADA, "a new password");
    w.realtime.reconcile();
    until(|| w.servers.idling(ADA, 2));
    let opened = w.servers.opened(ADA);
    assert_eq!(opened.len(), 2);
    assert_ne!(opened[1], old);
    assert_eq!(watching(&w), watched(&[(ADA, true)]));
    until(|| w.realtime.running() == 1);
}

#[test]
fn a_change_that_doesnt_touch_how_it_is_reached_leaves_the_worker_be() {
    let w = world(&[ADA]);
    w.realtime.start(quick());
    until(|| w.servers.idling(ADA, 1));
    // An alias or a signature: the account is reached as it was.
    assert_eq!(w.realtime.reconcile(), 1);
    assert!(w.servers.interrupted().is_empty());
    assert_eq!(w.servers.opened(ADA).len(), 1);
}

#[test]
fn an_added_account_is_watched() {
    let w = world(&[ADA]);
    w.realtime.start(quick());
    w.accounts.reach(DEE, "first");
    assert_eq!(w.realtime.reconcile(), 2);
    until(|| w.servers.idling(DEE, 1));
    assert_eq!(watching(&w), watched(&[(ADA, true), (DEE, true)]));
}

#[test]
fn past_the_cap_a_removed_account_makes_room_for_the_next() {
    let w = world(&[ADA, BEA, CY]);
    w.realtime.start(Pace { most: 2, ..quick() });
    assert_eq!(watching(&w), watched(&[(ADA, true), (BEA, true)]));
    w.accounts.remove(ADA);
    w.realtime.reconcile();
    until(|| w.servers.idling(CY, 1));
    assert_eq!(watching(&w), watched(&[(BEA, true), (CY, true)]));
}

#[test]
fn a_server_without_idle_ends_its_worker_and_is_logged_out_of_once() {
    let w = world(&[ADA]);
    w.servers.without_idle.store(true, Ordering::SeqCst);
    w.realtime.start(quick());
    until(|| w.realtime.running() == 0);
    // The periodic sync has it.
    assert_eq!(watching(&w), watched(&[(ADA, false)]));
    assert_eq!(w.servers.sent(ADA), ["LOGOUT"]);
}

#[test]
fn a_worker_whose_account_changed_unsaid_hands_over_after_an_idle_cycle() {
    let w = world(&[ADA]);
    w.realtime.start(Pace { idle: Duration::from_millis(50), ..quick() });
    until(|| w.servers.idling(ADA, 1));
    let old = w.servers.opened(ADA)[0];
    // No reconcile: as an edit of the file by hand, which says nothing.
    w.accounts.reach(ADA, "edited by hand");
    until(|| w.servers.opened(ADA).len() == 2);
    assert_ne!(w.servers.opened(ADA)[1], old);
    until(|| w.realtime.running() == 1);
    assert_eq!(watching(&w), watched(&[(ADA, true)]));
}

#[test]
fn a_worker_whose_login_fails_rests_and_tries_again() {
    let w = world(&[ADA]);
    w.servers.refusing.lock().unwrap().insert(ADA.to_owned(), 3);
    w.realtime.start(quick());
    until(|| w.servers.idling(ADA, 1));
    assert_eq!(w.servers.opened(ADA).len(), 4);
    assert_eq!(watching(&w), watched(&[(ADA, true)]));
}

#[test]
fn a_worker_resting_stops_at_once_when_its_account_goes() {
    let w = world(&[ADA]);
    w.servers.refusing.lock().unwrap().insert(ADA.to_owned(), usize::MAX);
    let an_hour = Duration::from_secs(3600);
    w.realtime.start(Pace { rests: Rests { first: an_hour, longest: an_hour }, ..quick() });
    until(|| w.servers.opened(ADA).len() == 1);
    w.accounts.remove(ADA);
    w.realtime.reconcile();
    until(|| w.realtime.running() == 0);
    assert!(watching(&w).is_empty());
}

#[test]
fn stop_interrupts_each_idle_and_each_worker_logs_out_of_its_own() {
    let w = world(&[ADA, BEA]);
    w.realtime.start(quick());
    until(|| w.servers.idling(ADA, 1) && w.servers.idling(BEA, 1));
    w.realtime.stop();
    until(|| w.realtime.running() == 0);
    let mut interrupted = w.servers.interrupted();
    interrupted.sort();
    assert_eq!(interrupted, [ADA, BEA]);
    let state = w.servers.held();
    for (n, (_, opener)) in state.opened.iter().enumerate() {
        assert_eq!(state.logged_out_by.get(&(n + 1)), Some(opener));
    }
}

#[test]
fn before_start_and_after_stop_it_starts_nothing() {
    let w = world(&[ADA]);
    assert_eq!(w.realtime.reconcile(), 0);
    assert!(watching(&w).is_empty());
    w.realtime.start(quick());
    until(|| w.servers.idling(ADA, 1));
    // A second start starts nothing more.
    w.realtime.start(quick());
    assert_eq!(w.servers.opened(ADA).len(), 1);
    w.realtime.stop();
    assert_eq!(w.realtime.reconcile(), 0);
    assert!(watching(&w).is_empty());
    w.realtime.start(quick());
    assert!(watching(&w).is_empty());
}

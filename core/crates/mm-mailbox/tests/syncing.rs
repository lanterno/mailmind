//! `syncing`: each account's INBOX synced through the single flight, and how each account's sync stands. A world of
//! stand-ins: a server that never answers as it was asked to, a store that keeps nothing, and a clock the test moves.
//! The served suites hold what a sync stores; these hold what the flight and an account's standing do when a sync
//! goes wrong. Every address is fictional.

#![allow(clippy::unwrap_used)]

use std::collections::{BTreeMap, BTreeSet, HashSet};
use std::sync::atomic::{AtomicI64, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use mm_content::mime::Fields;
use mm_kernel::errors::Fault;
use mm_kernel::seams::{Clock, Moment};
use mm_mailbox::command_ports::{CommandStore, MoveAccount, MoveAccounts, Tx};
use mm_mailbox::fetch_ports::KeptBody;
use mm_mailbox::folders::Folders;
use mm_mailbox::ingest::{Ingest, SyncError};
use mm_mailbox::ports::{Accounts, MailSession, MailSessions, SessionError, StoredMail, ViewCounts};
use mm_mailbox::session::{MailServer, MailServers, ServerError};
use mm_mailbox::sync_ports::{
    AroundSync, Classifier, Envelope, Events, FlagRow, FolderState, Incoming, MailboxEvent, Place, SyncAccount,
    SyncAccounts, SyncStore, Verdict,
};
use mm_mailbox::syncing::{AccountSync, Syncing, SyncingParts};
use mm_mailbox::warm::{Warming, WarmingParts};
use mm_mailbox::warm_ports::{WarmAccount, WarmAccounts, WarmSync};

/// An account whose server's opening panics, as a malformed answer could make it.
const PANICS: &str = "panics@example.test";

#[derive(Debug, Default)]
struct World {
    emails: Mutex<Vec<String>>,
    /// What opening each account's server fails by.
    fails: Mutex<BTreeMap<String, ServerError>>,
    opened: AtomicUsize,
    /// The sync results the store was asked to keep.
    recorded: AtomicUsize,
    woken: AtomicUsize,
    /// How many rounds' first step panics.
    panic_waking: AtomicUsize,
    /// The clock, in microseconds.
    now: AtomicI64,
}

impl World {
    fn with(emails: &[&str]) -> Arc<World> {
        let world = World { now: AtomicI64::new(1_793_620_800_000_000), ..World::default() };
        *world.emails.lock().unwrap() = emails.iter().map(|e| (*e).to_owned()).collect();
        Arc::new(world)
    }
}

impl Clock for World {
    fn now(&self) -> Moment {
        Moment::from_micros(self.now.load(Ordering::SeqCst))
    }
}

impl SyncAccounts for World {
    fn sync_account(&self, account: &str) -> Result<Option<SyncAccount>, Fault> {
        Ok(Some(SyncAccount { email: account.to_owned(), kind: "imap".to_owned(), gmail_threads: false }))
    }
}

impl MailServers for World {
    fn open(&self, account: &str, _timeout_seconds: u64) -> Result<Box<dyn MailServer>, ServerError> {
        self.opened.fetch_add(1, Ordering::SeqCst);
        assert_ne!(account, PANICS, "a server whose answer panics");
        Err(self.fails.lock().unwrap().get(account).copied().unwrap_or(ServerError::Unreachable("ConnectionRefused")))
    }
}

/// Nothing is stored: every sync here fails before it reads a message.
impl SyncStore for World {
    fn folder_state(&self, _: &str, _: &str) -> Result<FolderState, Fault> {
        unreachable!()
    }
    fn reset_folder(&self, _: &str, _: &str, _: i64) -> Result<(), Fault> {
        unreachable!()
    }
    fn set_uidvalidity(&self, _: &str, _: &str, _: u32) -> Result<(), Fault> {
        unreachable!()
    }
    fn store_copy(&self, _: &SyncAccount, _: &str, _: &Incoming) -> Result<(i64, bool), Fault> {
        unreachable!()
    }
    fn store_step(
        &self,
        _: &SyncAccount,
        _: &str,
        _: &[Incoming],
        _: &Place,
    ) -> Result<Option<Vec<(i64, bool)>>, Fault> {
        unreachable!()
    }
    fn reading_older(&self) -> Result<Vec<(String, String, u32)>, Fault> {
        unreachable!()
    }
    fn flag_rows(
        &self,
        _: &str,
        _: &str,
        _: Option<&[u32]>,
        _: usize,
    ) -> Result<(Vec<FlagRow>, HashSet<String>), Fault> {
        unreachable!()
    }
    fn write_flags(&self, _: &str, _: &str, _: &[(FlagRow, [i64; 5])], _: Option<u64>) -> Result<usize, Fault> {
        unreachable!()
    }
    fn settle(&self, _: &str, _: &str, _: Option<u64>, _: &[i64], _: &str) -> Result<(), Fault> {
        unreachable!()
    }
    fn is_known(&self, _: &str, _: &Fields) -> Result<bool, Fault> {
        unreachable!()
    }
    fn queue_filtered(&self, _: i64, _: &[(&'static str, bool)], _: &str) -> Result<bool, Fault> {
        unreachable!()
    }
    fn keep_invite(&self, _: i64, _: &KeptBody) -> Result<(), Fault> {
        unreachable!()
    }
}

impl Classifier for World {
    fn classify(&self, _: &[Envelope<'_>]) -> Result<Vec<Verdict>, Fault> {
        unreachable!()
    }
}

impl Events for World {
    fn publish(&self, _: MailboxEvent) {}
}

impl MoveAccounts for World {
    fn move_account(&self, _: &str) -> Result<Option<MoveAccount>, Fault> {
        Ok(None)
    }

    fn emails(&self) -> Result<Vec<String>, Fault> {
        Ok(self.emails.lock().unwrap().clone())
    }
}

/// A store that keeps no sync result, and says so: what a sync asked of it is counted.
impl CommandStore for World {
    fn write(&self, _: &mut dyn FnMut(&dyn Tx) -> Result<(), Fault>) -> Result<(), Fault> {
        self.recorded.fetch_add(1, Ordering::SeqCst);
        Err(Fault::new("NotKept"))
    }

    fn read(&self, _: &mut dyn FnMut(&dyn Tx) -> Result<(), Fault>) -> Result<(), Fault> {
        Err(Fault::new("NotKept"))
    }
}

impl Accounts for World {
    fn known(&self, _: &str) -> Result<bool, Fault> {
        Ok(true)
    }
}

impl MailSessions for World {
    fn open(&self, _: &str) -> Result<Box<dyn MailSession>, SessionError> {
        Err(SessionError::Unreachable("Timeout"))
    }
}

impl StoredMail for World {
    fn stored_folder_counts(&self, _: &str) -> Result<Vec<(String, Option<i64>, i64)>, Fault> {
        Ok(Vec::new())
    }

    fn view_counts(&self, _: &[String]) -> Result<ViewCounts, Fault> {
        Ok(ViewCounts::default())
    }
}

impl WarmAccounts for World {
    fn warm_accounts(&self) -> Result<Vec<WarmAccount>, Fault> {
        Ok(Vec::new())
    }
}

impl WarmSync for World {
    fn sync_recent(&self, _: &str, _: &str, _: usize) -> Result<usize, SyncError> {
        unreachable!()
    }
}

impl AroundSync for World {
    fn wake_due(&self) {
        self.woken.fetch_add(1, Ordering::SeqCst);
        let panicking = self.panic_waking.load(Ordering::SeqCst);
        if panicking > 0 {
            self.panic_waking.store(panicking - 1, Ordering::SeqCst);
            panic!("a snooze that panics");
        }
    }

    fn new_mail(&self) {}
}

fn syncing(world: &Arc<World>) -> Syncing {
    let ingest = Ingest::new(world.clone(), world.clone(), world.clone(), world.clone(), world.clone(), world.clone());
    Syncing::new(SyncingParts {
        ingest: Arc::new(ingest),
        accounts: world.clone(),
        store: world.clone(),
        folders: Arc::new(Folders::new(world.clone(), world.clone(), world.clone(), world.clone())),
        events: world.clone(),
        clock: world.clone(),
        around: world.clone(),
        warm: Arc::new(Warming::new(WarmingParts {
            accounts: world.clone(),
            servers: world.clone(),
            sync: world.clone(),
            variable: None,
            pause: Duration::ZERO,
        })),
    })
}

fn standing(syncing: &Syncing, account: &str) -> Option<AccountSync> {
    syncing.status().unwrap().get(account).cloned()
}

fn error(class: &'static str) -> Option<AccountSync> {
    Some(AccountSync { health: "error", msg: class.to_owned(), reading: None })
}

#[test]
fn an_account_whose_sync_panics_says_so_and_the_flight_comes_down_for_the_next() {
    let world = World::with(&[PANICS]);
    let syncing = syncing(&world);
    syncing.sync_guarded(None);
    assert_eq!(standing(&syncing, PANICS), error("Panic"));
    assert!(!syncing.sync().unwrap().running);
    // The next sync runs, as it would have.
    syncing.sync_guarded(Some(BTreeSet::from([PANICS.to_owned()])));
    assert_eq!(world.opened.load(Ordering::SeqCst), 2);
    assert!(!syncing.sync().unwrap().running);
}

#[test]
fn a_round_that_panics_before_any_account_brings_the_flight_down_too() {
    let world = World::with(&[]);
    world.panic_waking.store(1, Ordering::SeqCst);
    let syncing = syncing(&world);
    syncing.sync_once();
    assert!(!syncing.sync().unwrap().running);
    syncing.sync_once();
    assert_eq!(world.woken.load(Ordering::SeqCst), 2);
    // The round that went on to the end says when.
    assert!(syncing.sync().unwrap().last.is_some());
}

const ADA: &str = "ada@example.test";
const BEA: &str = "bea@example.test";

fn fail(world: &World, account: &str, error: ServerError) {
    world.fails.lock().unwrap().insert(account.to_owned(), error);
}

fn pass(world: &World, seconds: i64) {
    world.now.fetch_add(seconds * 1_000_000, Ordering::SeqCst);
}

#[test]
fn a_connection_lost_once_shows_nothing_and_keeps_no_error_until_the_next_sync_fails_too() {
    let world = World::with(&[ADA]);
    let syncing = syncing(&world);
    syncing.sync_guarded(None);
    assert_eq!(standing(&syncing, ADA), None, "it shows as it did before");
    assert_eq!(world.recorded.load(Ordering::SeqCst), 0, "nor does its record keep an error");
    syncing.sync_guarded(None);
    assert_eq!(standing(&syncing, ADA), error("ConnectionRefused"));
    assert_eq!(world.recorded.load(Ordering::SeqCst), 1);
}

#[test]
fn a_failure_that_may_pass_shows_three_minutes_on_though_no_sync_has_run_since() {
    let world = World::with(&[ADA]);
    fail(&world, ADA, ServerError::Unreachable("Timeout"));
    let syncing = syncing(&world);
    syncing.sync_guarded(None);
    pass(&world, 179);
    assert_eq!(standing(&syncing, ADA), None);
    pass(&world, 1);
    assert_eq!(standing(&syncing, ADA), error("Timeout"));
}

#[test]
fn a_login_the_server_cant_take_for_now_may_pass_and_one_it_refused_shows_at_once() {
    let world = World::with(&[ADA, BEA]);
    fail(&world, ADA, ServerError::Unreachable("LoginUnavailable"));
    fail(&world, BEA, ServerError::Unreachable("LoginRefused"));
    let syncing = syncing(&world);
    syncing.sync_guarded(None);
    assert_eq!(standing(&syncing, ADA), None);
    assert_eq!(standing(&syncing, BEA), error("LoginRefused"));
}

#[test]
fn a_wake_starts_trouble_that_may_pass_afresh_so_a_failure_from_before_the_sleep_never_shows_by_its_age() {
    let world = World::with(&[ADA, BEA]);
    fail(&world, BEA, ServerError::Unreachable("LoginRefused"));
    let syncing = syncing(&world);
    // The Wi-Fi went as the Mac went to sleep: the sync then failed once, and an hour passed asleep.
    syncing.sync_guarded(None);
    pass(&world, 3600);
    assert_eq!(standing(&syncing, ADA), error("ConnectionRefused"), "an hour on, it would show by its age");
    syncing.woke();
    assert_eq!(standing(&syncing, ADA), None);
    // The sync on waking fails while the network comes back: the first of a new run, so still nothing shows.
    let recorded = world.recorded.load(Ordering::SeqCst);
    syncing.sync_guarded(None);
    assert_eq!(standing(&syncing, ADA), None);
    assert_eq!(world.recorded.load(Ordering::SeqCst), recorded + 1, "only the refused login's record keeps an error");
    // A run that lasts shows, as before the sleep.
    syncing.sync_guarded(None);
    assert_eq!(standing(&syncing, ADA), error("ConnectionRefused"));
    // A refused password showed at once, and a wake doesn't hide it.
    syncing.woke();
    assert_eq!(standing(&syncing, BEA), error("LoginRefused"));
}

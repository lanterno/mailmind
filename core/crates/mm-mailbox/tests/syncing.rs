//! `syncing`: each account's INBOX synced through its own flight, and how each account's sync stands. A world of
//! stand-ins: a server that never answers as it was asked to, a store that keeps nothing, and a clock the test moves.
//! The served suites hold what a sync stores; these hold what the flight, an account's standing and a round of
//! older mail do when a sync goes wrong, or its account is removed. Every address is fictional.

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
use mm_mailbox::flights::Flights;
use mm_mailbox::folders::Folders;
use mm_mailbox::ingest::{Ingest, SyncError};
use mm_mailbox::ports::{Accounts, MailSession, MailSessions, SessionError, StoredMail, ViewCounts};
use mm_mailbox::session::{MailServer, MailServers, ServerError};
use mm_mailbox::sync_plan::Scan;
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
    /// Each account and folder whose first scan has older mail to read.
    older: Mutex<Vec<(String, String)>>,
    /// The clock, in microseconds.
    now: AtomicI64,
    /// Each account's syncs now, which a removal stops.
    flights: Arc<Flights>,
    /// The accounts removed as their server is opened: their flights are stopped then.
    removing: Mutex<HashSet<String>>,
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
        if self.removing.lock().unwrap().contains(account) {
            self.flights.stop(account);
        }
        Err(self.fails.lock().unwrap().get(account).copied().unwrap_or(ServerError::Unreachable("ConnectionRefused")))
    }
}

/// Nothing is stored: every sync here fails before it reads a message.
impl SyncStore for World {
    /// A folder whose first scan read down to UID 100.
    fn folder_state(&self, _: &str, _: &str) -> Result<FolderState, Fault> {
        Ok(FolderState { scan: Scan::Down(100), ..FolderState::default() })
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
        Ok(self.older.lock().unwrap().iter().map(|(account, folder)| (account.clone(), folder.clone(), 100)).collect())
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
    let ingest = Ingest::new(world.clone(), world.clone(), world.clone(), world.clone(), world.clone(), world.clone())
        .flying_in(world.flights.clone());
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

/// The log's lines, as level, tag, message and class: the process's sink, so every test here writes to it.
#[derive(Debug)]
struct Kept(Arc<Mutex<Vec<String>>>);

impl mm_kernel::log::Sink for Kept {
    fn write(&self, line: &mm_kernel::log::Line<'_>) {
        let said = format!("{} [{}] {} ({:?})", line.level.name(), line.tag, line.message, line.error);
        self.0.lock().unwrap_or_else(std::sync::PoisonError::into_inner).push(said);
    }
}

#[test]
fn a_failure_held_back_still_logs_its_class() {
    let kept = Arc::new(Mutex::new(Vec::new()));
    mm_kernel::log::install(Box::new(Kept(kept.clone())));
    let world = World::with(&[ADA]);
    // A class no other test here fails by, as every test logs to the one sink.
    fail(&world, ADA, ServerError::Unreachable("ConnectTimeout"));
    syncing(&world).sync_guarded(None);
    let said = r#"INFO [sync] an account's sync failed; it shows if the next fails too (Some("ConnectTimeout"))"#;
    assert!(kept.lock().unwrap().iter().any(|line| line == said), "{:?}", kept.lock().unwrap());
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

#[test]
fn an_account_whose_sync_panics_on_its_own_thread_leaves_the_others_syncing_and_every_flight_lands() {
    let world = World::with(&[PANICS, ADA]);
    let syncing = syncing(&world);
    syncing.sync_guarded(None);
    assert_eq!(standing(&syncing, PANICS), error("Panic"));
    assert_eq!(world.opened.load(Ordering::SeqCst), 2, "the other account's sync went too");
    assert!(!syncing.sync().unwrap().running);
    // Both flights landed: the next sync runs each again.
    syncing.sync_guarded(None);
    assert_eq!(world.opened.load(Ordering::SeqCst), 4);
}

#[test]
fn a_round_that_panics_before_its_accounts_sync_lands_their_flights_so_the_next_sync_runs_them() {
    let world = World::with(&[ADA, BEA]);
    world.panic_waking.store(1, Ordering::SeqCst);
    let syncing = syncing(&world);
    syncing.sync_guarded(None);
    assert_eq!(world.opened.load(Ordering::SeqCst), 0, "the round ended before its accounts' syncs");
    assert!(!syncing.sync().unwrap().running);
    // Neither account is left taken by a flight that never flew.
    syncing.sync_guarded(Some(BTreeSet::from([ADA.to_owned()])));
    assert_eq!(world.opened.load(Ordering::SeqCst), 1);
    syncing.sync_guarded(None);
    assert_eq!(world.opened.load(Ordering::SeqCst), 3);
}

#[test]
fn a_read_of_older_mail_that_panics_ends_only_its_folders_read_and_the_next_round_asks_again() {
    let world = World::with(&[PANICS, ADA]);
    let older = [(PANICS, "INBOX"), (PANICS, "Archive"), (ADA, "INBOX")];
    *world.older.lock().unwrap() = older.iter().map(|(a, f)| ((*a).to_owned(), (*f).to_owned())).collect();
    let syncing = syncing(&world);
    assert!(!syncing.read_older().unwrap());
    // Each folder's read went to its server: the Inbox's that panicked ended only its own.
    assert_eq!(world.opened.load(Ordering::SeqCst), 3);
    assert!(!syncing.read_older().unwrap());
    assert_eq!(world.opened.load(Ordering::SeqCst), 6);
}

fn removing(world: &World, account: &str) {
    world.removing.lock().unwrap().insert(account.to_owned());
}

#[test]
fn a_removed_accounts_run_of_failures_goes_with_it_so_added_again_it_starts_afresh() {
    let world = World::with(&[ADA]);
    let syncing = Arc::new(syncing(&world));
    syncing.sync_guarded(None);
    pass(&world, 3600);
    assert_eq!(standing(&syncing, ADA), error("ConnectionRefused"), "it shows by its age");
    syncing.removed(ADA);
    assert_eq!(standing(&syncing, ADA), None);
    // Added again, its first failure is the first of a new run: nothing shows, an hour on or not.
    syncing.sync_guarded(None);
    assert_eq!(standing(&syncing, ADA), None);
}

#[test]
fn a_sync_whose_account_is_removed_as_it_reads_says_and_keeps_nothing_of_it() {
    let world = World::with(&[ADA, BEA]);
    let syncing = syncing(&world);
    // A failure that may pass, which a run would show by its age.
    syncing.sync_guarded(None);
    let recorded = world.recorded.load(Ordering::SeqCst);
    removing(&world, ADA);
    syncing.sync_guarded(None);
    pass(&world, 3600);
    assert_eq!(standing(&syncing, ADA), None, "neither the sync's failure nor the run before it");
    assert_eq!(standing(&syncing, BEA), error("ConnectionRefused"), "the other account's run goes on");
    assert_eq!(world.recorded.load(Ordering::SeqCst), recorded + 1, "only the other account's failure is kept");
    assert!(!syncing.sync().unwrap().running);
}

#[test]
fn a_read_of_older_mail_whose_account_is_removed_stops_and_says_nothing_more_of_it() {
    let world = World::with(&[ADA, BEA]);
    let older = [(ADA, "INBOX"), (ADA, "Archive"), (BEA, "INBOX")];
    *world.older.lock().unwrap() = older.iter().map(|(a, f)| ((*a).to_owned(), (*f).to_owned())).collect();
    removing(&world, ADA);
    let syncing = syncing(&world);
    assert!(!syncing.read_older().unwrap());
    // Ada's Inbox's read was the last of hers; Bea's went on.
    assert_eq!(world.opened.load(Ordering::SeqCst), 2);
    assert_eq!(standing(&syncing, ADA), None);
}

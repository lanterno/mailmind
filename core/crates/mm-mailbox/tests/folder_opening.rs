//! A folder's sync as it opens (`folders.on_open`, `scanning_first`), as the Python app's
//! tests/characterization/mailbox/test_opening_a_folder.py has it: a first scan waited for a moment, which says so when
//! it fails or runs long; a catch-up of a folder read before, at most once a minute unless MailMind put mail there; an
//! open while one runs joins it; and every sync's end told on the event stream (`folder-synced`).

#![allow(clippy::unwrap_used)]

use std::collections::HashSet;
use std::sync::atomic::{AtomicI64, Ordering};
use std::sync::{Arc, Condvar, Mutex, MutexGuard};
use std::time::Duration;

use mm_kernel::errors::Fault;
use mm_kernel::events::Topic;
use mm_kernel::seams::{Clock, Moment};
use mm_mailbox::folders::Folders;
use mm_mailbox::gates::{Gates, Priority, Refused, Wait};
use mm_mailbox::ingest::SyncError;
use mm_mailbox::opening::{FolderOpening, OpeningParts, Waits};
use mm_mailbox::opening_ports::{Background, FolderSync};
use mm_mailbox::ports::{
    Accounts, FolderSyncs, MailSession, MailSessions, Opened, Page, Row, SessionError, StoredLists, StoredMail,
    ViewCounts,
};
use mm_mailbox::session::ServerError;
use mm_mailbox::sync_ports::{Events, MailboxEvent};
use serde_json::{Value, json};

const ADA: &str = "ada@example.test";

#[derive(Debug, Default)]
struct Ticking(AtomicI64);

impl Ticking {
    fn pass(&self, seconds: i64) {
        self.0.fetch_add(seconds * 1_000_000, Ordering::SeqCst);
    }
}

impl Clock for Ticking {
    fn now(&self) -> Moment {
        Moment::from_micros(self.0.load(Ordering::SeqCst))
    }
}

/// Which folders a scan has read whole: a first scan that works reads its folder whole.
#[derive(Debug, Default)]
struct Scanned(Mutex<HashSet<String>>);

impl StoredLists for Scanned {
    fn folder_page(&self, _: &str, _: &str, _: bool, _: i64, _: i64) -> Result<Page, Fault> {
        Ok(Page::default())
    }

    fn inboxes_page(&self, _: &[String], _: bool, _: i64, _: i64) -> Result<Page, Fault> {
        Ok(Page::default())
    }

    fn snoozed_page(&self, _: &[String], _: i64, _: i64) -> Result<Page, Fault> {
        Ok(Page::default())
    }

    fn muted(&self, _: &[String]) -> Result<Vec<Row>, Fault> {
        Ok(Vec::new())
    }

    fn waiting(&self, _: &[String]) -> Result<Vec<Row>, Fault> {
        Ok(Vec::new())
    }

    fn reply_later(&self, _: &[String]) -> Result<Vec<Row>, Fault> {
        Ok(Vec::new())
    }

    fn thread(&self, _: &str, _: &str) -> Result<Vec<Row>, Fault> {
        Ok(Vec::new())
    }

    fn scanned_whole(&self, _account: &str, folder: &str) -> Result<bool, Fault> {
        Ok(self.0.lock().unwrap().contains(folder))
    }
}

/// A sync that writes down each folder it was asked for and whether whole, finds `new` messages, fails while `fails`
/// says, and waits while the test holds `hold`.
#[derive(Debug, Default)]
struct Syncs {
    scanned: Arc<Scanned>,
    asked: Mutex<Vec<(String, bool)>>,
    fails: Mutex<bool>,
    new: Mutex<usize>,
    hold: Mutex<()>,
}

impl FolderSync for Syncs {
    fn sync_folder(&self, _account: &str, folder: &str, full: bool) -> Result<(usize, usize), SyncError> {
        let _held = self.hold.lock().unwrap();
        self.asked.lock().unwrap().push((folder.to_owned(), full));
        if *self.fails.lock().unwrap() {
            return Err(SyncError::Server(ServerError::Refused("error")));
        }
        if full {
            self.scanned.0.lock().unwrap().insert(folder.to_owned());
        }
        Ok((*self.new.lock().unwrap(), 0))
    }
}

/// Gates that write down each job's priority and wait.
#[derive(Debug, Default)]
struct Watched(Mutex<Vec<(Priority, Wait)>>);

impl Gates for Watched {
    fn run(&self, _account: &str, priority: Priority, wait: Wait, job: &mut dyn FnMut()) -> Result<(), Refused> {
        self.0.lock().unwrap().push((priority, wait));
        job();
        Ok(())
    }

    fn held(&self, _account: &str) -> bool {
        false
    }
}

/// What the open tabs were told, which a test can wait for.
#[derive(Debug, Default)]
struct Told {
    said: Mutex<Vec<(Topic, Value)>>,
    came: Condvar,
}

impl Told {
    /// The `n`th thing told, waiting for it at most a few seconds.
    fn nth(&self, n: usize) -> (Topic, Value) {
        let said = self.said.lock().unwrap();
        let (said, _) = self.came.wait_timeout_while(said, Duration::from_secs(5), |s| s.len() <= n).unwrap();
        said[n].clone()
    }

    fn count(&self) -> usize {
        self.said.lock().unwrap().len()
    }
}

impl Events for Told {
    fn publish(&self, event: MailboxEvent) {
        if let MailboxEvent::Page { topic, data } = event {
            self.said.lock().unwrap().push((topic, data));
            self.came.notify_all();
        }
    }
}

#[derive(Debug)]
struct Threads;

impl Background for Threads {
    fn spawn(&self, job: Box<dyn FnOnce() + Send>) {
        std::thread::spawn(job);
    }
}

#[derive(Debug)]
struct Ada;

impl Accounts for Ada {
    fn known(&self, account: &str) -> Result<bool, Fault> {
        Ok(account == ADA)
    }
}

impl MailSessions for Ada {
    fn open(&self, _account: &str) -> Result<Box<dyn MailSession>, SessionError> {
        Err(SessionError::Unreachable("Timeout"))
    }
}

impl StoredMail for Ada {
    fn stored_folder_counts(&self, _account: &str) -> Result<Vec<(String, Option<i64>, i64)>, Fault> {
        Ok(Vec::new())
    }

    fn view_counts(&self, _accounts: &[String]) -> Result<ViewCounts, Fault> {
        Ok(ViewCounts::default())
    }
}

struct World {
    syncs: Arc<Syncs>,
    gates: Arc<Watched>,
    told: Arc<Told>,
    clock: Arc<Ticking>,
    folders: Arc<Folders>,
    opening: FolderOpening,
}

impl World {
    /// Ada's account, whose `scanned` folders a scan has read whole.
    fn new(scanned: &[&str]) -> World {
        let stored = Arc::new(Scanned(Mutex::new(scanned.iter().map(|f| (*f).to_owned()).collect())));
        let syncs = Arc::new(Syncs { scanned: stored.clone(), ..Syncs::default() });
        let gates = Arc::new(Watched::default());
        let told = Arc::new(Told::default());
        let clock = Arc::new(Ticking::default());
        let folders = Arc::new(Folders::new(Arc::new(Ada), Arc::new(Ada), Arc::new(Ada), clock.clone()));
        let waits =
            Waits { first_scan: Duration::from_secs(5), catch_up: Duration::from_secs(5), catch_up_every: 60.0 };
        let opening = FolderOpening::new(OpeningParts {
            stored,
            sync: syncs.clone(),
            gates: gates.clone(),
            events: told.clone(),
            folders: folders.clone(),
            background: Arc::new(Threads),
            waits,
        });
        World { syncs, gates, told, clock, folders, opening }
    }

    /// The same, its waits as short as a server slower than them needs.
    fn impatient(scanned: &[&str]) -> World {
        let world = World::new(scanned);
        let waits =
            Waits { first_scan: Duration::from_millis(50), catch_up: Duration::from_millis(50), catch_up_every: 60.0 };
        let opening = FolderOpening::new(OpeningParts {
            stored: world.syncs.scanned.clone(),
            sync: world.syncs.clone(),
            gates: world.gates.clone(),
            events: world.told.clone(),
            folders: world.folders.clone(),
            background: Arc::new(Threads),
            waits,
        });
        World { opening, ..world }
    }

    fn open(&self, folder: &str) -> Opened {
        self.opening.on_open(ADA, folder).unwrap()
    }

    fn asked(&self) -> Vec<(String, bool)> {
        std::mem::take(&mut *self.syncs.asked.lock().unwrap())
    }

    fn held(&self) -> MutexGuard<'_, ()> {
        self.syncs.hold.lock().unwrap()
    }
}

fn synced(folder: &str, ok: bool, new: usize) -> (Topic, Value) {
    (Topic::FolderSynced, json!({"account": ADA, "folder": folder, "ok": ok, "new": new}))
}

#[test]
fn a_folder_never_read_is_scanned_whole_as_it_opens_and_its_end_is_told() {
    let world = World::new(&[]);
    *world.syncs.new.lock().unwrap() = 1;
    assert_eq!(world.open("Junk"), Opened::Done);
    assert_eq!(world.asked(), [("Junk".to_owned(), true)]);
    assert_eq!(*world.gates.0.lock().unwrap(), [(Priority::Interactive, Wait::Ever)]);
    assert_eq!(world.told.nth(0), synced("Junk", true, 1));
}

#[test]
fn a_folder_whose_first_scan_failed_says_so_and_is_scanned_on_the_next_open() {
    let world = World::new(&[]);
    *world.syncs.fails.lock().unwrap() = true;
    assert_eq!(world.open("Spam"), Opened::SyncFailed);
    assert_eq!(world.told.nth(0), synced("Spam", false, 0));
    *world.syncs.fails.lock().unwrap() = false;
    assert_eq!(world.open("Spam"), Opened::Done);
    assert_eq!(world.asked(), [("Spam".to_owned(), true), ("Spam".to_owned(), true)]);
    assert_eq!(world.told.nth(1), synced("Spam", true, 0));
}

#[test]
fn a_first_open_slower_than_the_wait_answers_and_a_second_joins_its_scan() {
    let world = World::impatient(&[]);
    {
        let _held = world.held();
        assert_eq!(world.open("Junk"), Opened::Syncing);
        assert_eq!(world.open("Junk"), Opened::Syncing);
    }
    assert_eq!(world.told.nth(0), synced("Junk", true, 0));
    assert_eq!(world.open("Junk"), Opened::Done);
    assert_eq!(world.asked(), [("Junk".to_owned(), true)]);
}

#[test]
fn a_folder_read_before_is_caught_up_once_however_often_it_opens_soon() {
    let world = World::new(&["Archive"]);
    for _ in 0..3 {
        assert_eq!(world.open("Archive"), Opened::Done);
    }
    assert_eq!(world.asked(), [("Archive".to_owned(), false)]);
    assert_eq!(*world.gates.0.lock().unwrap(), [(Priority::AfterUser, Wait::Ever)]);
    assert_eq!(world.told.nth(0), synced("Archive", true, 0));
}

#[test]
fn a_folder_is_caught_up_again_a_minute_on() {
    let world = World::new(&["Archive"]);
    world.open("Archive");
    world.clock.pass(59);
    world.open("Archive");
    world.clock.pass(1);
    world.open("Archive");
    assert_eq!(world.asked(), [("Archive".to_owned(), false), ("Archive".to_owned(), false)]);
}

#[test]
fn mail_put_in_a_folder_lets_its_next_open_catch_up_at_once() {
    let world = World::new(&["Drafts"]);
    world.open("Drafts");
    world.folders.touched(ADA, "Drafts").unwrap();
    world.open("Drafts");
    assert_eq!(world.asked(), [("Drafts".to_owned(), false), ("Drafts".to_owned(), false)]);
}

#[test]
fn the_inbox_is_left_to_the_background_sync() {
    let world = World::new(&["INBOX"]);
    assert_eq!(world.open("INBOX"), Opened::Done);
    assert!(world.asked().is_empty());
    assert_eq!(world.told.count(), 0);
}

#[test]
fn a_slow_catch_up_answers_with_what_is_stored_and_an_open_meanwhile_says_so_too() {
    let world = World::impatient(&["Archive"]);
    {
        let _held = world.held();
        assert_eq!(world.open("Archive"), Opened::CatchingUp);
        assert_eq!(world.open("Archive"), Opened::CatchingUp);
    }
    assert_eq!(world.told.nth(0), synced("Archive", true, 0));
    assert_eq!(world.open("Archive"), Opened::Done);
    assert_eq!(world.asked(), [("Archive".to_owned(), false)]);
}

#[test]
fn a_list_across_accounts_starts_a_first_scan_and_waits_for_none() {
    let world = World::new(&["Archive"]);
    assert!(!world.opening.scanning_first(ADA, "Archive").unwrap());
    let held = world.held();
    assert!(world.opening.scanning_first(ADA, "INBOX").unwrap());
    drop(held);
    assert_eq!(world.told.nth(0), synced("INBOX", true, 0));
    assert!(!world.opening.scanning_first(ADA, "INBOX").unwrap());
    assert_eq!(world.asked(), [("INBOX".to_owned(), true)]);
}

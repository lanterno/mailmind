//! A folder's sync as it opens (`mailbox.application.folders`' `on_open`, `scanning_first` and `_folder_sync`): the
//! first scan of a folder no scan has begun, or a catch-up of one read before when one is due, each a job on the
//! account's gate, on a thread of its own. The open waits for it a moment, and answers with what is stored if it
//! hasn't ended by then (`syncing`, `catching_up`). However it ends, it says so on the event stream (`folder-synced`,
//! with whether it read the folder and how many messages it stored), so a list that answered before it ended can load
//! again; mail it stored lets the rail's counts go.
//!
//! Only INBOX is synced in the background, so another folder is caught up as it opens, at most once a minute, unless
//! MailMind put mail there since (`Folders::touched`). An open while a sync of that folder runs joins it, rather than
//! start another. An Inbox no scan has begun joins the background's sync of it (`InboxSync`), which takes no gate,
//! so a message opens while it reads.
//!
//! A first scan stores the folder's newest mail and leaves the rest to the background (`Syncing::read_older`), whose
//! round it sets going at once (`OlderMail`), so a folder whose first scan has begun is caught up as one read before.

use std::collections::{HashMap, HashSet};
use std::sync::{Arc, Condvar, Mutex, PoisonError};
use std::time::Duration;

use mm_kernel::errors::{Fault, caught};
use mm_kernel::events::Topic;
use mm_kernel::log;
use serde_json::json;

use crate::folders::Folders;
use crate::gates::{Gates, Priority, Wait, on_gate};
use crate::ingest::SyncError;
use crate::opening_ports::{Background, FolderSync, InboxSync, OlderMail};
use crate::ports::{FolderSyncs, Opened, StoredLists};
use crate::sync_plan;
use crate::sync_ports::{Events, MailboxEvent};

const TAG: &str = "folder-sync";
const POISONED: Fault = Fault::new("PoisonedLock");

/// How long an open waits for a sync it set going, and how often a folder is caught up.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Waits {
    /// For a folder's first scan (`FIRST_SYNC_WAIT`).
    pub first_scan: Duration,
    /// For a catch-up of a folder read before (`CATCH_UP_WAIT`).
    pub catch_up: Duration,
    /// Seconds between catch-ups of one folder as it opens (`CATCH_UP_EVERY`).
    pub catch_up_every: f64,
}

impl Default for Waits {
    fn default() -> Waits {
        Waits { first_scan: Duration::from_secs(8), catch_up: Duration::from_millis(1500), catch_up_every: 60.0 }
    }
}

/// Set once a sync ends (`threading.Event`).
#[derive(Debug, Default)]
struct Done {
    ended: Mutex<bool>,
    woken: Condvar,
}

impl Done {
    fn set(&self) {
        *self.ended.lock().unwrap_or_else(PoisonError::into_inner) = true;
        self.woken.notify_all();
    }

    /// Whether it ended within `timeout`.
    fn wait(&self, timeout: Duration) -> bool {
        let ended = self.ended.lock().unwrap_or_else(PoisonError::into_inner);
        let (ended, _) =
            self.woken.wait_timeout_while(ended, timeout, |ended| !*ended).unwrap_or_else(PoisonError::into_inner);
        *ended
    }
}

type Key = (String, String);

#[derive(Debug, Default)]
struct Running {
    /// The syncs running now, each set when it ends.
    syncs: HashMap<Key, Arc<Done>>,
    /// The folders whose last first scan failed, while it did.
    first_failed: HashSet<Key>,
}

#[derive(Debug)]
struct Inner {
    stored: Arc<dyn StoredLists>,
    sync: Arc<dyn FolderSync>,
    gates: Arc<dyn Gates>,
    events: Arc<dyn Events>,
    folders: Arc<Folders>,
    background: Arc<dyn Background>,
    waits: Waits,
    inbox: Arc<dyn InboxSync>,
    older: Arc<dyn OlderMail>,
    running: Mutex<Running>,
}

/// A folder's sync as it opens.
#[derive(Debug)]
pub struct FolderOpening(Arc<Inner>);

/// What wires FolderOpening.
#[derive(Debug)]
pub struct OpeningParts {
    /// What is stored of each folder's scans.
    pub stored: Arc<dyn StoredLists>,
    pub sync: Arc<dyn FolderSync>,
    pub gates: Arc<dyn Gates>,
    /// Who hears that a sync ended.
    pub events: Arc<dyn Events>,
    /// The rail: when each folder was last caught up, and its counts, which mail stored lets go.
    pub folders: Arc<Folders>,
    pub background: Arc<dyn Background>,
    pub waits: Waits,
    /// The background's sync of each Inbox, which a first look at one joins.
    pub inbox: Arc<dyn InboxSync>,
    /// The background's read of older mail, which a first scan that leaves some sets going.
    pub older: Arc<dyn OlderMail>,
}

impl FolderOpening {
    pub fn new(parts: OpeningParts) -> FolderOpening {
        let OpeningParts { stored, sync, gates, events, folders, background, waits, inbox, older } = parts;
        let running = Mutex::new(Running::default());
        let inner = Inner { stored, sync, gates, events, folders, background, waits, inbox, older, running };
        FolderOpening(Arc::new(inner))
    }

    /// Whether opening a folder read before should catch it up now.
    fn catch_up_due(&self, account: &str, folder: &str) -> Result<bool, Fault> {
        let key = (account.to_owned(), folder.to_owned());
        let running = self.0.running.lock().map_err(|_| POISONED)?.syncs.contains_key(&key);
        let since = self.0.folders.since_caught_up(account, folder)?;
        Ok(sync_plan::catch_up_due(folder, running, since, self.0.waits.catch_up_every))
    }

    /// Whether the last first scan of the folder failed.
    fn first_failed(&self, account: &str, folder: &str) -> Result<bool, Fault> {
        let key = (account.to_owned(), folder.to_owned());
        Ok(self.0.running.lock().map_err(|_| POISONED)?.first_failed.contains(&key))
    }
}

impl Inner {
    /// Sync a folder as it opens, as a job on the account's gate, unless a sync of it runs already: its first scan
    /// (`full`) or a catch-up. What is set when it ends.
    fn folder_sync(self: &Arc<Inner>, account: &str, folder: &str, full: bool) -> Result<Arc<Done>, Fault> {
        let key = (account.to_owned(), folder.to_owned());
        let done = {
            let mut running = self.running.lock().map_err(|_| POISONED)?;
            if let Some(done) = running.syncs.get(&key) {
                return Ok(done.clone());
            }
            let done = Arc::new(Done::default());
            running.syncs.insert(key.clone(), done.clone());
            done
        };
        self.folders.catching_up(account, folder)?;
        let inner = self.clone();
        let ended = done.clone();
        self.background.spawn(Box::new(move || inner.run(key, full, &ended)));
        Ok(done)
    }

    /// The sync itself, on its own thread: then what it ended in, told. The Inbox's is the background's, joined. One
    /// that panics, as a malformed answer could make it, ends as one that failed: else the folder's opens would join a
    /// sync that never ends, and wait on it each time.
    fn run(&self, key: Key, full: bool, done: &Done) {
        let (account, folder) = (key.0.as_str(), key.1.as_str());
        let new = caught(|| self.sync(account, folder, full)).unwrap_or_else(|fault| {
            log::warning(TAG, "a folder's sync as it opened failed", &[], Some(fault.class));
            None
        });
        {
            let mut running = self.running.lock().unwrap_or_else(PoisonError::into_inner);
            match new {
                Some(_) => _ = running.first_failed.remove(&key),
                None if full => _ = running.first_failed.insert(key.clone()),
                None => {}
            }
            running.syncs.remove(&key);
        }
        done.set();
        let (ok, new) = (new.is_some(), new.unwrap_or(0));
        if new > 0
            && let Err(fault) = self.folders.invalidate(Some(account))
        {
            log::warning(TAG, "the rail's counts kept couldn't go", &[], Some(fault.class));
        }
        let data = json!({"account": account, "folder": folder, "ok": ok, "new": new});
        self.events.publish(MailboxEvent::Page { topic: Topic::FolderSynced, data });
    }

    /// How many messages a sync as the folder opens stored, or None where it failed.
    fn sync(&self, account: &str, folder: &str, full: bool) -> Option<usize> {
        if sync_plan::reads_bodies(folder) {
            // What it stored, the background's sync tells, and lets the rail's counts go for; it logs a failure.
            return self.inbox.sync_inbox(account).then_some(0);
        }
        let priority = if full { Priority::Interactive } else { Priority::AfterUser };
        match on_gate(&*self.gates, account, priority, Wait::Ever, || self.sync.sync_folder(account, folder, full)) {
            Ok(Ok(synced)) => {
                if synced.reading.is_some() {
                    // Its older mail is read on now, not at the background's next round.
                    self.older.left_some();
                }
                Some(synced.kept + synced.junk)
            }
            // Another client deleted or renamed it: forgotten, its list is empty and the rail leaves it out.
            Ok(Err(SyncError::Server(error))) if error.is_gone() => {
                if let Err(fault) = self.folders.gone(account, folder) {
                    log::warning(TAG, "a folder the server no longer has wasn't forgotten", &[], Some(fault.class));
                }
                None
            }
            Ok(Err(error)) => {
                log::warning(TAG, "a folder's sync as it opened failed", &[], Some(error.class()));
                None
            }
            Err(_) => {
                log::warning(TAG, "a folder's sync as it opened didn't get its gate", &[], None);
                None
            }
        }
    }
}

impl FolderSyncs for FolderOpening {
    fn on_open(&self, account: &str, folder: &str) -> Result<Opened, Fault> {
        if !self.0.stored.scan_begun(account, folder)? {
            if !self.0.folder_sync(account, folder, true)?.wait(self.0.waits.first_scan) {
                // Still reading: what is stored now, then folder-synced.
                return Ok(Opened::Syncing);
            }
            if self.first_failed(account, folder)? {
                return Ok(Opened::SyncFailed);
            }
        } else if self.catch_up_due(account, folder)?
            && !self.0.folder_sync(account, folder, false)?.wait(self.0.waits.catch_up)
        {
            // What is stored now; folder-synced says if more came.
            return Ok(Opened::CatchingUp);
        }
        Ok(Opened::Done)
    }

    fn scanning_first(&self, account: &str, folder: &str) -> Result<bool, Fault> {
        if self.0.stored.scan_begun(account, folder)? {
            return Ok(false);
        }
        Ok(!self.0.folder_sync(account, folder, true)?.wait(Duration::ZERO))
    }
}

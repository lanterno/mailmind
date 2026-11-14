//! A folder's sync as it opens (`mailbox.application.folders`' `on_open`, `scanning_first` and `_folder_sync`): the
//! first scan of a folder no scan has read whole, or a catch-up of one read before when one is due, each a job on the
//! account's gate, on a thread of its own. The open waits for it a moment, and answers with what is stored if it
//! hasn't ended by then (`syncing`, `catching_up`). However it ends, it says so on the event stream (`folder-synced`,
//! with whether it read the folder and how many messages it stored), so a list that answered before it ended can load
//! again; mail it stored lets the rail's counts go.
//!
//! Only INBOX is synced in the background, so another folder is caught up as it opens, at most once a minute, unless
//! MailMind put mail there since (`Folders::touched`). An open while a sync of that folder runs joins it, rather than
//! start another.

use std::collections::{HashMap, HashSet};
use std::sync::{Arc, Condvar, Mutex, PoisonError};
use std::time::Duration;

use mm_kernel::errors::Fault;
use mm_kernel::events::Topic;
use mm_kernel::log;
use serde_json::json;

use crate::folders::Folders;
use crate::gates::{Gates, Priority, Wait, on_gate};
use crate::opening_ports::{Background, FolderSync};
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
}

impl FolderOpening {
    pub fn new(parts: OpeningParts) -> FolderOpening {
        let OpeningParts { stored, sync, gates, events, folders, background, waits } = parts;
        let running = Mutex::new(Running::default());
        FolderOpening(Arc::new(Inner { stored, sync, gates, events, folders, background, waits, running }))
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

    /// The sync itself, on its own thread: then what it ended in, told.
    fn run(&self, key: Key, full: bool, done: &Done) {
        let (account, folder) = (key.0.as_str(), key.1.as_str());
        let priority = if full { Priority::Interactive } else { Priority::AfterUser };
        let synced =
            on_gate(&*self.gates, account, priority, Wait::Ever, || self.sync.sync_folder(account, folder, full));
        let new = match synced {
            Ok(Ok((kept, junk))) => Some(kept + junk),
            Ok(Err(error)) => {
                log::warning(TAG, "a folder's sync as it opened failed", &[], Some(error.class()));
                None
            }
            Err(_) => {
                log::warning(TAG, "a folder's sync as it opened didn't get its gate", &[], None);
                None
            }
        };
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
}

impl FolderSyncs for FolderOpening {
    fn on_open(&self, account: &str, folder: &str) -> Result<Opened, Fault> {
        if !self.0.stored.scanned_whole(account, folder)? {
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
        if self.0.stored.scanned_whole(account, folder)? {
            return Ok(false);
        }
        Ok(!self.0.folder_sync(account, folder, true)?.wait(Duration::ZERO))
    }
}

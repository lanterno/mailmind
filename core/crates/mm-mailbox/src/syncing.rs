//! The sync of each account's INBOX, one at a time, with what each account's last try came to
//! (`mailbox.application.syncing`): what `/api/refresh` and `/api/sync-retry-account` set going.
//!
//! A sync asked for while one runs isn't dropped: the running one goes round again as it ends, once for all such
//! asks, so mail heard of in the meantime is fetched now rather than at the next round.
//!
//! Each round ends by warming folders beyond the Inbox, for the accounts asking (`warm`), inside the single flight
//! and after the Inbox, so it never delays new mail.

use std::collections::{BTreeMap, BTreeSet};
use std::sync::{Arc, Mutex};

use mm_kernel::errors::{Failure, Fault, Problem};
use mm_kernel::events::Topic;
use mm_kernel::log::{self, Field};
use mm_kernel::seams::Clock;
use mm_kernel::when;
use serde_json::json;

use crate::command_ports::{self, CommandStore, MoveAccounts};
use crate::folders::Folders;
use crate::ingest::{Ingest, SyncError};
use crate::session::ServerError;
use crate::sync_ports::{AroundSync, Events, MailboxEvent};
use crate::warm::Warming;

const TAG: &str = "sync";
const POISONED: Fault = Fault::new("PoisonedLock");

/// How one account's sync stands now: its health (ok, syncing or error) and what it last said.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct AccountSync {
    pub health: &'static str,
    pub msg: String,
}

/// How the sync as a whole stands now.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct SyncState {
    pub running: bool,
    /// When the last round ended.
    pub last: Option<String>,
    /// What it came to: `idle`, or `N new`.
    pub result: String,
}

#[derive(Debug, Default)]
struct Held {
    sync: SyncState,
    status: BTreeMap<String, AccountSync>,
    /// Syncs asked for while one ran: each its accounts, or None for all.
    asked: Vec<Option<BTreeSet<String>>>,
}

/// The coarse, scrubbed kind of a sync's failure, from its class alone (`accounts.domain.health.error_kind`).
pub fn error_kind(error: SyncError) -> &'static str {
    let name = error.class().to_lowercase();
    let has = |words: &[&str]| words.iter().any(|w| name.contains(w));
    if has(&["tlspinmismatch"]) {
        "TLS_PIN"
    } else if has(&["timeout"]) {
        "TIMEOUT"
    } else if has(&["auth", "login", "permission"]) {
        "AUTH"
    } else if has(&["uidvalidity"]) {
        "UIDVALIDITY"
    } else if has(&["gaierror", "connection", "socket", "ssl", "tls", "dns", "resolve", "abort"]) {
        "NETWORK"
    } else if matches!(error, SyncError::Server(ServerError::Refused(_) | ServerError::Unreachable(_))) {
        "IMAP"
    } else {
        "UNKNOWN"
    }
}

/// What the next round covers, of the accounts each ask named while one ran; None, every account, wins.
pub fn asked_again(asked: &[Option<BTreeSet<String>>]) -> Option<BTreeSet<String>> {
    if asked.iter().any(Option::is_none) {
        return None;
    }
    Some(asked.iter().flatten().flatten().cloned().collect())
}

#[derive(Debug)]
pub struct Syncing {
    ingest: Arc<Ingest>,
    accounts: Arc<dyn MoveAccounts>,
    store: Arc<dyn CommandStore>,
    folders: Arc<Folders>,
    events: Arc<dyn Events>,
    clock: Arc<dyn Clock>,
    around: Arc<dyn AroundSync>,
    warm: Arc<Warming>,
    held: Mutex<Held>,
}

/// What wires Syncing.
#[derive(Debug)]
pub struct SyncingParts {
    pub ingest: Arc<Ingest>,
    pub accounts: Arc<dyn MoveAccounts>,
    pub store: Arc<dyn CommandStore>,
    pub folders: Arc<Folders>,
    pub events: Arc<dyn Events>,
    pub clock: Arc<dyn Clock>,
    /// What a round asks of the other contexts: snoozes woken before it, new mail triaged after.
    pub around: Arc<dyn AroundSync>,
    /// Folders beyond the Inbox, warmed as a round ends.
    pub warm: Arc<Warming>,
}

impl Syncing {
    pub fn new(parts: SyncingParts) -> Syncing {
        let SyncingParts { ingest, accounts, store, folders, events, clock, around, warm } = parts;
        let held = Held { sync: SyncState { result: "idle".to_owned(), ..SyncState::default() }, ..Held::default() };
        Syncing { ingest, accounts, store, folders, events, clock, around, warm, held: Mutex::new(held) }
    }

    /// How each account's sync stands now.
    pub fn status(&self) -> Result<BTreeMap<String, AccountSync>, Fault> {
        Ok(self.held.lock().map_err(|_| POISONED)?.status.clone())
    }

    /// How the sync as a whole stands now.
    pub fn sync(&self) -> Result<SyncState, Fault> {
        Ok(self.held.lock().map_err(|_| POISONED)?.sync.clone())
    }

    fn set_status(&self, account: &str, health: &'static str, msg: String) -> Result<(), Fault> {
        self.held.lock().map_err(|_| POISONED)?.status.insert(account.to_owned(), AccountSync { health, msg });
        Ok(())
    }

    fn page(&self, topic: Topic, data: serde_json::Value) {
        self.events.publish(MailboxEvent::Page { topic, data });
    }

    /// Sync the INBOX of each account named, or every account's: how many new messages it kept. The caller holds
    /// the single flight (`sync_guarded`).
    pub fn sync_accounts(&self, emails: Option<&BTreeSet<String>>) -> Result<usize, Fault> {
        let mut new = 0;
        for email in self.accounts.emails()? {
            if emails.is_some_and(|named| !named.contains(&email)) {
                continue;
            }
            self.set_status(&email, "syncing", String::new())?;
            match self.ingest.sync_folder(&email, "INBOX", false, None) {
                Ok((kept, junk, _)) => {
                    new += kept;
                    self.set_status(&email, "ok", format!("+{kept}"))?;
                    let at = when::iso(self.clock.now());
                    command_ports::write(&*self.store, |tx| tx.record_sync_result(&email, None, &at))?;
                    self.page(Topic::SyncDone, json!({"account": email, "kept": kept, "junk": junk}));
                    if kept > 0 {
                        self.page(Topic::NewMail, json!({"account": email, "folder": "INBOX", "count": kept}));
                    }
                }
                Err(error) => {
                    self.set_status(&email, "error", error.class().to_owned())?;
                    let at = when::iso(self.clock.now());
                    let failed = Some((error.class(), error_kind(error)));
                    command_ports::write(&*self.store, |tx| tx.record_sync_result(&email, failed, &at))?;
                    log::warning(TAG, "an account's sync failed", &[], Some(error.class()));
                }
            }
        }
        Ok(new)
    }

    /// A sync of every account.
    pub fn sync_once(&self) {
        self.sync_guarded(None);
    }

    /// Single-flight sync of `emails`, or of every account: a sync asked for while one runs makes that one go round
    /// again as it ends.
    pub fn sync_guarded(&self, emails: Option<BTreeSet<String>>) {
        {
            let Ok(mut held) = self.held.lock() else { return };
            if held.sync.running {
                held.asked.push(emails);
                return;
            }
            held.sync.running = true;
        }
        let mut emails = emails;
        loop {
            if let Err(fault) = self.round(emails.as_ref()) {
                log::error(TAG, "a sync round failed", &[], Some(fault.class));
            }
            let Ok(mut held) = self.held.lock() else { return };
            if held.asked.is_empty() {
                held.sync.running = false;
                return;
            }
            emails = asked_again(&held.asked);
            held.asked.clear();
        }
    }

    /// One sync of `emails`, or of every account. What is snoozed and due wakes first; new mail sets its triage
    /// going, outside this single flight. Folders beyond the Inbox are warmed last.
    fn round(&self, emails: Option<&BTreeSet<String>>) -> Result<(), Fault> {
        self.around.wake_due();
        let new = self.sync_accounts(emails)?;
        if new > 0 {
            // New mail: the rail's unread badges now, not after their TTL.
            self.folders.invalidate(None)?;
            self.around.new_mail();
        }
        let mut held = self.held.lock().map_err(|_| POISONED)?;
        held.sync.result = format!("{new} new");
        held.sync.last = Some(when::iso(self.clock.now()));
        drop(held);
        log::info(TAG, "%(count)s new", &[(Field::Count, &new.to_string())]);
        if let Err(fault) = self.warm.tick(emails) {
            log::warning("warm", "tick error", &[], Some(fault.class));
        }
        Ok(())
    }

    /// Clear an account's recorded sync error, before its sync runs again (`do_sync_retry`): the account, or
    /// `unknown account`. The caller sets the sync going.
    pub fn retry(&self, account: Option<&str>) -> Result<String, Failure> {
        let account = mm_kernel::text::strip(account.unwrap_or_default());
        if !self.accounts.emails()?.iter().any(|e| e == account) {
            return Err(Problem::refused("unknown account").into());
        }
        command_ports::write(&*self.store, |tx| tx.clear_sync_error(account))?;
        Ok(account.to_owned())
    }
}

//! The sync of each account's INBOX, one at a time, with what each account's last try came to
//! (`mailbox.application.syncing`): what `/api/refresh` and `/api/sync-retry-account` set going.
//!
//! A sync asked for while one runs isn't dropped: the running one goes round again as it ends, once for all such
//! asks, so mail heard of in the meantime is fetched now rather than at the next round.
//!
//! Each round ends by warming folders beyond the Inbox, for the accounts asking (`warm`), inside the single flight
//! and after the Inbox, so it never delays new mail.
//!
//! A round, or an account's sync in it, that panics ends as one that failed (`Panic`): the account says so, the
//! flight comes down, and the next sync runs as it would have.
//!
//! An account shows as failing only as `sync_health` says: trouble that may pass (a lost connection, a timeout, a
//! server that can't take a login for now) once a second sync in a row has failed too, or three minutes on; until
//! then it shows as it did before, and its record keeps no error.

use std::collections::{BTreeMap, BTreeSet};
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};

use mm_kernel::errors::{Failure, Fault, Problem, caught};
use mm_kernel::events::Topic;
use mm_kernel::log::{self, Field};
use mm_kernel::seams::Clock;
use mm_kernel::when;
use serde_json::json;

use crate::command_ports::{self, CommandStore, MoveAccounts};
use crate::folders::Folders;
use crate::ingest::{Ingest, SyncError};
use crate::session::ServerError;
use crate::sync_health::{self, Failing};
use crate::sync_ports::{AroundSync, Events, MailboxEvent};
use crate::warm::Warming;

const TAG: &str = "sync";

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
    /// Each account whose syncs have failed in a row, until one works.
    failing: BTreeMap<String, Failing>,
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

    /// What is held, whole between statements: so a lock a panicking round let go is still sound, and the flight
    /// still comes down.
    fn held(&self) -> MutexGuard<'_, Held> {
        self.held.lock().unwrap_or_else(PoisonError::into_inner)
    }

    /// How each account's sync stands now: one whose failures in a row now show, three minutes on, as failing.
    pub fn status(&self) -> Result<BTreeMap<String, AccountSync>, Fault> {
        let now = self.seconds();
        let held = self.held();
        let mut status = held.status.clone();
        for (account, run) in &held.failing {
            let syncing = status.get(account).is_some_and(|now| now.health == "syncing");
            if run.shows(now) && !syncing {
                status.insert(account.clone(), AccountSync { health: "error", msg: run.class.to_owned() });
            }
        }
        Ok(status)
    }

    /// The clock's seconds now.
    fn seconds(&self) -> i64 {
        self.clock.now().micros().div_euclid(1_000_000)
    }

    /// How the sync as a whole stands now.
    pub fn sync(&self) -> Result<SyncState, Fault> {
        Ok(self.held().sync.clone())
    }

    fn set_status(&self, account: &str, health: &'static str, msg: String) -> Result<(), Fault> {
        self.held().status.insert(account.to_owned(), AccountSync { health, msg });
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
            let before = self.held().status.get(&email).cloned();
            self.set_status(&email, "syncing", String::new())?;
            // An account whose sync panics fails as `Panic`, and the next account's goes on.
            let synced = caught(|| self.ingest.sync_folder(&email, "INBOX", false, None));
            match synced.unwrap_or_else(|fault| Err(SyncError::Store(fault))) {
                Ok((kept, junk, _)) => {
                    new += kept;
                    self.held().failing.remove(&email);
                    self.set_status(&email, "ok", format!("+{kept}"))?;
                    let at = when::iso(self.clock.now());
                    command_ports::write(&*self.store, |tx| tx.record_sync_result(&email, None, &at))?;
                    self.page(Topic::SyncDone, json!({"account": email, "kept": kept, "junk": junk}));
                    if kept > 0 {
                        self.page(Topic::NewMail, json!({"account": email, "folder": "INBOX", "count": kept}));
                    }
                }
                Err(error) => {
                    let (class, kind, now) = (error.class(), error_kind(error), self.seconds());
                    let run = {
                        let mut held = self.held();
                        let run =
                            Failing::and(held.failing.get(&email), class, sync_health::may_pass(class, kind), now);
                        held.failing.insert(email.clone(), run.clone());
                        run
                    };
                    if !run.shows(now) {
                        // It may pass: the account shows as it did, and the next sync says.
                        let mut held = self.held();
                        match before {
                            Some(before) => held.status.insert(email.clone(), before),
                            None => held.status.remove(&email),
                        };
                        drop(held);
                        log::info(TAG, "an account's sync failed; it shows if the next fails too", &[]);
                        continue;
                    }
                    self.set_status(&email, "error", class.to_owned())?;
                    let at = when::iso(self.clock.now());
                    command_ports::write(&*self.store, |tx| tx.record_sync_result(&email, Some((class, kind)), &at))?;
                    log::warning(TAG, "an account's sync failed", &[], Some(class));
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
            let mut held = self.held();
            if held.sync.running {
                held.asked.push(emails);
                return;
            }
            held.sync.running = true;
        }
        let mut emails = emails;
        loop {
            if let Err(fault) = caught(|| self.round(emails.as_ref())).and_then(|round| round) {
                log::error(TAG, "a sync round failed", &[], Some(fault.class));
            }
            let mut held = self.held();
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
        let mut held = self.held();
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

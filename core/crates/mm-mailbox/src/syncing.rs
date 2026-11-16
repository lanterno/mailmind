//! The sync of each account's INBOX, with what each account's last try came to (`mailbox.application.syncing`): what
//! `/api/refresh`, `/api/sync-retry-account`, IDLE and the background's rounds set going.
//!
//! Each account syncs on its own. A sync of several runs each on a thread of its own and waits for them all, so one
//! slow server, or one account's first scan, holds up no other account. An account's sync is single flight: one
//! asked for while that account's runs isn't dropped, the running one goes round again as it ends, once for all such
//! asks, so mail heard of in the meantime is fetched now rather than at the next round.
//!
//! An Inbox's first scan stores its newest mail, which was there before, so it is no new mail: no `new-mail`, and no
//! count in the round's result. Its older mail is read after, a few steps at a time, by the background's
//! `read_older` rounds; until it is all read, the account's status says how far the scan has got.
//!
//! Each round ends by warming folders beyond the Inbox, for the accounts asking (`warm`), after their Inbox, so it
//! never delays new mail.
//!
//! A round, or an account's sync in it, that panics ends as one that failed (`Panic`): the account says so, its
//! flight lands, and the next sync runs as it would have. A folder's read of older mail that panics ends as one that
//! failed: the steps it stored stay, the account's other folders are read still, and the next round asks again.
//!
//! An account shows as failing only as `sync_health` says: trouble that may pass (a lost connection, a timeout, a
//! server that can't take a login for now) once a second sync in a row has failed too, or three minutes on; until
//! then it shows as it did before, and its record keeps no error. A wake, or a change of network, starts each such
//! run again (`woke`).

use std::collections::{BTreeMap, BTreeSet};
use std::sync::{Arc, Condvar, Mutex, MutexGuard, PoisonError};

use mm_kernel::errors::{Failure, Fault, Problem, caught};
use mm_kernel::events::Topic;
use mm_kernel::log::{self, Field};
use mm_kernel::seams::Clock;
use mm_kernel::when;
use serde_json::json;

use crate::command_ports::{self, CommandStore, MoveAccounts};
use crate::folders::Folders;
use crate::ingest::{Ingest, Progress, SyncError};
use crate::opening_ports::InboxSync;
use crate::session::ServerError;
use crate::sync_health::{self, Failing};
use crate::sync_ports::{AroundSync, Events, MailboxEvent};
use crate::warm::Warming;

const TAG: &str = "sync";
const INBOX: &str = "INBOX";
/// How many steps of older mail one round reads of each folder still being read, before it gives the others a turn.
pub const OLDER_STEPS: usize = 4;
/// Seconds between the rounds that read older mail, when none is left to read; a first scan that leaves some kicks
/// the next round at once.
pub const OLDER_EVERY: i64 = 600;
/// Seconds after the start before the first, which goes on with what a quit or a crash left unread.
pub const OLDER_FIRST: i64 = 15;

/// How one account's sync stands now: its health (ok, syncing or error), what it last said, and while the Inbox's
/// first scan still has older mail to read, how far it has got.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct AccountSync {
    pub health: &'static str,
    pub msg: String,
    pub reading: Option<Progress>,
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
    /// The accounts whose sync runs now.
    flying: BTreeSet<String>,
    /// Those asked for again while it ran: each goes round again as it ends.
    again: BTreeSet<String>,
    /// The syncs running now, of one account or of several: while any is, the sync as a whole runs.
    rounds: usize,
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
    /// Told whenever an account's sync ends, for those waiting on it.
    landed: Condvar,
    /// One warming round at a time.
    warming: Mutex<()>,
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
    /// What a round asks of the other contexts: snoozes woken before it, new mail triaged after, older mail read.
    pub around: Arc<dyn AroundSync>,
    /// Folders beyond the Inbox, warmed as a round ends.
    pub warm: Arc<Warming>,
}

/// An account's flight, from the moment a sync takes it, which lands however its sync ends: a sync that failed or
/// panicked part way, or a round that panicked before it began, leaves the account free to sync again.
struct Flight<'a> {
    syncing: &'a Syncing,
    account: &'a str,
    landed: bool,
}

impl Drop for Flight<'_> {
    fn drop(&mut self) {
        if !self.landed {
            let mut held = self.syncing.held();
            held.flying.remove(self.account);
            held.again.remove(self.account);
            drop(held);
            self.syncing.landed.notify_all();
        }
    }
}

/// A sync of one account or several, counted while it runs, however it ends.
struct Round<'a>(&'a Syncing);

impl Drop for Round<'_> {
    fn drop(&mut self) {
        let mut held = self.0.held();
        held.rounds = held.rounds.saturating_sub(1);
        held.sync.running = held.rounds > 0;
    }
}

impl Syncing {
    pub fn new(parts: SyncingParts) -> Syncing {
        let SyncingParts { ingest, accounts, store, folders, events, clock, around, warm } = parts;
        let held = Held { sync: SyncState { result: "idle".to_owned(), ..SyncState::default() }, ..Held::default() };
        Syncing {
            ingest,
            accounts,
            store,
            folders,
            events,
            clock,
            around,
            warm,
            held: Mutex::new(held),
            landed: Condvar::new(),
            warming: Mutex::new(()),
        }
    }

    /// What is held, whole between statements: so a lock a thread that panicked let go is still sound, and its
    /// flight still lands.
    fn held(&self) -> MutexGuard<'_, Held> {
        self.held.lock().unwrap_or_else(PoisonError::into_inner)
    }

    /// How each account's sync stands now: one whose failures in a row now show, three minutes on, as failing.
    pub fn status(&self) -> Result<BTreeMap<String, AccountSync>, Fault> {
        let now = self.seconds();
        let held = self.held();
        let mut status = held.status.clone();
        for (account, run) in &held.failing {
            let said = status.get(account);
            let (syncing, reading) =
                (said.is_some_and(|now| now.health == "syncing"), said.and_then(|now| now.reading));
            if run.shows(now) && !syncing {
                status.insert(account.clone(), AccountSync { health: "error", msg: run.class.to_owned(), reading });
            }
        }
        Ok(status)
    }

    /// The computer woke from sleep, or its network changed: trouble that may pass from before is no measure of now.
    /// Each such run starts again, so a failure from before neither shows by its age nor counts toward two in a row,
    /// and the sync on waking, while the network comes back, is the first of a new run. What shows already stays
    /// until a sync says otherwise.
    pub fn woke(&self) {
        self.held().failing.retain(|_, run| !run.passing);
    }

    /// The clock's seconds now.
    fn seconds(&self) -> i64 {
        self.clock.now().micros().div_euclid(1_000_000)
    }

    /// How the sync as a whole stands now.
    pub fn sync(&self) -> Result<SyncState, Fault> {
        Ok(self.held().sync.clone())
    }

    /// An account's health and what it said, keeping how far its Inbox's first scan has got.
    fn set_status(&self, account: &str, health: &'static str, msg: String) {
        let mut held = self.held();
        let reading = held.status.get(account).and_then(|now| now.reading);
        held.status.insert(account.to_owned(), AccountSync { health, msg, reading });
    }

    /// An account shows as it did before a sync whose failure may pass, keeping how far its Inbox's first scan has
    /// got, which the reading of its older mail may have moved on meanwhile.
    fn show_as_before(&self, account: &str, before: Option<AccountSync>) {
        let mut held = self.held();
        let reading = held.status.get(account).and_then(|now| now.reading);
        match before {
            Some(before) => held.status.insert(account.to_owned(), AccountSync { reading, ..before }),
            None if reading.is_some() => {
                held.status.insert(account.to_owned(), AccountSync { health: "ok", msg: String::new(), reading })
            }
            None => held.status.remove(account),
        };
    }

    /// How far an account's Inbox's first scan has got, None once it is read whole. An account no sync has
    /// reached yet in this process reads as connected: its server just answered.
    fn set_reading(&self, account: &str, reading: Option<Progress>) {
        let mut held = self.held();
        let now =
            held.status.entry(account.to_owned()).or_insert(AccountSync { health: "ok", msg: String::new(), reading });
        now.reading = reading;
    }

    fn page(&self, topic: Topic, data: serde_json::Value) {
        self.events.publish(MailboxEvent::Page { topic, data });
    }

    /// Sync one account's INBOX: how many new messages it kept. A first scan keeps none new.
    fn sync_account(&self, email: &str) -> Result<usize, Fault> {
        let before = self.held().status.get(email).cloned();
        self.set_status(email, "syncing", String::new());
        // A sync that panics fails as `Panic`, and the other accounts' go on.
        let synced = caught(|| self.ingest.sync_folder(email, INBOX, false, None));
        match synced.unwrap_or_else(|fault| Err(SyncError::Store(fault))) {
            Ok(synced) => {
                self.held().failing.remove(email);
                let new = if synced.afresh { 0 } else { synced.kept };
                if synced.afresh {
                    // How far it got, said only where the rest will be read.
                    let reading = synced.reading.filter(|_| self.around.older_to_read());
                    self.set_reading(email, reading);
                }
                self.set_status(email, "ok", format!("+{}", synced.kept));
                let at = when::iso(self.clock.now());
                command_ports::write(&*self.store, |tx| tx.record_sync_result(email, None, &at))?;
                self.page(Topic::SyncDone, json!({"account": email, "kept": synced.kept, "junk": synced.junk}));
                if new > 0 {
                    self.page(Topic::NewMail, json!({"account": email, "folder": INBOX, "count": new}));
                } else if synced.afresh && synced.kept + synced.junk > 0 {
                    // What a first scan stored changes the rail's counts, though none of it is new.
                    self.folders.invalidate(Some(email))?;
                }
                Ok(new)
            }
            Err(error) => {
                let (class, kind, now) = (error.class(), error_kind(error), self.seconds());
                let run = {
                    let mut held = self.held();
                    let run = Failing::and(held.failing.get(email), class, sync_health::may_pass(class, kind), now);
                    held.failing.insert(email.to_owned(), run.clone());
                    run
                };
                if !run.shows(now) {
                    // It may pass: the account shows as it did, and the next sync says.
                    self.show_as_before(email, before);
                    log::info(TAG, "an account's sync failed; it shows if the next fails too", &[]);
                    return Ok(0);
                }
                self.set_status(email, "error", class.to_owned());
                let at = when::iso(self.clock.now());
                command_ports::write(&*self.store, |tx| tx.record_sync_result(email, Some((class, kind)), &at))?;
                log::warning(TAG, "an account's sync failed", &[], Some(class));
                Ok(0)
            }
        }
    }

    /// An account's flight: its sync, and again for as long as one was asked for while it ran. How many new
    /// messages it kept.
    fn fly(&self, mut flight: Flight<'_>) -> usize {
        let email = flight.account;
        let mut new = 0;
        loop {
            match caught(|| self.sync_account(email)).and_then(|synced| synced) {
                Ok(kept) => new += kept,
                Err(fault) => log::error(TAG, "a sync round failed", &[], Some(fault.class)),
            }
            let mut held = self.held();
            if held.again.remove(email) {
                continue;
            }
            // It lands as it sees no ask waiting, so an ask that comes after starts a flight of its own.
            held.flying.remove(email);
            flight.landed = true;
            drop(held);
            self.landed.notify_all();
            return new;
        }
    }

    /// A sync of every account.
    pub fn sync_once(&self) {
        self.sync_guarded(None);
    }

    /// Sync `emails`, or every account, each on its own and each single flight: an account whose sync runs already
    /// goes round again as that one ends, and this doesn't wait for it. What is snoozed and due wakes first; new mail
    /// sets its triage going, outside the flights; folders beyond the Inbox are warmed last. A round that panics is
    /// logged and ends, and each of its accounts' flights lands.
    pub fn sync_guarded(&self, emails: Option<BTreeSet<String>>) {
        let accounts = match caught(|| self.accounts.emails()).and_then(|accounts| accounts) {
            Ok(accounts) => accounts,
            Err(fault) => {
                log::error(TAG, "a sync round failed", &[], Some(fault.class));
                return;
            }
        };
        let mine: Vec<String> = {
            let mut held = self.held();
            let (mut mine, mut joined) = (Vec::new(), false);
            for email in accounts.into_iter().filter(|e| emails.as_ref().is_none_or(|named| named.contains(e))) {
                if held.flying.contains(&email) {
                    held.again.insert(email);
                    joined = true;
                } else {
                    held.flying.insert(email.clone());
                    mine.push(email);
                }
            }
            if mine.is_empty() && joined {
                // Each account it names is syncing, and goes round again as that ends.
                return;
            }
            held.rounds += 1;
            held.sync.running = true;
            mine
        };
        let round = Round(self);
        let flights = mine.iter().map(|account| Flight { syncing: self, account, landed: false }).collect();
        if let Err(fault) = caught(|| self.round(flights)) {
            log::error(TAG, "a sync round failed", &[], Some(fault.class));
        }
        drop(round);
        let warmed: BTreeSet<String> = mine.into_iter().collect();
        let _turn = self.warming.lock().unwrap_or_else(PoisonError::into_inner);
        if let Err(fault) = caught(|| self.warm.tick(Some(&warmed))).and_then(|ticked| ticked) {
            log::warning("warm", "tick error", &[], Some(fault.class));
        }
    }

    /// One sync of the accounts whose flights it holds, each on a thread of its own when there are several, once
    /// what is snoozed and due has woken. New mail sets its triage going.
    fn round(&self, flights: Vec<Flight<'_>>) {
        self.around.wake_due();
        let new: usize = if flights.len() == 1 {
            flights.into_iter().map(|flight| self.fly(flight)).sum()
        } else {
            std::thread::scope(|scope| {
                let flying: Vec<_> = flights.into_iter().map(|flight| scope.spawn(move || self.fly(flight))).collect();
                flying.into_iter().map(|flight| flight.join().unwrap_or(0)).sum()
            })
        };
        if new > 0 {
            // New mail: the rail's unread badges now, not after their TTL.
            if let Err(fault) = self.folders.invalidate(None) {
                log::warning(TAG, "the rail's counts kept couldn't go", &[], Some(fault.class));
            }
            self.around.new_mail();
        }
        {
            let mut held = self.held();
            held.sync.result = format!("{new} new");
            held.sync.last = Some(when::iso(self.clock.now()));
        }
        log::info(TAG, "%(count)s new", &[(Field::Count, &new.to_string())]);
    }

    /// One round of reading older mail: of each folder whose first scan left some, a few steps, newest first, each
    /// account on a thread of its own. Whether any account stored a step and has more to read, so the next round
    /// should come at once.
    pub fn read_older(&self) -> Result<bool, Fault> {
        let accounts: BTreeSet<String> = self.accounts.emails()?.into_iter().collect();
        let mut by_account: BTreeMap<String, Vec<String>> = BTreeMap::new();
        for (account, folder) in self.ingest.reading_older()? {
            if accounts.contains(&account) {
                by_account.entry(account).or_default().push(folder);
            }
        }
        let more = std::thread::scope(|scope| {
            let rounds: Vec<_> = by_account
                .iter()
                .map(|(account, folders)| scope.spawn(move || self.read_older_of(account, folders)))
                .collect();
            rounds.into_iter().any(|round| round.join().unwrap_or(false))
        });
        Ok(more)
    }

    /// One account's part of a round of reading older mail: whether it has more to read after this.
    fn read_older_of(&self, account: &str, folders: &[String]) -> bool {
        let mut more = false;
        for folder in folders {
            let inbox = folder.eq_ignore_ascii_case(INBOX);
            let mut heard = |reading: Option<Progress>| {
                if inbox {
                    self.set_reading(account, reading);
                }
            };
            // A read that panics ends as one that failed: the steps it stored stay, the account's other folders are
            // read still, and the next round asks again.
            let read = caught(|| self.ingest.read_older(account, folder, OLDER_STEPS, &mut heard));
            match read.unwrap_or_else(|fault| Err(SyncError::Store(fault))) {
                Ok(reading) => {
                    more |= reading.is_some();
                    if inbox && reading.is_none() {
                        self.set_reading(account, None);
                    }
                }
                Err(error) => log::warning(TAG, "older mail wasn't read", &[], Some(error.class())),
            }
        }
        more
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

impl InboxSync for Syncing {
    /// Waits for the account's sync running now, or runs one here: a first look at an Inbox joins the sync that
    /// reads it rather than read it a second time beside it.
    fn sync_inbox(&self, account: &str) -> bool {
        if !self.held().flying.contains(account) {
            // Run here, or, if one started meanwhile, have it go round once more.
            self.sync_guarded(Some(BTreeSet::from([account.to_owned()])));
        }
        let held = self
            .landed
            .wait_while(self.held(), |held| held.flying.contains(account))
            .unwrap_or_else(PoisonError::into_inner);
        // A failure that may pass leaves the account showing as it did, "ok" perhaps: its run of failures says it.
        !held.failing.contains_key(account) && held.status.get(account).is_some_and(|now| now.health == "ok")
    }
}

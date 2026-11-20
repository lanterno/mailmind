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
//! `read_older` rounds; until it is all read, the account's status says how far the scan has got. A read of it that
//! met trouble that may pass goes on as soon as the account's next sync works, rather than at the next round.
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
//! then it shows as it did before, and its record keeps no error, but its status says it is trying again
//! (`trying_again`), so nothing says it synced when its last sync didn't. A wake, or a change of network, starts each
//! such run again (`woke`). A sync that fails tells the open tabs (`sync-trouble`), which read the status again.
//!
//! A connection to an account's server that couldn't be made outside its sync, by trouble that may pass (IDLE's, as
//! it connects again after its connection was lost, or a job's), says so within seconds rather than at the next
//! round, minutes on (`unreached`): the account shows as trying again, as after a sync that failed, the tabs are
//! told, and the sync that measures it runs now. It counts no failed sync: the next that fails is the run's first.
//! One dropped connection that connects again at once logs in, and says nothing.
//!
//! While an account's trouble may pass, the background tries it again every `sync_health::RETRY_SECONDS` (`try_unreached`),
//! so it says it synced within moments of its server answering again, rather than at the next round. A try is its
//! sync, run on its own: one that fails changes nothing and says nothing, and counts no failed sync, so trouble shows
//! as it would have, after the round's next sync or three minutes on.
//!
//! A sync that works after one of the account's failed, a wake between them or not, and a person's Retry, let go of
//! the waits of the changes queued for its server (`AroundSync::server_back`): they go now, not minutes on; and its
//! IDLE worker, resting after a connection that failed, connects now (`Realtime::hurry`), so new mail is heard within
//! moments, not after a rest that may have grown to five minutes.
//!
//! An account removed (`removed`) stops each sync and read of older mail reading its mail at its next step, as its
//! flights (`flights`) were told, and nothing is kept of how its syncs went: its status, how far its first scan had
//! got, its run of failures, an ask to sync it again; now, and again as the last of them lands.

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

/// How one account's sync stands now: its health (ok, syncing or error, or none before its first sync), what it last
/// said, while the Inbox's first scan still has older mail to read how far it has got, and whether its last sync
/// failed by trouble that may pass, which doesn't show as failing yet: the next sync tries again.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct AccountSync {
    pub health: &'static str,
    pub msg: String,
    pub reading: Option<Progress>,
    pub trying_again: bool,
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
    /// Each account whose last read of older mail met trouble that may pass: its next sync that works reads on.
    older_failed: BTreeSet<String>,
    /// Each account whose last sync failed, kept through a wake: its next sync that works lets the changes queued for
    /// its server go.
    unreached: BTreeSet<String>,
    /// The accounts whose sync now is a try of a server that didn't answer (`try_unreached`).
    retrying: BTreeSet<String>,
}

/// The coarse, scrubbed kind of a sync's failure, from its class alone (`accounts.domain.health.error_kind`). MailMind
/// having no password for the account (`NoCredential`: none in the Keychain, or wherever the account says it is) is a
/// kind of its own, `NO_PASSWORD`: nothing was sent, and only a password typed again helps.
pub fn error_kind(error: SyncError) -> &'static str {
    let name = error.class().to_lowercase();
    let has = |words: &[&str]| words.iter().any(|w| name.contains(w));
    if has(&["nocredential"]) {
        "NO_PASSWORD"
    } else if has(&["tlspinmismatch"]) {
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

    /// How each account's sync stands now: one whose failures in a row now show, three minutes on, as failing; one
    /// whose don't show yet as it did before, trying again. One syncing now says that alone.
    pub fn status(&self) -> Result<BTreeMap<String, AccountSync>, Fault> {
        let now = self.seconds();
        let held = self.held();
        let mut status = held.status.clone();
        for (account, run) in &held.failing {
            let said = status.get(account);
            let (syncing, reading) =
                (said.is_some_and(|now| now.health == "syncing"), said.and_then(|now| now.reading));
            if syncing {
                continue;
            }
            if run.shows(now) {
                let msg = run.class.to_owned();
                status.insert(account.clone(), AccountSync { health: "error", msg, reading, trying_again: false });
            } else {
                // As it was, health and all, but its last sync failed: nothing may say it synced. An account no sync
                // has reached has no health to show.
                let none = AccountSync { health: "", msg: String::new(), reading, trying_again: false };
                status.entry(account.clone()).or_insert(none).trying_again = true;
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
        held.status.insert(account.to_owned(), AccountSync { health, msg, reading, trying_again: false });
    }

    /// An account shows as it did before a sync whose failure may pass, keeping how far its Inbox's first scan has
    /// got, which the reading of its older mail may have moved on meanwhile.
    fn show_as_before(&self, account: &str, before: Option<AccountSync>) {
        let mut held = self.held();
        let reading = held.status.get(account).and_then(|now| now.reading);
        match before {
            Some(before) => held.status.insert(account.to_owned(), AccountSync { reading, ..before }),
            None if reading.is_some() => held.status.insert(
                account.to_owned(),
                AccountSync { health: "ok", msg: String::new(), reading, trying_again: false },
            ),
            None => held.status.remove(account),
        };
    }

    /// How far an account's Inbox's first scan has got, None once it is read whole. An account no sync has
    /// reached yet in this process reads as connected: its server just answered.
    fn set_reading(&self, account: &str, reading: Option<Progress>) {
        let mut held = self.held();
        let connected = AccountSync { health: "ok", msg: String::new(), reading, trying_again: false };
        let now = held.status.entry(account.to_owned()).or_insert(connected);
        now.reading = reading;
    }

    /// Nothing is kept of how a removed account's syncs went: its status, with how far its Inbox's first scan had got,
    /// its run of failures, and an ask to sync it again.
    fn forget(&self, account: &str) {
        let mut held = self.held();
        held.status.remove(account);
        held.failing.remove(account);
        held.again.remove(account);
        held.older_failed.remove(account);
        held.unreached.remove(account);
        held.retrying.remove(account);
    }

    /// The account was removed: each sync reading its mail stops at its next step (its flights were told), and nothing
    /// is kept of how its syncs went, now or as the last of them lands, so a sync that ended meanwhile leaves nothing
    /// either.
    pub fn removed(self: &Arc<Self>, account: &str) {
        self.forget(account);
        let (syncing, gone) = (Arc::clone(self), account.to_owned());
        self.ingest.flights().after(account, Box::new(move || syncing.forget(&gone)));
    }

    fn page(&self, topic: Topic, data: serde_json::Value) {
        self.events.publish(MailboxEvent::Page { topic, data });
    }

    /// How an account's sync stands changed for the worse: the open tabs read it again. Nothing else tells them, as a
    /// sync that fails stores and says nothing else.
    fn trouble(&self, account: &str) {
        self.page(Topic::SyncTrouble, json!({"account": account}));
    }

    /// A connection to `account`'s server, made outside its sync, couldn't be made (IDLE's as it connects again, or a
    /// job's): trouble that may pass shows at once as a sync that failed does, trying again, rather than at the next
    /// sync, minutes on; the tabs hear of it, and the sync that measures it runs now. The run counts no failed sync,
    /// so the next that fails is its first. Nothing changes while the account's sync runs, which says how it went, or
    /// once its syncs fail already, or for an account no longer kept; any other trouble is the sync's to say.
    pub fn unreached(&self, account: &str, error: SyncError) {
        let (class, kind) = (error.class(), error_kind(error));
        if !sync_health::may_pass(class, kind) {
            return;
        }
        if !self.accounts.emails().is_ok_and(|emails| emails.iter().any(|e| e == account)) {
            return;
        }
        let now = self.seconds();
        {
            let mut held = self.held();
            if held.flying.contains(account) || held.failing.contains_key(account) {
                return;
            }
            held.failing.insert(account.to_owned(), Failing::heard(class, now));
            // Its next sync that works lets the changes queued for its server go, as after a sync that failed.
            held.unreached.insert(account.to_owned());
        }
        let message = "a connection to an account's server failed; it is trying again";
        log::line(&log::Line { level: log::Level::Info, tag: TAG, message, fields: &[], error: Some(class) });
        self.trouble(account);
        self.around.sync_soon();
    }

    /// Sync one account's INBOX: how many new messages it kept. A first scan keeps none new.
    fn sync_account(&self, email: &str) -> Result<usize, Fault> {
        // While it reads, a removal stops it at its next step; then nothing more is said or kept of the account, and a
        // sync that finds it gone already has nothing to say either.
        let flight = self.ingest.flights().fly(email);
        let before = self.held().status.get(email).cloned();
        self.set_status(email, "syncing", String::new());
        // A sync that panics fails as `Panic`, and the other accounts' go on.
        let synced = caught(|| self.ingest.sync_folder(email, INBOX, false, None));
        match synced.unwrap_or_else(|fault| Err(SyncError::Store(fault))) {
            _ if flight.stopped() => {
                self.forget(email);
                Ok(0)
            }
            Err(SyncError::UnknownAccount) => {
                self.forget(email);
                Ok(0)
            }
            Ok(synced) => {
                self.held().retrying.remove(email);
                self.held().failing.remove(email);
                if self.held().unreached.remove(email) {
                    // The server answers again: what was queued for it while it didn't goes now.
                    self.around.server_back(email);
                }
                if self.held().older_failed.remove(email) {
                    // The server answers again: the older mail its last read couldn't reach is read on now, not at
                    // the background's next round, minutes on.
                    self.around.older_to_read();
                }
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
                    // One message names its UID, so a notification can say whose it is and open it.
                    let mut said = json!({"account": email, "folder": INBOX, "count": new});
                    if new == 1 {
                        said["uid"] = json!(synced.newest_kept);
                    }
                    self.page(Topic::NewMail, said);
                } else if synced.afresh && synced.kept + synced.junk > 0 {
                    // What a first scan stored changes the rail's counts, though none of it is new.
                    self.folders.invalidate(Some(email))?;
                }
                Ok(new)
            }
            Err(error) => {
                let (class, kind, now) = (error.class(), error_kind(error), self.seconds());
                let tried = self.held().retrying.remove(email);
                if tried && sync_health::may_pass(class, kind) {
                    // A try of a server that still doesn't answer: the account shows as it did, its run as it was.
                    self.show_as_before(email, before);
                    return Ok(0);
                }
                let run = {
                    let mut held = self.held();
                    held.unreached.insert(email.to_owned());
                    let run = Failing::and(held.failing.get(email), class, sync_health::may_pass(class, kind), now);
                    held.failing.insert(email.to_owned(), run.clone());
                    run
                };
                if !run.shows(now) {
                    // It may pass: the account shows as it did, and the next sync says.
                    self.show_as_before(email, before);
                    // Its class, so a run that fails and works by turns still says what it met.
                    let message = "an account's sync failed; it shows if the next fails too";
                    log::line(&log::Line {
                        level: log::Level::Info,
                        tag: TAG,
                        message,
                        fields: &[],
                        error: Some(class),
                    });
                    self.trouble(email);
                    return Ok(0);
                }
                self.set_status(email, "error", class.to_owned());
                self.trouble(email);
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

    /// Try again each account whose syncs fail by trouble that may pass and whose sync isn't running: its sync, on its
    /// own, each on a thread of its own when there are several. One that works ends the trouble as any sync that works
    /// does; one that fails changes nothing (`sync_account`). Nothing else a round does goes with it: no snooze wakes,
    /// no folder is warmed, and the round's last end stays as it was. New mail sets its triage going.
    pub fn try_unreached(&self) {
        let trying: Vec<String> = {
            let mut held = self.held();
            let trying: Vec<String> = held
                .failing
                .iter()
                .filter(|(account, run)| run.passing && !held.flying.contains(*account))
                .map(|(account, _)| account.clone())
                .collect();
            for account in &trying {
                held.flying.insert(account.clone());
                held.retrying.insert(account.clone());
            }
            trying
        };
        if trying.is_empty() {
            return;
        }
        let flights: Vec<Flight<'_>> =
            trying.iter().map(|account| Flight { syncing: self, account, landed: false }).collect();
        let new: usize = std::thread::scope(|scope| {
            let flying: Vec<_> = flights.into_iter().map(|flight| scope.spawn(move || self.fly(flight))).collect();
            flying.into_iter().map(|flight| flight.join().unwrap_or(0)).sum()
        });
        if new > 0 {
            if let Err(fault) = self.folders.invalidate(None) {
                log::warning(TAG, "the rail's counts kept couldn't go", &[], Some(fault.class));
            }
            self.around.new_mail();
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

    /// One account's part of a round of reading older mail: whether it has more to read after this. An account removed
    /// meanwhile stops it at its next step, and nothing more is read or said of it.
    fn read_older_of(&self, account: &str, folders: &[String]) -> bool {
        let flight = self.ingest.flights().fly(account);
        let (mut more, mut gone) = (false, false);
        for folder in folders {
            if flight.stopped() {
                break;
            }
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
                Err(SyncError::UnknownAccount) => {
                    gone = true;
                    break;
                }
                // Another client deleted or renamed it: forgotten, it isn't read again.
                Err(SyncError::Server(error)) if error.is_gone() => {
                    if let Err(fault) = self.folders.gone(account, folder) {
                        log::warning(TAG, "a folder the server no longer has wasn't forgotten", &[], Some(fault.class));
                    }
                }
                Err(error) => {
                    log::warning(TAG, "older mail wasn't read", &[], Some(error.class()));
                    if sync_health::may_pass(error.class(), error_kind(error)) {
                        self.held().older_failed.insert(account.to_owned());
                    }
                }
            }
        }
        if gone || flight.stopped() {
            self.forget(account);
            return false;
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
        // A person asked: what waits for the account's server goes with its sync, whatever the last one came to.
        self.around.server_back(account);
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

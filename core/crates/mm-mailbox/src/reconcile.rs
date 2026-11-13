//! What a sync can't notice: mail gone from the server, deleted or moved in webmail, another client or a filter
//! (`mailbox.application.reconcile` and `domain.prune`). A sync only adds new mail, so those rows linger as ghosts;
//! reconcile prunes them, each folder a job of its own on the account's gate.
//!
//! It never prunes on a server's error, on a folder the server renumbered, or on an empty SEARCH against mail
//! stored, and only what was stored before its SEARCH may go: the sync passes no gate, and can store mail meanwhile.

use std::collections::HashSet;
use std::sync::Arc;

use mm_kernel::errors::{Failure, Fault, Problem};
use mm_kernel::events::Topic;
use mm_kernel::log::{self, Field};
use mm_kernel::seams::Clock;
use mm_kernel::when;
use serde_json::{Value, json};

use crate::command_ports::{self, CommandStore, MoveAccounts};
use crate::folders::Folders;
use crate::gates::{Gates, Priority, Refused, Wait, on_gate};
use crate::session::{MailServers, Search, ServerError};
use crate::stats::LiveStats;
use crate::sync_ports::{Events, MailboxEvent};

const TAG: &str = "reconcile";
/// The audit trail a prune is written to.
const TRAIL: &str = "reconcile";
/// Seconds each step waits: a hung account mustn't pin the gate for the interactive minute.
pub const READ_TIMEOUT: u64 = 15;

/// Why this folder can't be pruned, the server having renumbered it, or None when it hasn't as far as either side
/// says.
pub fn renumbered(server: Option<u32>, stored: Option<i64>) -> Option<String> {
    match (server.filter(|s| *s != 0), stored.filter(|s| *s != 0)) {
        (Some(server), Some(stored)) if i64::from(server) != stored => {
            Some(format!("UIDVALIDITY changed ({stored}->{server}) \u{2014} prune skipped"))
        }
        _ => None,
    }
}

/// Why this folder can't be pruned, its SEARCH having found nothing though mail is stored.
pub fn empty_search(live: usize, stored: usize, force: bool) -> Option<String> {
    (live == 0 && stored > 0 && !force).then(|| "empty SEARCH against non-empty store \u{2014} skipped".to_owned())
}

/// What one folder's reconcile, or one account's added up, came to.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Pruned {
    pub account: String,
    pub live: usize,
    pub stored: usize,
    pub ghosts: usize,
    pub pruned: usize,
    /// Why it pruned nothing: a class, never what a server said.
    pub error: Option<String>,
    /// How many folders it reached.
    pub folders: usize,
}

impl Pruned {
    /// As an answer's `results` and a dry run show it.
    pub fn to_json(&self) -> Value {
        json!({"account": self.account, "live": self.live, "stored": self.stored, "ghosts": self.ghosts,
               "pruned": self.pruned, "error": self.error, "folders": self.folders})
    }
}

#[derive(Debug)]
pub struct Reconciler {
    store: Arc<dyn CommandStore>,
    accounts: Arc<dyn MoveAccounts>,
    servers: Arc<dyn MailServers>,
    gates: Arc<dyn Gates>,
    folders: Arc<Folders>,
    events: Arc<dyn Events>,
    clock: Arc<dyn Clock>,
    stats: Arc<LiveStats>,
}

/// What wires the Reconciler.
#[derive(Debug)]
pub struct ReconcilerParts {
    pub store: Arc<dyn CommandStore>,
    pub accounts: Arc<dyn MoveAccounts>,
    pub servers: Arc<dyn MailServers>,
    pub gates: Arc<dyn Gates>,
    pub folders: Arc<Folders>,
    pub events: Arc<dyn Events>,
    pub clock: Arc<dyn Clock>,
    /// The counts the page shows, which a prune moves.
    pub stats: Arc<LiveStats>,
}

impl Reconciler {
    pub fn new(parts: ReconcilerParts) -> Reconciler {
        let ReconcilerParts { store, accounts, servers, gates, folders, events, clock, stats } = parts;
        Reconciler { store, accounts, servers, gates, folders, events, clock, stats }
    }

    /// The folders reconcile looks at: each one a sync has stored, INBOX first.
    fn reconciled_folders(&self, email: &str) -> Result<Vec<String>, Fault> {
        let folders = command_ports::read(&*self.store, |tx| tx.synced_folders(email))?;
        Ok(if folders.is_empty() { vec!["INBOX".to_owned()] } else { folders })
    }

    /// The live UIDs of `folder` and its UIDVALIDITY, over a session of its own.
    fn live(&self, email: &str, folder: &str) -> Result<(Vec<u32>, Option<u32>), ServerError> {
        let mut s = self.servers.open(email, READ_TIMEOUT)?;
        let read = s.select(folder, true).and_then(|selected| Ok((s.search(Search::All)?, selected.uidvalidity)));
        match read {
            Err(ServerError::Unreachable(_)) => s.shutdown(),
            _ => s.logout(),
        }
        read
    }

    /// Prune one folder's rows whose UID is no longer live, and stamp the survivors verified; with `dry_run`, only
    /// count the ghosts.
    fn reconcile_folder(&self, email: &str, folder: &str, dry_run: bool) -> Result<Pruned, Fault> {
        let stored_uids = command_ports::read(&*self.store, |tx| tx.stored_uids(email, folder))?;
        let mut res = Pruned { account: email.to_owned(), stored: stored_uids.len(), ..Pruned::default() };
        let (live, uidvalidity) = match self.live(email, folder) {
            Ok(read) => read,
            Err(error) => {
                log::warning(TAG, "an account failed", &[], Some(error.class()));
                res.error = Some(error.class().to_owned());
                return Ok(res);
            }
        };
        let stored_uidvalidity = command_ports::read(&*self.store, |tx| tx.uidvalidity(email, folder))?;
        if let Some(why) = renumbered(uidvalidity, stored_uidvalidity) {
            res.error = Some(why);
            return Ok(res);
        }
        let live_set: HashSet<i64> = live.iter().map(|u| i64::from(*u)).collect();
        res.live = live_set.len();
        res.ghosts = stored_uids.iter().filter(|u| !live_set.contains(u)).count();
        if let Some(why) = empty_search(res.live, res.stored, false) {
            res.error = Some(why);
            return Ok(res);
        }
        if dry_run {
            return Ok(res);
        }
        let now = self.clock.now();
        let at = when::iso(now);
        let seconds = now.micros().div_euclid(1_000_000);
        res.pruned =
            command_ports::write(&*self.store, |tx| tx.prune(email, folder, &live, &stored_uids, &at, seconds))?;
        Ok(res)
    }

    /// Each folder a sync has stored for the account, a job of its own on the account's gate: the counts added up,
    /// the first folder's error and how many folders it reached. Not waiting, it stops at the first folder whose
    /// gate is held, and is Busy if that is the first.
    fn reconcile_account(&self, email: &str, dry_run: bool, priority: Priority, wait: Wait) -> Result<Pruned, Busy> {
        let mut out = Pruned { account: email.to_owned(), ..Pruned::default() };
        for folder in self.reconciled_folders(email).map_err(Busy::Fault)? {
            let res =
                match on_gate(&*self.gates, email, priority, wait, || self.reconcile_folder(email, &folder, dry_run)) {
                    Ok(res) => res.map_err(Busy::Fault)?,
                    Err(Refused::Busy) if out.folders == 0 => return Err(Busy::Held),
                    // A person's work came: the rest wait for next time.
                    Err(Refused::Busy) => break,
                    Err(Refused::Nested) => return Err(Busy::Fault(Fault::new("NestedGate"))),
                };
            out.folders += 1;
            out.live += res.live;
            out.stored += res.stored;
            out.ghosts += res.ghosts;
            out.pruned += res.pruned;
            out.error = out.error.or(res.error);
        }
        Ok(out)
    }

    /// A record of each account's prune, all of one moment.
    fn trail(&self, results: &[&Pruned], auto: bool) -> Result<(), Fault> {
        let stamp = when::iso(self.clock.now());
        let records: Vec<Value> = results
            .iter()
            .map(|r| {
                let mut record = json!({"kind": "reconcile", "ts": stamp});
                if auto {
                    record["auto"] = Value::Bool(true);
                }
                record["account"] = json!(r.account);
                record["pruned"] = json!(r.pruned);
                record["live"] = json!(r.live);
                record
            })
            .collect();
        command_ports::write(&*self.store, |tx| tx.audit(TRAIL, &records))
    }

    /// Prune one account's ghosts, folder by folder, yielding to a person's work: its gate is taken without
    /// waiting, and a held one is tried again next time. Open lists hear what went.
    pub fn auto_reconcile_one(&self, email: &str) -> Result<(), Fault> {
        let res = match self.reconcile_account(email, false, Priority::Maintenance, Wait::Not) {
            Ok(res) => res,
            Err(Busy::Held) => return Ok(()),
            Err(Busy::Fault(fault)) => return Err(fault),
        };
        if res.pruned == 0 {
            return Ok(());
        }
        // Committed before the next turn asks a server anything.
        self.trail(&[&res], true)?;
        self.stats.invalidate();
        self.folders.invalidate(Some(email))?;
        self.events.publish(MailboxEvent::Page {
            topic: Topic::Reconcile,
            data: json!({"account": email, "pruned": res.pruned}),
        });
        log::info(TAG, "auto: pruned %(count)s ghost row(s)", &[(Field::Count, &res.pruned.to_string())]);
        Ok(())
    }

    /// Reconcile the account named, or each in turn for `all` or none, every folder a sync has stored.
    pub fn do_reconcile(&self, account: Option<&str>, dry_run: bool) -> Result<Value, Failure> {
        let emails = match account.filter(|a| !a.is_empty() && *a != "all") {
            Some(one) if self.accounts.move_account(one)?.is_some() => vec![one.to_owned()],
            Some(_) => return Err(Problem::refused("unknown account").into()),
            None => self.accounts.emails()?,
        };
        let mut results = Vec::new();
        for email in &emails {
            match self.reconcile_account(email, dry_run, Priority::Interactive, Wait::Ever) {
                Ok(res) => results.push(res),
                Err(Busy::Fault(fault)) => return Err(fault.into()),
                Err(Busy::Held) => return Err(Fault::new("Busy").into()),
            }
        }
        if !dry_run {
            let pruned: Vec<&Pruned> = results.iter().filter(|r| r.pruned > 0).collect();
            if !pruned.is_empty() {
                // After every account's server, committed as it ends.
                self.trail(&pruned, false)?;
            }
            self.stats.invalidate();
        }
        Ok(json!({"ok": true, "dry_run": dry_run, "results": results.iter().map(Pruned::to_json).collect::<Vec<_>>(),
                  "total_ghosts": results.iter().map(|r| r.ghosts).sum::<usize>(),
                  "total_pruned": results.iter().map(|r| r.pruned).sum::<usize>(),
                  "generated": when::iso(self.clock.now())}))
    }
}

/// Why an account's reconcile stopped: its gate was held, or something failed.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Busy {
    Held,
    Fault(Fault),
}

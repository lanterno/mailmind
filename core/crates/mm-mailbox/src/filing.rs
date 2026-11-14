//! Filing a message as a person asks, through the one command path (`MailCommands`), and what each outcome answers
//! (`mailbox.application.filing` and `domain.filing_answer`). A message that has gone is NotFound, and one the server
//! wouldn't take a Conflict in the words for it: either way it is where it was. The tray's Retry and Discard of what
//! is waiting are here too.

use std::collections::{HashMap, HashSet};
use std::sync::Arc;
use std::time::Duration;

use mm_kernel::errors::{Failure, Fault, Kind, Problem};
use mm_kernel::events::Topic;
use mm_kernel::log::{self, Field};
use mm_kernel::seams::{Clock, Moment};
use serde_json::{Value, json};

use crate::command::{Accepted, Outcome};
use crate::command_ports::{self, Tokens, Tx};
use crate::commands::MailCommands;
use crate::folders::Folders;
use crate::gates::{Gates, Priority, Refused, Wait, on_gate};
use crate::ingest::Ingest;
use crate::stats::LiveStats;
use crate::sync_ports::{Events, MailboxEvent};
use crate::undo;

const TAG: &str = "do";
/// What a message gone from where the request saw it is told.
pub const GONE: &str = "not found";
/// Rows one request may file: the page sends more as more requests, under one undo group.
pub const BULK_MAX: usize = 500;
/// The filing verbs a marked row may take.
pub const FILING_VERBS: [&str; 3] = ["archive", "trash", "junk"];
/// The flags a request may set: \Deleted, \Draft and \Answered are the app's to set, never a request's.
pub const FLAG_NAMES: [&str; 2] = ["flagged", "seen"];

/// A copy of a message as a request names it: its key, its folder, and its uid there where given.
pub type NamedCopy = (i64, String, Option<i64>);

/// What each verb's filing is called once done.
fn filed(kind: &str) -> &'static str {
    match kind {
        "trash" => "trashed",
        "archive" => "archived",
        "junk" => "junked",
        _ => "moved",
    }
}

/// What a person is told of a move of `kind` that won't happen: the verb's own words, else the plain ones.
pub fn refusal(kind: &str, outcome: Outcome) -> &'static str {
    match (kind, outcome) {
        ("archive", Outcome::NoFolder) => "no archive folder on this account",
        ("junk", Outcome::NoFolder) => "no junk folder on this account",
        ("archive", _) => "the server refused the archive",
        _ => "the server refused the move",
    }
}

/// What one message of many came to: its answer, or what it raised.
type Said = Result<Value, Problem>;

/// What the person hears of many moves: one the server refused went back alone, and the rest stay filed, named by
/// their key, folder and uid as they were. With none filed, what the first raised, or NotFound with none to file. A
/// move the worker has, queued or waiting on its account's gate, counts as queued, as it does when it is the only one.
pub fn of_many(
    accepted: &[(i64, Option<Accepted>)],
    results: &HashMap<i64, Said>,
    names: &std::collections::BTreeMap<i64, (i64, String, i64)>,
) -> Result<Value, Problem> {
    let filed: Vec<(i64, &Value)> =
        accepted.iter().filter_map(|(p, _)| results.get(p).and_then(|r| r.as_ref().ok()).map(|v| (*p, v))).collect();
    if filed.is_empty() {
        let first = accepted.iter().find_map(|(p, _)| results.get(p).and_then(|r| r.as_ref().err()));
        return Err(first.cloned().unwrap_or_else(|| Problem::not_found(GONE)));
    }
    let queued = filed
        .iter()
        .filter(|(_, said)| matches!(said.get("status").and_then(Value::as_str), Some("queued" | "waiting")))
        .count();
    let named: Vec<Value> = filed
        .iter()
        .filter_map(|(p, _)| names.get(p))
        .map(|(key, folder, uid)| json!({"key": key, "folder": folder, "uid": uid}))
        .collect();
    let undoable = filed.iter().any(|(_, said)| said.get("undoable").is_some_and(crate::command::truthy));
    Ok(json!({"ok": true, "msg": if queued > 0 { "queued" } else { "filed" }, "filed": named, "count": filed.len(),
              "failed": accepted.len() - filed.len(), "queued": queued, "undoable": undoable}))
}

/// How marked rows came out, each standing for members of its own: a row failed if none of its messages was filed,
/// and is partial if only some were.
fn rows_tally(results: &HashMap<i64, Said>, members: &[Vec<i64>]) -> (usize, bool) {
    let each: Vec<usize> =
        members.iter().map(|pks| pks.iter().filter(|p| results.get(p).is_some_and(Result::is_ok)).count()).collect();
    let failed = each.iter().filter(|n| **n == 0).count();
    let partial = each.iter().zip(members).any(|(n, pks)| *n > 0 && *n < pks.len());
    (failed, partial)
}

#[derive(Debug)]
pub struct Filing {
    commands: Arc<MailCommands>,
    ingest: Arc<Ingest>,
    folders: Arc<Folders>,
    gates: Arc<dyn Gates>,
    clock: Arc<dyn Clock>,
    events: Arc<dyn Events>,
    tokens: Arc<dyn Tokens>,
    stats: Arc<LiveStats>,
}

/// What wires Filing.
#[derive(Debug)]
pub struct FilingParts {
    pub commands: Arc<MailCommands>,
    pub ingest: Arc<Ingest>,
    pub folders: Arc<Folders>,
    pub gates: Arc<dyn Gates>,
    pub clock: Arc<dyn Clock>,
    pub events: Arc<dyn Events>,
    pub tokens: Arc<dyn Tokens>,
    /// The counts the page shows, which a change that lands, or is given back, moves.
    pub stats: Arc<LiveStats>,
}

impl Filing {
    /// How long the tray's Discard waits for a try at the change to end.
    pub const PENDING_WAIT: Duration = Duration::from_secs(15);

    pub fn new(parts: FilingParts) -> Filing {
        let FilingParts { commands, ingest, folders, gates, clock, events, tokens, stats } = parts;
        Filing { commands, ingest, folders, gates, clock, events, tokens, stats }
    }

    /// The worker has changes to carry: it starts on them now, and the tray shows them. What another context queued
    /// on its own (a mute marking its thread read, a snooze waking read mail unread) says so here too.
    pub fn flags_queued(&self) {
        self.commands.kick();
        self.stats.invalidate();
        self.events.publish(MailboxEvent::Page { topic: Topic::Outbox, data: json!({}) });
    }

    /// A queued change reached the server or won't, or a person asked it again or dropped it.
    pub fn settled(&self) {
        self.stats.invalidate();
        self.events.publish(MailboxEvent::Page { topic: Topic::CommandsChanged, data: json!({}) });
    }

    /// A change landed on its server: the counts, and the rail's badges, move.
    fn invalidate(&self) {
        self.stats.invalidate();
        if let Err(fault) = self.folders.invalidate(None) {
            log::warning(TAG, "the rail's counts kept couldn't go", &[], Some(fault.class));
        }
    }

    /// What keeps a move's reversal under `group`, in the block that accepts the move.
    fn pushing(&self, group: Option<&str>) -> impl Fn(&dyn Tx, &Value) -> Result<(), Fault> + '_ {
        let group = group.map(str::to_owned);
        move |tx, entry| undo::push(tx, entry, group.as_deref(), self.clock.now())
    }

    /// A move accepted and committed: a try at it, and what the person hears of it.
    fn submit_move(&self, kind: &str, accepted: Option<&Accepted>) -> Result<Value, Failure> {
        let Some(accepted) = accepted else { return Err(Problem::not_found(GONE).into()) };
        if !accepted.created {
            return Ok(json!({"ok": true, "msg": "queued", "status": "queued", "undoable": false}));
        }
        match self.commands.submit(accepted)? {
            Outcome::Applied => {
                self.invalidate();
                Ok(json!({"ok": true, "msg": filed(kind), "undoable": accepted.undoable}))
            }
            outcome @ (Outcome::Queued | Outcome::Waiting) => {
                self.flags_queued();
                Ok(json!({"ok": true, "msg": "queued", "status": outcome.name(), "undoable": accepted.undoable}))
            }
            Outcome::Cancelled => Ok(json!({"ok": true, "msg": "cancelled", "undoable": false})),
            outcome => {
                // The message is listed again.
                self.stats.invalidate();
                Err(Problem::new(Kind::Conflict, refusal(kind, outcome)).into())
            }
        }
    }

    /// A move of one message, through the command path: what the person hears of it.
    pub fn file(
        &self,
        kind: &str,
        pk: i64,
        undo_group: Option<&str>,
        mutation_id: Option<&str>,
        to: Option<&str>,
    ) -> Result<Value, Failure> {
        let push = self.pushing(undo_group);
        let accepted = self.commands.accept_move(kind, pk, mutation_id, Some(&push), to)?;
        self.submit_move(kind, accepted.as_ref())
    }

    /// Trash message `pk` with nothing for Undo to reverse, as blocking its sender does: whether it went or will go.
    /// One that has gone, or that its server wouldn't take, is where it was.
    pub fn trash_now(&self, pk: i64) -> Result<bool, Fault> {
        let accepted = self.commands.accept_move("trash", pk, None, None, None)?;
        match self.submit_move("trash", accepted.as_ref()) {
            Ok(_) => Ok(true),
            Err(Failure::Problem(no)) if matches!(no.kind, Kind::NotFound | Kind::Conflict) => Ok(false),
            Err(Failure::Problem(_)) => Err(Fault::new("Problem")),
            Err(Failure::Fault(fault)) => Err(fault),
        }
    }

    /// A flag change, through the command path: what the person hears of it.
    pub fn set_flags(&self, pk: i64, flags: &[(&str, bool)], mutation_id: Option<&str>) -> Result<Value, Failure> {
        let Some(accepted) = self.commands.accept_flags(pk, flags, mutation_id)? else {
            return Err(Problem::not_found(GONE).into());
        };
        if !accepted.created {
            return Ok(json!({"ok": true, "msg": "queued", "status": "queued", "undoable": false}));
        }
        match self.commands.submit(&accepted)? {
            Outcome::Applied => {
                self.invalidate();
                Ok(json!({"ok": true, "msg": "ok", "undoable": false}))
            }
            outcome @ (Outcome::Queued | Outcome::Waiting) => {
                self.flags_queued();
                Ok(json!({"ok": true, "msg": "queued", "status": outcome.name(), "undoable": false}))
            }
            Outcome::Cancelled => Ok(json!({"ok": true, "msg": "cancelled", "undoable": false})),
            _ => Err(Problem::new(Kind::Conflict, "the server refused the flag change").into()),
        }
    }

    /// The message a move to a picked folder names and where it goes, as (its account, the folder), once it's a
    /// folder the message's own account may file into. Reads and lets go, and may ask the server what folders it has.
    fn picked_folder(&self, pk: i64, to: &str) -> Result<String, Failure> {
        if to.is_empty() {
            return Err(Problem::bad_request().into());
        }
        let Some(row) = command_ports::read(self.commands.store(), |tx| tx.message(pk))? else {
            return Err(Problem::not_found(GONE).into());
        };
        if row.folder == to {
            return Err(Problem::refused("it's in that folder already").into());
        }
        self.folders.ensure_takes_filing(&row.account, to)?;
        Ok(row.account)
    }

    /// Move one message to a folder of its account's that a person picked.
    pub fn move_to(
        &self,
        pk: i64,
        to: &str,
        undo_group: Option<&str>,
        mutation_id: Option<&str>,
    ) -> Result<Value, Failure> {
        let account = self.picked_folder(pk, to)?;
        let said = self.file("move", pk, undo_group, mutation_id, Some(to))?;
        self.folders.touched(&account, to)?;
        Ok(said)
    }

    /// The messages a conversation's row stands for: its thread in its folder. One without a thread id is a
    /// conversation of one, never the folder's unthreaded pool.
    fn conversation_of(tx: &dyn Tx, pk: i64) -> Result<Vec<i64>, Fault> {
        let Some(row) = tx.message(pk)? else { return Ok(Vec::new()) };
        if row.has_thread() {
            return tx.thread_pks_in(&row.account, &row.folder, row.thread_id.as_deref().unwrap_or_default());
        }
        Ok(vec![pk])
    }

    /// Archive, trash or move a whole conversation: every message sharing the anchor's thread in the anchor's folder.
    /// One transaction accepts them all under one undo group, so they leave the lists together and a single Undo
    /// restores them; then each is tried on its own, and answers for itself.
    pub fn file_thread(
        &self,
        kind: &str,
        pk: i64,
        to: Option<&str>,
        undo_group: Option<&str>,
        mutation_id: Option<&str>,
    ) -> Result<Value, Failure> {
        let account = match to {
            Some(to) => Some(self.picked_folder(pk, to)?),
            None => None,
        };
        let group = match undo_group {
            Some(g) if !g.is_empty() => g.to_owned(),
            _ => format!("thread:{}", self.tokens.hex(6)),
        };
        let push = self.pushing(Some(&group));
        let (accepted, names) = command_ports::write(self.commands.store(), |tx| {
            let pks = Self::conversation_of(tx, pk)?;
            let names = tx.names_of(&pks)?;
            let mut accepted = Vec::new();
            for p in pks {
                accepted.push((p, self.commands.accept_move_within(tx, kind, p, mutation_id, Some(&push), to)?));
            }
            Ok((accepted, names))
        })?;
        let results = self.try_each(kind, &accepted);
        let said = of_many(&accepted, &results, &names)?;
        if let (Some(account), Some(to)) = (account, to) {
            self.folders.touched(&account, to)?;
        }
        Ok(said)
    }

    /// The messages a marked row stands for, none if its copy has gone.
    fn row_members(tx: &dyn Tx, named: &NamedCopy, whole: bool) -> Result<Vec<i64>, Fault> {
        let Some(pk) = tx.copy_pk(named.0, &named.1, named.2)? else { return Ok(Vec::new()) };
        if whole { Self::conversation_of(tx, pk) } else { Ok(vec![pk]) }
    }

    /// Archive, trash or junk the marked rows as one. A row counts as failed if none of its messages was filed, and
    /// is `partial` if some were.
    pub fn file_many(
        &self,
        verb: &str,
        rows: &[(NamedCopy, bool)],
        undo_group: Option<&str>,
        mutation_id: Option<&str>,
    ) -> Result<Value, Failure> {
        self.file_rows(verb, rows, undo_group, mutation_id, None)
    }

    /// Move the marked rows to a folder a person picked. A folder is one account's, so rows of two accounts are
    /// refused whole, before any leaves a list.
    pub fn move_many(
        &self,
        to: &str,
        rows: &[(NamedCopy, bool)],
        undo_group: Option<&str>,
        mutation_id: Option<&str>,
    ) -> Result<Value, Failure> {
        let accounts: HashSet<String> = command_ports::read(self.commands.store(), |tx| {
            let mut accounts = HashSet::new();
            for ((key, folder, uid), _) in rows {
                if let Some(pk) = tx.copy_pk(*key, folder, *uid)? {
                    accounts.extend(tx.message(pk)?.map(|m| m.account));
                }
            }
            Ok(accounts)
        })?;
        if accounts.is_empty() {
            return Err(Problem::not_found(GONE).into());
        }
        if accounts.len() > 1 {
            return Err(Problem::refused("those are in different accounts").into());
        }
        if to.is_empty() {
            return Err(Problem::bad_request().into());
        }
        let account = accounts.into_iter().next().unwrap_or_default();
        self.folders.ensure_takes_filing(&account, to)?;
        let said = self.file_rows("move", rows, undo_group, mutation_id, Some(to))?;
        self.folders.touched(&account, to)?;
        Ok(said)
    }

    fn file_rows(
        &self,
        verb: &str,
        rows: &[(NamedCopy, bool)],
        undo_group: Option<&str>,
        mutation_id: Option<&str>,
        to: Option<&str>,
    ) -> Result<Value, Failure> {
        let group = match undo_group {
            Some(g) if !g.is_empty() => g.to_owned(),
            _ => format!("bulk:{}", self.tokens.hex(6)),
        };
        let push = self.pushing(Some(&group));
        let (accepted, members, names) = command_ports::write(self.commands.store(), |tx| {
            // Before any leaves a list.
            let mut members = Vec::new();
            for (named, whole) in rows {
                members.push(Self::row_members(tx, named, *whole)?);
            }
            let all: Vec<i64> = members.iter().flatten().copied().collect();
            let names = tx.names_of(&all)?;
            let mut taken = HashSet::new();
            let mut accepted = Vec::new();
            for p in members.iter().flatten() {
                // Two rows of one conversation.
                if taken.insert(*p) {
                    accepted.push((*p, self.commands.accept_move_within(tx, verb, *p, mutation_id, Some(&push), to)?));
                }
            }
            Ok((accepted, members, names))
        })?;
        let results = self.try_each(verb, &accepted);
        let mut said = of_many(&accepted, &results, &names)?;
        let (rows_failed, partial) = rows_tally(&results, &members);
        said["rows_failed"] = json!(rows_failed);
        said["partial"] = json!(partial);
        Ok(said)
    }

    /// Moves accepted and committed: a try at each, as filing one message is, on the gate with no transaction open.
    /// Whoever clicked waits about as long as for one: past FILE_WAIT the rest are left to the worker.
    fn try_each(&self, kind: &str, accepted: &[(i64, Option<Accepted>)]) -> HashMap<i64, Said> {
        let mut out = HashMap::new();
        let wait = i64::try_from(MailCommands::FILE_WAIT.as_micros()).unwrap_or(i64::MAX);
        let until = Moment::from_micros(self.clock.now().micros().saturating_add(wait));
        let mut left = false;
        for (i, (p, one)) in accepted.iter().enumerate() {
            let undoable = one.as_ref().is_some_and(|a| a.undoable);
            let late = i > 0 && one.as_ref().is_some_and(|a| a.created) && self.clock.now() >= until;
            let said = match (late, one) {
                (true, Some(one)) => self.commands.leave_to_worker(one).map_err(Failure::from).map(|status| {
                    left = true;
                    json!({"ok": true, "msg": "queued", "status": status.name(), "undoable": undoable})
                }),
                _ => self.submit_move(kind, one.as_ref()),
            };
            let said = match said {
                Ok(value) => Ok(value),
                Err(Failure::Problem(no)) if matches!(no.kind, Kind::NotFound | Kind::Conflict) => Err(no),
                Err(failure) => {
                    // It's back in the queue; the rest are claimed, and wait on this loop.
                    let class = match &failure {
                        Failure::Fault(fault) => fault.class,
                        Failure::Problem(_) => "Problem",
                    };
                    log::warning(TAG, "%(what)s failed", &[(Field::What, kind)], Some(class));
                    self.flags_queued();
                    Ok(json!({"ok": true, "status": "queued", "undoable": undoable}))
                }
            };
            out.insert(*p, said);
        }
        if left {
            self.flags_queued();
        }
        out
    }

    /// The tray's Retry: a change the queue gave up on goes back in.
    pub fn pending_retry(&self, id: i64) -> Result<Value, Failure> {
        if !self.commands.retry(id)? {
            return Err(Problem::not_found("").into());
        }
        self.settled();
        Ok(json!({"ok": true}))
    }

    /// The tray's Discard, on the account's gate. A message whose row went when it was queued is read back. A gate
    /// that stays held is Busy.
    pub fn pending_discard(&self, id: i64) -> Result<Value, Failure> {
        let Some(row) = command_ports::read(self.commands.store(), |tx| tx.command(id))? else {
            return Err(Problem::not_found("").into());
        };
        let job = || -> Result<bool, Fault> {
            let Some(gone) = self.commands.discard(id)? else { return Ok(false) };
            if gone.kind == "flag" || gone.message_id.is_empty() {
                return Ok(true);
            }
            let listed = command_ports::read(self.commands.store(), |tx| {
                tx.copy_in(&gone.account, &gone.src_folder, &gone.message_id, false)
            })?;
            // The next open of the folder has another chance.
            if !listed && let Err(e) = self.ingest.ingest_message(&gone.account, &gone.src_folder, &gone.message_id) {
                log::warning("pending", "reading a discarded move's message back", &[], Some(e.class()));
            }
            Ok(true)
        };
        match on_gate(&*self.gates, &row.account, Priority::Interactive, Wait::For(Self::PENDING_WAIT), job) {
            Err(Refused::Busy) => return Err(Problem::new(Kind::Busy, "").into()),
            Err(Refused::Nested) => return Err(Fault::new("NestedGate").into()),
            Ok(Err(fault)) => return Err(fault.into()),
            Ok(Ok(false)) => return Err(Problem::not_found("").into()),
            Ok(Ok(true)) => {}
        }
        self.settled();
        Ok(json!({"ok": true}))
    }
}

//! Undo (`platform.undo`): a stack in the database, so it outlives a restart, of what reverses each thing a person
//! did. It knows nothing of what an entry means: each kind's reverser says, and runs on the account's gate,
//! answering (ok, the account, the folder). An entry leaves the stack, committed, before any server is asked.
//!
//! An entry is kept for a day, and the stack holds the newest fifty, a group counting as one. The functions here
//! take their caller's block of work; `Undo`'s methods each take blocks of their own.

use std::fmt;
use std::sync::{Arc, Mutex};

use mm_kernel::errors::{Failure, Fault, Kind, Problem};
use mm_kernel::events::Topic;
use mm_kernel::log::{self, Field};
use mm_kernel::seams::{Clock, Moment};
use mm_kernel::when;
use serde_json::{Value, json};

use crate::command_ports::{self, CommandStore, Tx};
use crate::gates::{EVERY_ACCOUNT, Gates, Priority, Refused, Wait, on_gate};
use crate::sync_ports::{Events, MailboxEvent};

const TAG: &str = "undo";
/// How long an entry may be undone, in microseconds.
const KEEP_FOR: i64 = 24 * 3600 * 1_000_000;
/// How many entries the stack keeps; a group counts as one.
pub const UNDO_MAX: i64 = 50;

/// What a reversal came to: whether it went back, the account it touched and the folder.
pub type Reversed = (bool, Option<String>, Option<String>);

/// What reverses entries of some kinds.
pub trait Reverser: Send + Sync + fmt::Debug {
    /// Whether it reverses an entry of `kind`.
    fn reverses(&self, kind: &str) -> bool;
    /// Reverse `entry`, on its account's gate. An error is a reversal that may go better another time.
    fn reverse(&self, entry: &Value) -> Result<Reversed, Fault>;
    /// Whether a group's entries of `kind` on one account go back together, at one turn of its gate
    /// (`reverse_together`), rather than one at a time: a sweep's hundreds ask the server once, not once each.
    fn together(&self, _kind: &str) -> bool {
        false
    }
    /// Reverse `entries`, all of one kind and one account, at one turn of its gate: what each came to, in their
    /// order. An error is a reversal of them all that may go better another time.
    fn reverse_together(&self, entries: &[Value]) -> Result<Vec<Reversed>, Fault> {
        entries.iter().map(|entry| self.reverse(entry)).collect()
    }
}

fn oldest_kept(now: Moment) -> String {
    when::iso(Moment::from_micros(now.micros() - KEEP_FOR))
}

fn is_group(entry: &Value) -> bool {
    entry.get("kind").and_then(Value::as_str) == Some("bulk")
}

fn children(entry: &Value) -> Vec<Value> {
    entry.get("children").and_then(Value::as_array).cloned().unwrap_or_default()
}

fn kind_of(entry: &Value) -> &str {
    entry.get("kind").and_then(Value::as_str).unwrap_or_default()
}

/// The gate an entry's reversal runs on: its account's, or every account's when it names none.
fn gate_of(entry: &Value) -> &str {
    entry.get("account").and_then(Value::as_str).filter(|a| !a.is_empty()).unwrap_or(EVERY_ACCOUNT)
}

/// Put `entry` on top of the stack, or, when the top gathers `group`, add it to that group's children. Then keep
/// only the newest `UNDO_MAX`, none older than a day.
pub fn push(tx: &dyn Tx, entry: &Value, group: Option<&str>, now: Moment) -> Result<(), Fault> {
    // An empty group gathers nothing.
    let group = group.filter(|g| !g.is_empty());
    tx.undo_expire(&oldest_kept(now))?;
    let at = when::iso(now);
    let top = tx.undo_rows()?.into_iter().next();
    match (group, top) {
        (Some(group), Some(top)) if top.group.as_deref() == Some(group) => {
            let mut held = top.entry;
            if let Some(Value::Array(children)) = held.get_mut("children") {
                children.push(entry.clone());
            }
            tx.undo_update(top.id, &held)?;
        }
        (Some(group), _) => {
            let label = entry.get("label").cloned().unwrap_or_else(|| json!("Updated"));
            let held = json!({"kind": "bulk", "group": group, "label": label, "children": [entry]});
            tx.undo_insert(Some(group), &held, &at)?;
        }
        (None, _) => tx.undo_insert(None, entry, &at)?,
    }
    tx.undo_keep(UNDO_MAX)
}

/// Put an entry taken off the stack back on top: it, or what's left of its group, can be tried again.
pub fn put_back(tx: &dyn Tx, entry: &Value, now: Moment) -> Result<(), Fault> {
    let group = if is_group(entry) { entry.get("group").and_then(Value::as_str) } else { None };
    tx.undo_insert(group, entry, &when::iso(now))
}

/// Take the newest entry off the stack: None when none is left that may be undone.
pub fn pop(tx: &dyn Tx, now: Moment) -> Result<Option<Value>, Fault> {
    tx.undo_expire(&oldest_kept(now))?;
    let Some(top) = tx.undo_rows()?.into_iter().next() else { return Ok(None) };
    tx.undo_delete(top.id)?;
    Ok(Some(top.entry))
}

/// Take one group off the stack, wherever it stands; None when it has gone.
pub fn pop_group(tx: &dyn Tx, group: &str, now: Moment) -> Result<Option<Value>, Fault> {
    tx.undo_expire(&oldest_kept(now))?;
    let Some(held) = tx.undo_group_row(group)? else { return Ok(None) };
    tx.undo_delete(held.id)?;
    Ok(Some(held.entry))
}

/// There is nothing to reverse any more: the newest entry `names` is true of leaves the stack, or its group.
pub fn forget(tx: &dyn Tx, names: impl Fn(&Value) -> bool) -> Result<(), Fault> {
    for row in tx.undo_rows()? {
        if !is_group(&row.entry) {
            if names(&row.entry) {
                return tx.undo_delete(row.id);
            }
            continue;
        }
        let mut kids = children(&row.entry);
        let Some(hit) = kids.iter().rposition(&names) else { continue };
        kids.remove(hit);
        if kids.is_empty() {
            return tx.undo_delete(row.id);
        }
        let mut held = row.entry;
        held["children"] = Value::Array(kids);
        return tx.undo_update(row.id, &held);
    }
    Ok(())
}

/// The use case: Undo's button, and its count.
#[derive(Debug)]
pub struct Undo {
    store: Arc<dyn CommandStore>,
    gates: Arc<dyn Gates>,
    clock: Arc<dyn Clock>,
    events: Arc<dyn Events>,
    reversers: Vec<Arc<dyn Reverser>>,
    /// One change to the stack at a time.
    lock: Mutex<()>,
}

const COULDNT: &str = "couldn\u{2019}t undo \u{2014} the message may have moved";

impl Undo {
    pub fn new(
        store: Arc<dyn CommandStore>,
        gates: Arc<dyn Gates>,
        clock: Arc<dyn Clock>,
        events: Arc<dyn Events>,
        reversers: Vec<Arc<dyn Reverser>>,
    ) -> Undo {
        Undo { store, gates, clock, events, reversers, lock: Mutex::new(()) }
    }

    fn on_stack<T>(&self, work: impl FnOnce(&dyn Tx) -> Result<T, Fault>) -> Result<T, Fault> {
        let _one = self.lock.lock().map_err(|_| Fault::new("PoisonedLock"))?;
        command_ports::write(&*self.store, work)
    }

    /// The undo stack, oldest first.
    pub fn entries(&self) -> Result<Vec<Value>, Fault> {
        let oldest = oldest_kept(self.clock.now());
        command_ports::read(&*self.store, |tx| tx.undo_entries(&oldest))
    }

    fn reverser(&self, kind: &str) -> Option<&Arc<dyn Reverser>> {
        self.reversers.iter().find(|r| r.reverses(kind))
    }

    /// `work` on `account`'s gate, as a person waits.
    fn on_gate<T>(&self, account: &str, work: impl FnOnce() -> Result<T, Fault>) -> Result<T, Fault> {
        match on_gate(&*self.gates, account, Priority::Interactive, Wait::Ever, work) {
            Ok(done) => done,
            Err(Refused::Busy) => Err(Fault::new("Busy")),
            Err(Refused::Nested) => Err(Fault::new("NestedGate")),
        }
    }

    /// One entry's reversal, on its account's gate.
    fn reverse_one(&self, entry: &Value) -> Result<Reversed, Fault> {
        let held = || match self.reverser(kind_of(entry)) {
            Some(reverser) => reverser.reverse(entry),
            None => Ok((false, entry.get("account").and_then(Value::as_str).map(str::to_owned), None)),
        };
        self.on_gate(gate_of(entry), held)
    }

    /// A group's members, newest first, each run of one kind and one account that goes back together (`together`) at
    /// one turn of its gate and the rest one at a time: what each came to, in their order. One run's or member's
    /// trouble never stops the rest.
    fn reverse_members(&self, newest_first: &[Value]) -> Vec<Option<Reversed>> {
        let mut out = Vec::with_capacity(newest_first.len());
        let mut at = 0;
        while let Some(first) = newest_first.get(at) {
            let (kind, gate) = (kind_of(first), gate_of(first));
            let Some(reverser) = self.reverser(kind).filter(|r| r.together(kind)) else {
                out.push(self.reverse_one(first).map_err(|fault| Self::member_failed(&fault)).ok());
                at += 1;
                continue;
            };
            let run = newest_first[at..].iter().take_while(|e| kind_of(e) == kind && gate_of(e) == gate).count();
            let members = &newest_first[at..at + run];
            match self.on_gate(gate, || reverser.reverse_together(members)) {
                Ok(answers) => out.extend((0..run).map(|k| answers.get(k).cloned())),
                Err(fault) => {
                    Self::member_failed(&fault);
                    out.extend((0..run).map(|_| None));
                }
            }
            at += run;
        }
        out
    }

    fn member_failed(fault: &Fault) {
        log::warning(TAG, "a group member: %(why)s", &[(Field::Why, fault.class)], None);
    }

    /// Take the last thing done off the stack and reverse it, and say what went back: a group says how many of it
    /// did and how many didn't. Nothing to undo is NotFound (`empty`), and none of it going back a Conflict. What
    /// didn't go back is on the stack again for another try, and so is all of it when a reversal failed, which
    /// goes on up.
    pub fn undo_last(&self) -> Result<Value, Failure> {
        let now = self.clock.now();
        let entry = self.on_stack(|tx| pop(tx, now))?;
        self.reverse(entry)
    }

    /// As undo_last, for one group wherever it stands on the stack.
    pub fn undo_group(&self, group: &str) -> Result<Value, Failure> {
        let now = self.clock.now();
        let entry = self.on_stack(|tx| pop_group(tx, group, now))?;
        self.reverse(entry)
    }

    fn put_back(&self, entry: &Value) -> Result<(), Fault> {
        let now = self.clock.now();
        self.on_stack(|tx| put_back(tx, entry, now))
    }

    fn reverse(&self, entry: Option<Value>) -> Result<Value, Failure> {
        let Some(entry) = entry else {
            return Err(Problem::new(Kind::NotFound, "").with("empty", Value::Bool(true)).into());
        };
        let mut kept = false;
        let done = self.reverse_entry(&entry, &mut kept);
        if let Err(Failure::Fault(fault)) = &done {
            if !kept {
                self.put_back(&entry)?;
            }
            log::error(TAG, "error", &[], Some(fault.class));
        }
        done
    }

    fn reverse_entry(&self, entry: &Value, kept: &mut bool) -> Result<Value, Failure> {
        let label = entry.get("label").cloned().unwrap_or(Value::Null);
        let (went, mut answer) = if is_group(entry) {
            let mut kids = children(entry);
            let (mut account, mut folder) = (None, None);
            // Newest first, as they were tried.
            kids.reverse();
            let mut left = Vec::new();
            for (child, answer) in kids.iter().zip(self.reverse_members(&kids)) {
                match answer {
                    Some((true, a, f)) => (account, folder) = (a, f),
                    _ => left.push(child.clone()),
                }
            }
            let done = kids.len() - left.len();
            if !left.is_empty() {
                left.reverse();
                let mut smaller = entry.clone();
                smaller["children"] = Value::Array(left.clone());
                self.put_back(&smaller)?;
                *kept = true;
            }
            (
                done > 0,
                json!({"ok": true, "label": label, "account": account, "folder": folder, "reversed": done,
                       "failed": left.len()}),
            )
        } else {
            let (ok, account, folder) = self.reverse_one(entry)?;
            (ok, json!({"ok": true, "label": label, "account": account, "folder": folder}))
        };
        if !went {
            if !*kept {
                self.put_back(entry)?;
            }
            return Err(Problem::new(Kind::Conflict, COULDNT).into());
        }
        self.events.publish(MailboxEvent::Page { topic: Topic::UndoDone, data: json!({}) });
        answer["remaining"] = json!(self.entries()?.len());
        Ok(answer)
    }
}

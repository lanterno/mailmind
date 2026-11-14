//! Tidy's runs as the brief's strip reads them (`mailmind.mailbox.domain.tidy`, `application.tidying.latest`): the
//! newest run a person can still take back, and what it filed. A run is a group on the undo stack, named for its day.
//! And what a run that goes by itself may file, with no I/O: what the brief's Tidy offers, of what is still in an
//! Inbox, and never what a person has already taken back.

use std::collections::HashSet;
use std::fmt;

use mm_kernel::errors::{Failure, Fault};
use serde_json::Value;

use crate::ports::Row;

/// A run's undo group: `tidy-` and the local day it ran on.
pub const PREFIX: &str = "tidy-";
/// Local: not before someone's day has started.
pub const FIRST_HOUR: i64 = 6;
/// As many as the brief's list holds.
pub const READS: i64 = 400;

pub fn is_run(group: Option<&str>) -> bool {
    group.is_some_and(|g| g.starts_with(PREFIX))
}

/// A run's undo group, which is also its commands' mutation id: `tidy-<local day>`.
pub fn group_of(day: &str) -> String {
    format!("{PREFIX}{day}")
}

/// Each message whose archive an Undo took back, as (account, Message-ID).
pub type TakenBack = HashSet<(String, Option<String>)>;

/// Whether a digest row is one a run may file: in an Inbox, with a key and a uid, not taken back, and what the brief's
/// Tidy offers (`attention.safe_to_file`).
pub fn takes(row: &Row, taken_back: &TakenBack) -> bool {
    let text = |name: &str| row.get(name).and_then(Value::as_str);
    let present = |name: &str| row.get(name).is_some_and(|v| !v.is_null());
    let account = text("account").unwrap_or_default().to_owned();
    text("folder") == Some(crate::lists::INBOX)
        && present("message_key")
        && present("uid")
        && !taken_back.contains(&(account, text("message_id").map(str::to_owned)))
        && mm_kernel::verdict::safe_to_file(
            row.get("importance").and_then(Value::as_i64),
            text("category"),
            text("action"),
        )
}

/// What a run reads of the change queue's log.
pub trait TidyLog: Send + Sync + fmt::Debug {
    /// Whether any command was accepted under this mutation id.
    fn ran_under(&self, mutation_id: &str) -> Result<bool, Fault>;
    /// Each message whose archive an Undo took back, for as long as the log keeps it: it stays where a person put it.
    fn archives_undone(&self) -> Result<TakenBack, Fault>;
}

/// How a run files what it picked (the Mailbox's `file_many`): archived as one, under `group`, which is also its
/// commands' mutation id. What it answers is what a person who filed them would hear.
pub trait RunFiling: Send + Sync + fmt::Debug {
    fn archive(&self, rows: &[(crate::filing::NamedCopy, bool)], group: &str) -> Result<Value, Failure>;
}

/// One message a run filed, as its undo entry names it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Filed {
    pub subject: Option<String>,
    pub who: Option<String>,
}

/// An entry of the undo stack, as far as a run goes by it: its group, and what it gathers.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Entry {
    pub group: Option<String>,
    pub children: Vec<Filed>,
}

/// The undo stack, oldest first, none older than a day.
pub trait UndoEntries: Send + Sync + fmt::Debug {
    fn entries(&self) -> Result<Vec<Entry>, Fault>;
}

/// A run: its group, and what it filed, the last first.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Run {
    pub group: String,
    pub filed: Vec<Filed>,
}

/// The newest run on the stack. With an Undo of it, or once it has aged off the stack, there is none.
pub fn latest(stack: &dyn UndoEntries) -> Result<Option<Run>, Fault> {
    Ok(stack
        .entries()?
        .into_iter()
        .rev()
        .find(|e| is_run(e.group.as_deref()))
        .map(|e| Run { group: e.group.unwrap_or_default(), filed: e.children.into_iter().rev().collect() }))
}

//! Tidy's runs as the brief's strip reads them (`mailmind.mailbox.domain.tidy`, `application.tidying.latest`): the
//! newest run a person can still take back, and what it filed. A run is a group on the undo stack, named for its day.

use std::fmt;

use mm_kernel::errors::Fault;

/// A run's undo group: `tidy-` and the local day it ran on.
pub const PREFIX: &str = "tidy-";

pub fn is_run(group: Option<&str>) -> bool {
    group.is_some_and(|g| g.starts_with(PREFIX))
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

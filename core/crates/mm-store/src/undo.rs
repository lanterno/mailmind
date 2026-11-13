//! The undo stack as Tidy's strip reads it (`platform.undo.entries`): each entry's group and what it gathers.

use mm_kernel::errors::Fault;
use mm_kernel::seams::Moment;
use mm_mailbox::tidy::{Entry, Filed, UndoEntries};
use serde_json::Value;

use crate::attention::UndoStack;
use crate::blocks;

/// How long an entry may still be undone, in microseconds.
const KEEP_FOR: i64 = 24 * 3600 * 1_000_000;

fn text(value: Option<&Value>) -> Option<String> {
    value.and_then(Value::as_str).map(str::to_owned)
}

fn entry(held: &Value) -> Entry {
    let children = held.get("children").and_then(Value::as_array).map(Vec::as_slice).unwrap_or_default();
    Entry {
        group: text(held.get("group")),
        children: children.iter().map(|d| Filed { subject: text(d.get("subject")), who: text(d.get("who")) }).collect(),
    }
}

impl UndoEntries for UndoStack {
    fn entries(&self) -> Result<Vec<Entry>, Fault> {
        let oldest = mm_kernel::when::iso(Moment::from_micros(self.clock.now().micros() - KEEP_FOR));
        Ok(self.db.read(|c| blocks::undo_entries(c, &oldest))?.iter().map(entry).collect())
    }
}

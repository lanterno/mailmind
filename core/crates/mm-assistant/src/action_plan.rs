//! What a bulk command may do (`domain.action_plan`): its verbs, how far one may reach, what is taken from a model's
//! plan, and the plan a preview freezes.
//!
//! Nothing here touches mail. `bulk` holds the plans open and makes them, and the mail's own contexts carry them out.
//! Whether a filter narrows, and whether what it reaches is most of the mailbox, are Discovery's word.

use std::collections::BTreeSet;

use serde_json::{Map, Value};

use crate::text::{cut, python_str, strip, truthy};

/// Seconds a preview stays good for.
pub const TTL: i64 = 600;
/// Plans held at a time, the newest.
pub const KEPT: usize = 16;
/// What a command may do, the allowlist: the plan's schema offers the model these and no others.
pub const VERBS: [&str; 7] = ["archive", "trash", "junk", "read", "unread", "star", "snooze"];
/// What a snooze may wait for (`kernel.snoozed`).
pub const SNOOZE_PRESETS: [&str; 4] = ["later_today", "tomorrow", "weekend", "next_week"];
/// The most messages one command may touch.
pub const CAP: i64 = 500;
/// Trash and junk are the dangerous two, so they reach fewer.
pub const TRASH_CAP: i64 = 200;
/// How many a preview shows.
pub const SAMPLE: usize = 8;
/// How much of a command is read.
pub const COMMAND_MAX: usize = 512;
/// How much of the filter a model wrote is read.
pub const FILTER_MAX: usize = 400;
/// The roles a command reaches only if its filter names a folder.
pub const APART: [&str; 4] = ["sent", "drafts", "trash", "junk"];

/// What a preview froze: the messages, each by its key and the folder it was seen in.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ActionPlan {
    pub id: String,
    pub verb: String,
    pub preset: Option<String>,
    pub messages: Vec<(i64, String)>,
    /// When it expires, in microseconds since the epoch.
    pub expires: i64,
}

/// `str(text or "")`: Python's words for what a request or a model gave.
fn said(value: Option<&Value>) -> String {
    value.filter(|v| truthy(v)).map(python_str).unwrap_or_default()
}

pub fn command_of(text: Option<&Value>) -> String {
    cut(strip(&said(text)), COMMAND_MAX).to_owned()
}

pub fn filter_told(plan: &Map<String, Value>) -> String {
    cut(strip(&said(plan.get("filter"))), FILTER_MAX).to_owned()
}

/// The verb a model's plan acts with, or None when the plan isn't a clean act on the allowlist, which degrades to a
/// search: it acts on nothing.
pub fn verb_told(plan: &Map<String, Value>) -> Option<&'static str> {
    if plan.get("intent").and_then(Value::as_str) != Some("act") {
        return None;
    }
    let verb = plan.get("verb").and_then(Value::as_str)?;
    VERBS.into_iter().find(|v| *v == verb)
}

/// What a snooze waits for: only a snooze has one to name, and one that names none, or none there is, waits until
/// tomorrow, as its preview then says.
pub fn preset_told(plan: &Map<String, Value>, verb: &str) -> &'static str {
    let named = if verb == "snooze" { plan.get("snooze_preset").and_then(Value::as_str) } else { None };
    named.and_then(|p| SNOOZE_PRESETS.into_iter().find(|s| *s == p)).unwrap_or("tomorrow")
}

pub fn cap_of(verb: &str) -> i64 {
    if verb == "trash" || verb == "junk" { TRASH_CAP } else { CAP }
}

/// The folder roles a command leaves alone. A person who says "archive what Lin sent" means the mail that is around,
/// not the copy already archived, one in Trash or their own in Sent, so a filter reaches those only by naming a folder,
/// and a verb never reaches its own home.
pub fn out_of_reach(verb: &str, names_folder: bool) -> BTreeSet<&'static str> {
    let mut apart: BTreeSet<&'static str> = match verb {
        "archive" => ["archive", "all"].into(),
        "trash" => ["trash"].into(),
        "junk" => ["junk"].into(),
        _ => BTreeSet::new(),
    };
    if !names_folder {
        apart.extend(APART);
    }
    apart
}

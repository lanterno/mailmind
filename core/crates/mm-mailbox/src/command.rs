//! A change to the mail on a server, as it goes through the one path: accepted, tried, settled
//! (`mailbox.domain.command`). What its row says, what reverses it, what the tray shows of it, and which message a try
//! acts on (`mailbox.domain.try_target`) are here too. Values in and values out: `commands` does the asking.

use std::collections::BTreeMap;

use serde_json::{Map, Value, json};

use crate::destination::Destination;
use crate::sync_plan::COLUMNS;

/// What became of a command, as far as whoever asked for it hears.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Outcome {
    /// The server holds the change, or the message had already left its folder.
    Applied,
    /// The server can't be reached, or this process only reads: the worker has it.
    Queued,
    /// The account's gate didn't come free in time: the worker has it.
    Waiting,
    /// The server said NO or BAD: nothing happened, and no retry will make it.
    Refused,
    /// The account has no folder to move it to.
    NoFolder,
    /// The account is gone.
    Removed,
    /// An Undo or a Discard got there first.
    Cancelled,
}

impl Outcome {
    pub fn name(self) -> &'static str {
        match self {
            Outcome::Applied => "applied",
            Outcome::Queued => "queued",
            Outcome::Waiting => "waiting",
            Outcome::Refused => "refused",
            Outcome::NoFolder => "no_folder",
            Outcome::Removed => "removed",
            Outcome::Cancelled => "cancelled",
        }
    }

    /// Whether no retry can make it happen (`WONT_HAPPEN`).
    pub fn wont_happen(self) -> bool {
        matches!(self, Outcome::Refused | Outcome::NoFolder | Outcome::Removed)
    }
}

/// What a row of the command log holds.
#[derive(Clone, Debug, PartialEq)]
pub struct Command {
    pub id: i64,
    pub account: String,
    /// trash, archive, junk, move or flag.
    pub kind: String,
    /// The message's Message-ID, what outlasts a renumber; empty when it has none.
    pub message_id: String,
    /// Where the message was when it was accepted.
    pub src_folder: String,
    /// Where it goes, once that is known.
    pub dest: Option<String>,
    /// The copy as accepted, the flags, what they replaced: a JSON object.
    pub params: Value,
    /// queued, running, done or error.
    pub state: String,
    pub attempts: i64,
    pub next_attempt_at: i64,
    /// The server holds the change: done once.
    pub applied: bool,
    /// A try may have reached the server: the next asks before it repeats anything.
    pub begun: bool,
    /// What a reversal needs from the server's answer: a JSON object.
    pub outcome: Value,
    pub last_error: Option<String>,
    pub created_at: String,
}

impl Command {
    /// One of its params.
    pub fn param(&self, name: &str) -> Option<&Value> {
        self.params.get(name).filter(|v| !v.is_null())
    }
}

/// A message's row, as the command path reads it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Message {
    pub pk: i64,
    pub account: String,
    pub folder: String,
    pub uid: i64,
    pub message_id: Option<String>,
    pub subject: Option<String>,
    pub from_name: Option<String>,
    pub from_addr: Option<String>,
    pub thread_id: Option<String>,
    /// The command taking it away, while one is.
    pub pending_removal: Option<i64>,
    /// The flag columns (`sync_plan::COLUMNS`).
    pub flags: [i64; 5],
    pub message_key: Option<i64>,
}

impl Message {
    /// Whether it holds the flag named by its column.
    pub fn holds(&self, flag: &str) -> bool {
        COLUMNS.iter().position(|c| *c == flag).is_some_and(|i| self.flags[i] != 0)
    }

    /// Its Message-ID, trimmed; empty when it has none.
    pub fn mid(&self) -> String {
        self.message_id.as_deref().unwrap_or_default().trim().to_owned()
    }

    /// Whether it is in a thread: one without a thread id is a conversation of one.
    pub fn has_thread(&self) -> bool {
        !self.thread_id.as_deref().unwrap_or_default().trim().is_empty()
    }
}

/// A command's row, written in one transaction with what it hides and what reverses it.
#[derive(Clone, Debug, PartialEq)]
pub struct Accepted {
    /// None only for a request seen before whose row has since gone.
    pub id: Option<i64>,
    pub account: String,
    /// False: this request was seen before, and nothing new was written.
    pub created: bool,
    pub undoable: bool,
    /// Its row, claimed for a try now; None when only the worker may try it.
    pub claimed: Option<Command>,
}

/// What a try at a command left on the server.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct ServerAnswer {
    /// Why it won't happen; None once the server holds the change.
    pub wont: Option<Outcome>,
    /// The folder it went to.
    pub dest: Option<String>,
    /// The server marked it read on the way to Trash: a reversal takes that off.
    pub unseen: bool,
}

impl ServerAnswer {
    pub fn wont(outcome: Outcome) -> ServerAnswer {
        ServerAnswer { wont: Some(outcome), ..ServerAnswer::default() }
    }
}

const REQUEST_ID_MAX: usize = 128;

/// Whether a client's `mutation_id` can name its request, so a resubmit of it dedupes.
pub fn names_a_request(mutation_id: Option<&str>) -> bool {
    mutation_id.is_some_and(|m| (1..=REQUEST_ID_MAX).contains(&m.chars().count()))
}

/// What makes this request's row its own: the same request again finds it, and nothing else does.
pub fn idempotency_key(kind: &str, account: &str, message_id: &str, pk: i64, request: &str) -> String {
    let named = if message_id.is_empty() { pk.to_string() } else { message_id.to_owned() };
    format!("{kind}:{account}:{named}:{request}")
}

/// Whether a message can be asked to move: it is there, not already on its way out, and not already where `to`
/// names.
pub fn takes_a_move(row: Option<&Message>, to: Option<&str>) -> bool {
    row.is_some_and(|r| r.pending_removal.is_none() && Some(r.folder.as_str()) != to)
}

/// What a move's row keeps of its message as it is accepted, `epoch` being the folder's UIDVALIDITY then.
pub fn move_params(pk: i64, row: &Message, epoch: Option<i64>) -> Value {
    json!({"pk": pk, "uid": row.uid, "epoch": epoch, "seen": row.holds("seen"), "subject": row.subject,
           "thread_id": row.thread_id})
}

/// A flag change's params: the copy as asked on, the flags, and those it replaces.
pub fn flag_params(row: &Message, epoch: Option<i64>, flags: &[(&str, bool)]) -> Value {
    let wanted: Map<String, Value> = flags.iter().map(|(k, v)| ((*k).to_owned(), Value::Bool(*v))).collect();
    json!({"pk": row.pk, "uid": row.uid, "epoch": epoch, "flags": wanted, "was": held_now(row, flags)})
}

/// What `row` holds of these flags: what a change to them replaces, for a refusal to put back.
pub fn held_now(row: &Message, flags: &[(&str, bool)]) -> Value {
    Value::Object(flags.iter().map(|(k, _)| ((*k).to_owned(), Value::Bool(row.holds(k)))).collect())
}

/// A JSON object of flags as (name, on) pairs, the names the columns', in its order.
pub fn flags_of(value: Option<&Value>) -> Vec<(String, bool)> {
    let Some(Value::Object(flags)) = value else { return Vec::new() };
    flags.iter().map(|(k, v)| (k.clone(), truthy(v))).collect()
}

/// What Python's `bool()` makes of a JSON value.
pub fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

fn undo_label(kind: &str) -> &'static str {
    match kind {
        "trash" => "Trashed",
        "archive" => "Archived",
        "junk" => "Marked junk",
        "move" => "Moved",
        _ => "Updated",
    }
}

/// What Undo keeps to reverse a move, and to show it.
pub fn undo_entry(kind: &str, command_id: i64, row: &Message, message_id: &str, place: &Destination) -> Value {
    let who = match row.from_name.as_deref() {
        Some(name) if !name.is_empty() => Some(name),
        _ => row.from_addr.as_deref(),
    };
    json!({"kind": if place.drop_inbox_label { "gmail_archive" } else { "move" }, "command": command_id,
           "account": row.account, "src_folder": row.folder, "dest_folder": place.folder.as_deref().unwrap_or_default(),
           "message_id": message_id, "label": undo_label(kind), "subject": row.subject, "who": who,
           "thread_id": row.thread_id})
}

/// Whether an undo `entry` is the one that reverses the move `row`.
pub fn reverses(entry: &Value, row: &Command) -> bool {
    let text = |name: &str| entry.get(name).and_then(Value::as_str);
    matches!(text("kind"), Some("move" | "gmail_archive"))
        && text("account") == Some(row.account.as_str())
        && text("message_id") == Some(row.message_id.as_str())
        && text("src_folder") == Some(row.src_folder.as_str())
}

/// What a row the worker gave up on says, for the tray.
pub fn why_given_up(outcome: Outcome) -> &'static str {
    if outcome == Outcome::Removed { "account removed" } else { outcome.name() }
}

/// A change that hasn't reached a server, as the tray shows it.
pub fn tray_item(row: &Command, subject: &Value) -> Value {
    json!({"id": row.id, "account": row.account, "op": row.kind, "folder": row.src_folder, "dest": row.dest,
           "flags": row.params.get("flags").cloned().unwrap_or(Value::Null), "state": row.state,
           "attempts": row.attempts, "error": row.last_error, "subject": subject,
           "next_attempt_at": row.next_attempt_at, "created_at": row.created_at})
}

/// How many changes wait, a try included, and how many were given up on, from {state: how many}.
pub fn tray_counts(counts: &BTreeMap<String, i64>) -> (i64, i64) {
    let n = |state: &str| counts.get(state).copied().unwrap_or(0);
    (n("queued") + n("running"), n("error"))
}

/// Which message a try at a command acts on.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Target {
    /// Ask the server which UID the Message-ID names, and believe it.
    pub by_message_id: bool,
    /// What to act on otherwise, or if it won't search; None: the message has left.
    pub uid: Option<u32>,
}

/// Whether no earlier try can have reached the server.
pub fn is_first_try(just_accepted: bool, begun: bool) -> bool {
    just_accepted || !begun
}

/// Whether a UID seen in `epoch` still means the same message, as far as either side says.
pub fn same_epoch(epoch: Option<i64>, uidvalidity: Option<u32>) -> bool {
    match (epoch, uidvalidity) {
        (Some(epoch), Some(now)) if epoch != 0 && now != 0 => epoch == i64::from(now),
        _ => true,
    }
}

/// What a try acts on: `uid` and `epoch` as the command was accepted, `uidvalidity` as the select just said. A UID
/// means something only in the epoch it was seen in; on a first try the one seen as the command was accepted is it,
/// and a later try may follow one that moved the message, so the Message-ID says.
pub fn of_try(
    uid: Option<i64>,
    epoch: Option<i64>,
    uidvalidity: Option<u32>,
    first_try: bool,
    has_message_id: bool,
) -> Target {
    let seen = uid.filter(|u| *u != 0).and_then(|u| u32::try_from(u).ok());
    let same = same_epoch(epoch, uidvalidity);
    Target {
        by_message_id: has_message_id && !(first_try && seen.is_some() && same),
        uid: if same { seen } else { None },
    }
}

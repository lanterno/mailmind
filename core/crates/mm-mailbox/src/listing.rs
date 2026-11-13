//! What a row of a list says (`mailmind.mailbox.domain.listing`): a folder's, a conversation's, and the views that
//! aren't folders (Snoozed, Muted, Waiting, Reply Later). The page reads these answers and never works them out. A
//! list reads no clock here: its use case says what time it is.
//!
//! A row comes as the query gave it, each column by name; a column a query didn't ask for reads as null, as a value
//! SQLite held reads as Python reads it (`bool(r["seen"])`, `r["subject"] or ...`).

use mm_kernel::ago::{ago, rel_time, rel_time_future};
use mm_kernel::seams::Moment;
use serde_json::{Map, Value, json};

use crate::ports::Row;

pub const SNIPPET_CHARS: usize = 200;
pub const SUMMARY_CHARS: usize = 300;
const ACTION_NEEDED: &str = "action-needed";

static NULL: Value = Value::Null;

/// Column `name` of `r`, null when the query didn't ask for it.
pub fn col<'a>(r: &'a Row, name: &str) -> &'a Value {
    r.get(name).unwrap_or(&NULL)
}

/// Python's truth of a value: null, false, 0, "" and an empty list are false.
pub fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// `a or b`.
pub fn or(a: &Value, b: Value) -> Value {
    if truthy(a) { a.clone() } else { b }
}

/// A column's text, None for null or a value that isn't text.
pub fn text<'a>(r: &'a Row, name: &str) -> Option<&'a str> {
    col(r, name).as_str()
}

/// `(r[name] or "")[:chars]`.
fn cut(r: &Row, name: &str, chars: usize) -> Value {
    match col(r, name) {
        Value::String(s) => Value::String(s.chars().take(chars).collect()),
        v if !truthy(v) => Value::String(String::new()),
        other => other.clone(),
    }
}

fn flag(r: &Row, name: &str) -> Value {
    Value::Bool(truthy(col(r, name)))
}

/// An integer column, as `int()` would take it, None for null or what isn't one.
pub fn int(r: &Row, name: &str) -> Option<i64> {
    match col(r, name) {
        Value::Number(n) => n.as_i64().or_else(|| n.as_f64().map(|f| f as i64)),
        Value::String(s) => mm_kernel::text::strip(s).parse().ok(),
        _ => None,
    }
}

/// What the AI's verdict on a message asks of you (`kernel.attention.answers`): its importance, category and the
/// next step it wrote. A message with no verdict asks nothing.
pub fn attention(importance: &Value, category: &Value, action: &Value) -> [(&'static str, bool); 4] {
    let importance = importance.as_f64();
    let category = category.as_str();
    let has_step = action.as_str().is_some_and(|a| !mm_kernel::text::strip(a).is_empty());
    let reply_worthy = importance.unwrap_or(0.0) >= 4.0 || category == Some(ACTION_NEEDED);
    let needs_you = has_step || reply_worthy;
    let commitment_worthy = has_step && (importance.unwrap_or(0.0) >= 3.0 || category == Some(ACTION_NEEDED));
    let low = match importance {
        Some(i) if i != 0.0 => i <= 2.0,
        _ => true,
    };
    let safe_to_file = low && !has_step && matches!(category, Some("info" | "other"));
    [
        ("needs_you", needs_you),
        ("reply_worthy", reply_worthy),
        ("commitment_worthy", commitment_worthy),
        ("safe_to_file", safe_to_file),
    ]
}

fn add_attention(it: &mut Map<String, Value>, r: &Row) {
    for (name, value) in attention(col(r, "importance"), col(r, "category"), col(r, "action")) {
        it.insert(name.into(), Value::Bool(value));
    }
}

/// Which copy this is and who it is from.
fn who(r: &Row) -> Map<String, Value> {
    let mut it = Map::new();
    it.insert("key".into(), col(r, "message_key").clone());
    it.insert("folder".into(), col(r, "folder").clone());
    it.insert("uid".into(), col(r, "uid").clone());
    it.insert("from_name".into(), or(col(r, "from_name"), col(r, "from_addr").clone()));
    it.insert("from_addr".into(), col(r, "from_addr").clone());
    it.insert("subject".into(), or(col(r, "subject"), json!("(no subject)")));
    it
}

/// What the row shows beside its sender.
fn marks(it: &mut Map<String, Value>, r: &Row) {
    it.insert("snippet".into(), cut(r, "snippet", SNIPPET_CHARS));
    it.insert("seen".into(), flag(r, "seen"));
    it.insert("flagged".into(), flag(r, "flagged"));
    it.insert("has_attach".into(), flag(r, "has_attach"));
    it.insert("is_junk".into(), flag(r, "is_junk"));
    it.insert("bucket".into(), col(r, "bucket").clone());
}

fn when(r: &Row, now: Moment) -> Value {
    Value::String(rel_time(text(r, "date_iso"), now))
}

/// Where a page of `shown` items stands in the `total`.
pub fn window(shown: usize, total: i64, offset: i64) -> [(&'static str, Value); 3] {
    [("total", json!(total)), ("offset", json!(offset)), ("has_more", json!(offset + (shown as i64) < total))]
}

/// A row of a list of a folder or of the Inboxes, one conversation's latest message if `grouped`.
pub fn listed_item(r: &Row, grouped: bool, now: Moment) -> Value {
    let mut it = who(r);
    it.insert("when".into(), when(r, now));
    marks(&mut it, r);
    it.insert("importance".into(), col(r, "importance").clone());
    add_attention(&mut it, r);
    it.insert("thread_id".into(), col(r, "thread_id").clone());
    it.insert("summary".into(), cut(r, "summary", SUMMARY_CHARS));
    it.insert("action".into(), or(col(r, "action"), json!("")));
    it.insert("category".into(), col(r, "category").clone());
    it.insert("ts".into(), col(r, "ts").clone());
    it.insert("account".into(), col(r, "account").clone());
    if grouped {
        it.insert("count".into(), col(r, "cnt").clone());
        it.insert("thread_count".into(), or(col(r, "thread_count"), col(r, "cnt").clone()));
        it.insert("seen".into(), Value::Bool(!truthy(col(r, "unread"))));
        it.insert("flagged".into(), flag(r, "any_flagged"));
        it.insert("has_attach".into(), flag(r, "any_attach"));
    }
    Value::Object(it)
}

/// A snoozed message, and when it wakes.
pub fn snoozed_item(r: &Row, now: Moment) -> Value {
    let mut it = who(r);
    let wakes = int(r, "snoozed_until").map(|ts| rel_time_future(ts, now)).unwrap_or_default();
    it.insert("when".into(), Value::String(format!("wakes {wakes}")));
    it.insert("snoozed_until".into(), col(r, "snoozed_until").clone());
    marks(&mut it, r);
    it.insert("importance".into(), col(r, "importance").clone());
    add_attention(&mut it, r);
    it.insert("thread_id".into(), col(r, "thread_id").clone());
    it.insert("account".into(), col(r, "account").clone());
    Value::Object(it)
}

/// The start of a thread's row in a view that isn't a folder: its latest message and how many the thread holds.
fn thread_row(r: &Row, now: Moment) -> Map<String, Value> {
    let mut it = who(r);
    it.insert("when".into(), when(r, now));
    marks(&mut it, r);
    it
}

fn thread_tail(it: &mut Map<String, Value>, r: &Row) {
    it.insert("thread_id".into(), col(r, "thread_id").clone());
    it.insert("ts".into(), col(r, "ts").clone());
    it.insert("count".into(), col(r, "thread_count").clone());
}

/// A muted thread's latest message, which counts as read whatever its flag says.
pub fn muted_item(r: &Row, now: Moment) -> Value {
    let mut it = thread_row(r, now);
    it.insert("seen".into(), Value::Bool(true));
    thread_tail(&mut it, r);
    it.insert("muted".into(), Value::Bool(true));
    it.insert("account".into(), col(r, "account").clone());
    Value::Object(it)
}

/// A thread waiting on someone: on the other side if it was sent (who is the first address it went to), else on
/// you.
pub fn waiting_item(r: &Row, now: Moment) -> Value {
    let label = if text(r, "f_kind") == Some("sent") {
        format!("Sent {} ago · no reply", ago(int(r, "f_first_sent"), now))
    } else {
        format!("Awaiting your reply · {}", rel_time(text(r, "date_iso"), now))
    };
    let mut it = thread_row(r, now);
    thread_tail(&mut it, r);
    it.insert("waiting".into(), Value::Bool(true));
    it.insert("account".into(), col(r, "account").clone());
    it.insert("waiting_label".into(), Value::String(label));
    it.insert("kind".into(), col(r, "f_kind").clone());
    let to = text(r, "recipients").unwrap_or_default().split(',').next().unwrap_or_default();
    it.insert("to".into(), Value::String(mm_kernel::text::strip(to).to_owned()));
    Value::Object(it)
}

/// A thread pinned to reply to later.
pub fn replylater_item(r: &Row, now: Moment) -> Value {
    let mut it = thread_row(r, now);
    thread_tail(&mut it, r);
    it.insert("replylater".into(), Value::Bool(true));
    it.insert("account".into(), col(r, "account").clone());
    it.insert("waiting_label".into(), json!("Reply later"));
    Value::Object(it)
}

/// A conversation's messages as it opens, each once: a Gmail copy in the Inbox and in All Mail is one message. One
/// with no Message-ID has nothing to be the same as.
pub fn thread_items(rows: &[Row], now: Moment) -> Vec<Value> {
    let mut seen_ids = std::collections::HashSet::new();
    let mut items = Vec::new();
    for r in rows {
        let id = text(r, "message_id").map(mm_kernel::text::strip).unwrap_or_default();
        let id = if id.is_empty() { format!("pk:{}", col(r, "pk")) } else { id.to_owned() };
        if !seen_ids.insert(id) {
            continue;
        }
        let mut it = who(r);
        it.insert("when".into(), when(r, now));
        it.insert("snippet".into(), cut(r, "snippet", SNIPPET_CHARS));
        for name in ["seen", "flagged", "answered", "has_attach", "is_junk"] {
            it.insert(name.into(), flag(r, name));
        }
        items.push(Value::Object(it));
    }
    items
}

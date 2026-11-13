//! What the outbox tray lists (`compose.outbox.list_recent` and `compose.drafts.waiting`): the recent sends, each as
//! the page sees it (`outbox_item.listed`), and the saves that should have reached their server by now and haven't
//! (`draft_journal.listed`).

use std::fmt;
use std::sync::Arc;

use mm_kernel::errors::Fault;
use mm_kernel::seams::Clock;
use serde_json::{Value, json};

use crate::ports::{Row, StoredOutbox};

/// The tray names a save this many seconds overdue (`draft_journal.WAITING_AFTER`).
pub const WAITING_AFTER: i64 = 30;
/// Said of a send that went out and whose copy in Sent is still to be saved.
pub const SENT_COPY_PENDING: &str = "Delivered; its copy in Sent is still to be saved";
/// Said of a send that went out and whose copy in Sent couldn't be saved.
pub const SENT_COPY_FAILED: &str = "Delivered, but its copy couldn't be saved in Sent";

/// A queued send waits for a time a person chose (`outbox_item.SCHEDULED`) or to try again (`BACKOFF`).
const SCHEDULED: &str = "scheduled";
const BACKOFF: &str = "backoff";

/// A column as stored, null when the row hasn't it.
fn col<'a>(r: &'a Row, name: &str) -> &'a Value {
    r.get(name).unwrap_or(&Value::Null)
}

/// Python's truth of a stored value.
fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// `value or fallback`.
fn or(v: &Value, fallback: Value) -> Value {
    if truthy(v) { v.clone() } else { fallback }
}

/// A send as the outbox lists it (`outbox_item.listed`): one that went out is sent, or in error when its copy in
/// Sent couldn't be saved; a queued one says what it waits for.
pub fn listed(r: &Row) -> Value {
    let recipients = match col(r, "recipients") {
        Value::String(text) => serde_json::from_str::<Value>(text).ok(),
        _ => None,
    };
    let recipients = match recipients {
        Some(Value::Object(map)) => map,
        _ => serde_json::Map::new(),
    };
    let named = |field: &str| recipients.get(field).cloned().unwrap_or_else(|| json!([]));
    let bcc_count = match recipients.get("bcc") {
        Some(Value::Array(bcc)) => bcc.len(),
        Some(Value::Object(bcc)) => bcc.len(),
        Some(Value::String(bcc)) => bcc.chars().count(),
        _ => 0,
    };
    let (mut state, mut error) = (col(r, "state").clone(), col(r, "last_error").clone());
    let delivered = truthy(col(r, "delivered"));
    if delivered && state.as_str() != Some("sent") {
        if truthy(col(r, "sent_appended")) {
            (state, error) = (json!("sent"), Value::Null);
        } else if state.as_str() == Some("error") {
            error = json!(SENT_COPY_FAILED);
        } else {
            (state, error) = (json!("sent"), json!(SENT_COPY_PENDING));
        }
    }
    let waiting = if state.as_str() == Some("queued") { col(r, "wait_kind").as_str() } else { None };
    let unsure = col(r, "state").as_str() == Some("error") && truthy(col(r, "wire_begun")) && !delivered;
    json!({
        "id": col(r, "id"),
        "account": col(r, "account"),
        "subject": or(col(r, "subject"), json!("(no subject)")),
        "to": named("to"),
        "cc": named("cc"),
        "bcc_count": bcc_count,
        "state": state,
        "attempts": col(r, "attempts"),
        "error": error,
        "unsure": unsure,
        "created_at": col(r, "created_at"),
        "sent_at": col(r, "sent_at"),
        "next_attempt_at": or(col(r, "next_attempt_at"), json!(0)),
        "scheduled": waiting == Some(SCHEDULED),
        "retrying": waiting == Some(BACKOFF),
    })
}

/// A save the tray names (`draft_journal.listed`).
pub fn waiting(r: &Row) -> Value {
    json!({
        "id": col(r, "draft_id"),
        "account": col(r, "account"),
        "subject": or(col(r, "subject"), json!("")),
        "saved_at": col(r, "saved_at"),
        "attempts": col(r, "attempts"),
        "discarding": truthy(col(r, "discard")),
    })
}

/// Saves from before this should have reached their server (`draft_journal.overdue_since`): with no push job
/// running (read-only), every one should.
pub fn overdue_since(now: i64, pushing: bool) -> i64 {
    now - if pushing { WAITING_AFTER } else { 0 }
}

pub struct Outbox {
    stored: Arc<dyn StoredOutbox>,
    clock: Arc<dyn Clock>,
    /// Whether this process writes to a mail server, and so pushes saves (`profile.writes_allowed`).
    pushing: bool,
}

impl fmt::Debug for Outbox {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Outbox").field("pushing", &self.pushing).finish_non_exhaustive()
    }
}

impl Outbox {
    pub fn new(stored: Arc<dyn StoredOutbox>, clock: Arc<dyn Clock>, pushing: bool) -> Outbox {
        Outbox { stored, clock, pushing }
    }

    /// The tray: the newest `limit` sends, and the saves still to reach their server.
    pub fn tray(&self, limit: i64) -> Result<Value, Fault> {
        let items: Vec<Value> = self.stored.recent(limit)?.iter().map(listed).collect();
        let before = overdue_since(self.clock.now().whole_seconds(), self.pushing);
        let drafts: Vec<Value> = self.stored.overdue(before)?.iter().map(waiting).collect();
        Ok(json!({"ok": true, "items": items, "drafts": drafts}))
    }
}

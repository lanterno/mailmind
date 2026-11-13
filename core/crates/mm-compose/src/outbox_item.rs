//! An outbox row (`outbox_item`): what one is written as, what a try at it leaves, and what it owes once delivered.
//! Values in and values out. Which row may move from which state is the queue's, each a conditional UPDATE the worker
//! can't race, so that isn't said again here. How a row is listed is `outbox::listed`.

use crate::sending_ports::{Mark, NewSend, OutboxRow};
use crate::spec::Built;

/// Said of a send that may have gone out: the server stopped answering once it had the whole message.
pub const UNSURE: &str = "May have gone out: the server stopped answering once it had the whole message";

pub const MAX_ATTEMPTS: i64 = 5;
/// Seconds; doubles each attempt.
pub const BACKOFF_BASE: i64 = 20;
pub const BACKOFF_CAP: i64 = 900;
/// The grace window before the wire send, so Undo Send can cancel it, unless a person has set another
/// (`kernel.send_window.DEFAULT`).
pub const SEND_DELAY: i64 = 10;

/// What a queued send waits for (`outbox.wait_kind`).
pub const GRACE: &str = "grace";
pub const SCHEDULED: &str = "scheduled";
pub const BACKOFF: &str = "backoff";
/// A person's Retry.
pub const RETRIED: [Mark; 2] = [Mark::WireBegun(false), Mark::WaitKind(GRACE)];

/// What goes with a built message into the outbox (`SendMeta`).
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct SendMeta {
    pub account: String,
    pub idempotency_key: String,
    pub in_reply_to_pk: Option<i64>,
    pub orig_message_id: Option<String>,
    pub subject: String,
    pub draft_id: Option<i64>,
    pub send_after: i64,
}

/// The message a send replies to, as its row has it.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct OriginalRow {
    pub account: String,
    pub message_id: Option<String>,
    pub thread_id: Option<String>,
    pub message_key: Option<i64>,
}

/// The row a send is written as (`new_row`). `orig` is the row of the message it replies to, if any is left. `due`
/// is the end of the grace window: a later `send_after` moves it on.
pub fn new_row(built: &Built, meta: &SendMeta, orig: Option<&OriginalRow>, due: i64, created_at: &str) -> NewSend {
    let orig_account = match orig {
        Some(o) => Some(o.account.clone()),
        None => meta.orig_message_id.as_ref().filter(|m| !m.is_empty()).map(|_| meta.account.clone()),
    };
    NewSend {
        account: meta.account.clone(),
        idempotency_key: meta.idempotency_key.clone(),
        message_id: built.message_id.clone(),
        orig_account,
        orig_message_id: match orig {
            Some(o) => o.message_id.clone(),
            None => meta.orig_message_id.clone(),
        },
        orig_thread_id: orig.and_then(|o| o.thread_id.clone()),
        original_key: orig.and_then(|o| o.message_key),
        envelope: python_list(&built.envelope),
        recipients: format!(
            "{{\"to\": {}, \"cc\": {}, \"bcc\": {}}}",
            python_list(&built.to_disp),
            python_list(&built.cc_disp),
            python_list(&built.bcc_disp)
        ),
        subject: meta.subject.clone(),
        raw: built.raw.clone(),
        draft_id: meta.draft_id,
        next_attempt_at: due.max(meta.send_after),
        wait_kind: if meta.send_after > due { SCHEDULED } else { GRACE },
        created_at: created_at.to_owned(),
    }
}

/// A list of text as Python's `json.dumps` writes it by default: `", "` between items, and every character past
/// ASCII escaped.
pub fn python_list(items: &[String]) -> String {
    let mut out = String::from("[");
    for (i, item) in items.iter().enumerate() {
        if i > 0 {
            out.push_str(", ");
        }
        python_string(item, &mut out);
    }
    out.push(']');
    out
}

/// One string as `json.dumps` writes it.
pub fn python_string(text: &str, out: &mut String) {
    out.push('"');
    for c in text.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            c if !(' '..='~').contains(&c) => {
                let mut units = [0u16; 2];
                for unit in c.encode_utf16(&mut units) {
                    out.push_str(&format!("\\u{unit:04x}"));
                }
            }
            c => out.push(c),
        }
    }
    out.push('"');
}

/// The thread a reply's follow-up lands on: the original's as it stands, else the one it had when this was queued.
pub fn thread_of_reply(r: &OutboxRow, orig_thread: Option<&str>) -> Option<String> {
    orig_thread.filter(|t| !t.is_empty()).map(str::to_owned).or_else(|| r.orig_thread_id.clone())
}

/// The whole message went out and no answer came, so it may have been delivered (`is_unsure`).
pub fn is_unsure(r: &OutboxRow) -> bool {
    r.state == "error" && r.wire_begun && !r.delivered
}

/// What a try at the wire that didn't deliver leaves on its row (`FailedTry`).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct FailedTry {
    /// To error at once, or back in the queue after a back-off.
    pub give_up: bool,
    /// Scrubbed: it is stored and shown.
    pub error: &'static str,
    pub marks: Vec<Mark>,
    pub unsure: bool,
}

/// `unanswered`: it stays marked and only a person sends it again. Any other failure had an answer or never had the
/// whole message out, so the mark goes: `permanent` gives up, and the rest back off.
pub fn failed_try(unanswered: bool, permanent: bool, error: &'static str) -> FailedTry {
    if unanswered {
        return FailedTry { give_up: true, error: UNSURE, marks: Vec::new(), unsure: true };
    }
    FailedTry { give_up: permanent, error, marks: vec![Mark::WireBegun(false), Mark::WaitKind(BACKOFF)], unsure: false }
}

/// What a delivered send still owes, each until its own done-once flag is set (`Owed`).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Owed {
    /// A copy to save in Sent.
    pub sent_copy: bool,
    /// The server files sent mail itself: only the flag is left to set.
    pub sent_filed_for_us: bool,
    /// \Answered on the original.
    pub answered: bool,
    /// A follow-up on the thread it answers.
    pub followup: bool,
    /// The draft it was composed from, superseded now it is truly sent.
    pub draft: bool,
}

/// A brand-new message owes nothing of what a reply does (`owed`).
pub fn owed(r: &OutboxRow, saves_sent: bool) -> Owed {
    let replied_to = r.orig_message_id.as_deref().is_some_and(|m| !m.is_empty()) || r.original_key.unwrap_or(0) != 0;
    Owed {
        sent_copy: !saves_sent && !r.sent_appended,
        sent_filed_for_us: saves_sent && !r.sent_appended,
        answered: replied_to && !r.answered_set,
        followup: replied_to && !r.followup_recorded,
        draft: r.draft_id.unwrap_or(0) != 0 && !r.draft_deleted,
    }
}

/// The Message-ID to look for in Sent before saving a copy: none for a row delivered just now (`sent_copy_to_look_for`).
pub fn sent_copy_to_look_for(r: &OutboxRow) -> Option<&str> {
    if r.delivered { Some(&r.message_id) } else { None }
}

/// How many tries a delivered row's bookkeeping has had: they count from the delivery.
pub fn attempts_since_delivery(r: &OutboxRow) -> i64 {
    if r.delivered { r.attempts } else { 0 }
}

/// Seconds to wait after the `attempts`-th attempt failed (`DurableQueue.backoff`).
pub fn backoff(attempts: i64) -> i64 {
    let shift = u32::try_from((attempts - 1).clamp(0, 30)).unwrap_or(30);
    BACKOFF_CAP.min(BACKOFF_BASE << shift)
}

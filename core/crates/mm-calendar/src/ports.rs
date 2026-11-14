//! What the calendar needs from outside it. The store implements the stored mail's and the zone database's; the
//! server binds the rest.

use std::fmt;
use std::sync::Arc;

use mm_kernel::errors::{Failure, Fault};
use mm_kernel::zone::Zone;
use serde_json::Value;

/// The zone database: what `ZoneInfo(name)` finds.
pub trait ZoneNames: Send + Sync + fmt::Debug {
    /// The zone `name` names, None where the database holds no zone of that name or the name is no key of it.
    fn zone(&self, name: &str) -> Option<Arc<dyn Zone>>;
}

/// The person's zone, and the zone database a TZID is looked up in: what reads an invite's times.
#[derive(Clone, Copy, Debug)]
pub struct Here<'a> {
    pub zones: &'a dyn ZoneNames,
    pub person: &'a dyn Zone,
}

/// A message an invite came in, as Today & next names it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct InviteRow {
    pub pk: i64,
    pub key: Option<i64>,
    pub uid: Option<i64>,
    pub account: String,
    pub folder: String,
    pub thread_id: Option<String>,
}

/// Invite parts in stored bodies, never IMAP, and what the outbox has sent.
pub trait StoredInvites: Send + Sync + fmt::Debug {
    /// Every calendar part of mail whose body is stored and that came since `since` (seconds since the epoch), newest
    /// first, as its message and its text, leaving out mail in the folders `skipped` says and any part that can't be
    /// read.
    fn invite_parts(&self, since: i64, skipped: &dyn Fn(&str) -> bool) -> Result<Vec<(InviteRow, String)>, Fault>;
    /// The outbox's idempotency keys that begin with `prefix`, the newest first.
    fn keys_sent(&self, prefix: &str) -> Result<Vec<String>, Fault>;
}

/// The part a folder plays, as its name alone gives it (the Mailbox's `roles.role_by_name`).
pub trait FolderRoles: Send + Sync + fmt::Debug {
    fn role_by_name(&self, folder: &str) -> String;
}

/// A message an answer to an invite, or an event file, comes from.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Message {
    pub pk: i64,
    pub key: Option<i64>,
    pub account: String,
}

/// An account an answer goes from: its address and the name it sends under.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Me {
    pub email: String,
    pub alias: Option<String>,
}

/// What an answer to an invite needs of a message and its account, until the Mailbox and Accounts hold them.
pub trait Messages: Send + Sync + fmt::Debug {
    fn message(&self, pk: i64) -> Result<Option<Message>, Fault>;
    /// The account of this address, None where there is none.
    fn account(&self, email: &str) -> Result<Option<Me>, Fault>;
    /// The message's raw body, fetched where it isn't stored: `Unreachable` where it can't be.
    fn body(&self, pk: i64) -> Result<Vec<u8>, Failure>;
}

/// An answer to an invite, for the outbox: what `SystemSend.build` and `enqueue` take in the Python app.
#[derive(Clone, Debug, PartialEq)]
pub struct Reply {
    /// The message it answers.
    pub pk: i64,
    pub account: String,
    pub account_name: String,
    pub to: String,
    pub subject: String,
    pub text: String,
    /// The METHOD:REPLY calendar, sent as `invite.ics`.
    pub calendar: String,
    pub idempotency_key: String,
}

/// A message MailMind sends for itself: built, then queued in the outbox under its idempotency key.
pub trait SystemSend: Send + Sync + fmt::Debug {
    /// The outbox's id for it and whether this queued it (false: the same key was queued already), and how long it
    /// can still be undone: `{"outbox_id", "queued", "undo_seconds"}`.
    fn send(&self, reply: &Reply) -> Result<Value, Failure>;
}

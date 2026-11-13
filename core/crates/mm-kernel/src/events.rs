//! What the app tells the open tabs (`mailmind.platform.sse`): an event names one of a closed set of topics, and its
//! data carries ids and counts only, never a subject or an address in a message; a tab fetches the rest through the
//! routes. `Topic` is the whole contract the page listens to.
//!
//! A use case that tells the tabs something takes an `Arc<dyn Events>`; the server's hub is the one the app binds, and
//! numbers each event so a tab that reconnects misses nothing.

use std::fmt;

use serde_json::Value;

/// Every topic the app publishes.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Topic {
    /// A sync finished for an account.
    SyncDone,
    /// It brought new mail into INBOX.
    NewMail,
    /// Rows of mail gone from the server went, in `folder` if it names one.
    Reconcile,
    /// A sync found mail read, starred or answered elsewhere, in `folder`.
    FlagsChanged,
    /// The first scan of a folder ended (`ok`: whether it read the folder).
    FolderSynced,
    /// Snoozed mail came back.
    SnoozeWake,
    /// The follow-ups changed.
    Followup,
    /// A task someone confirmed was added, marked or removed.
    TasksChanged,
    /// A conversation was muted or unmuted.
    Mute,
    /// A reply draft written ahead is ready.
    DraftReady,
    /// A send went out, or a change was queued: what the outbox panel shows.
    Outbox,
    /// An undo finished.
    UndoDone,
    /// A queued move or flag change was applied or refused, retried or discarded.
    CommandsChanged,
}

impl Topic {
    /// The topic as the stream names it, which the page listens for.
    pub const fn name(self) -> &'static str {
        match self {
            Topic::SyncDone => "sync-done",
            Topic::NewMail => "new-mail",
            Topic::Reconcile => "reconcile",
            Topic::FlagsChanged => "flags-changed",
            Topic::FolderSynced => "folder-synced",
            Topic::SnoozeWake => "snooze-wake",
            Topic::Followup => "followup",
            Topic::TasksChanged => "tasks-changed",
            Topic::Mute => "mute",
            Topic::DraftReady => "draft-ready",
            Topic::Outbox => "outbox",
            Topic::UndoDone => "undo-done",
            Topic::CommandsChanged => "commands-changed",
        }
    }
}

/// Where the app tells the open tabs what changed.
pub trait Events: Send + Sync + fmt::Debug {
    /// Tell every open tab, and keep it for the tabs that reconnect: `data` is a JSON object of ids and counts.
    fn publish(&self, topic: Topic, data: Value);
}

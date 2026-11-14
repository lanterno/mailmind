//! What a sync asks of a person's filters (`ports.Curation`'s `arrival_flags` and `flags_queued`): mail it stored that
//! MailMind has never seen before is judged by them, and what they ask goes to the server as any flag change does.
//! Curation judges them; the program binds the two.

use std::fmt;

/// A person's filters, as new Inbox mail arrives.
pub trait ArrivalFilters: Send + Sync + fmt::Debug {
    /// The flags the filters a message just stored and indexed fits ask of it, as the flag columns name them
    /// (`seen`, `flagged`). Nothing where none fits, or where they couldn't be judged: no filter may stop a sync.
    fn arrival_flags(&self, pk: i64) -> Vec<(&'static str, bool)>;
    /// A sync queued what they asked: the queue's worker starts on it now, and the tray shows it.
    fn flags_queued(&self);
}

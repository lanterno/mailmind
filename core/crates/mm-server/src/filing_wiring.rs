//! What Attention and Curation ask of the Mailbox's filing, and what the Mailbox's Undo and sync ask of them
//! (`bootstrap.attention`'s and `.curation`'s Mailbox ports, and `bootstrap.mailbox`'s reversers): a mute or a woken
//! snooze tells the change queue, a block trashes the copy it names, and Undo takes back a snooze, a mute and a
//! cleaned-up sender.

use std::sync::Arc;

use mm_attention::muting::Muting;
use mm_attention::snoozing::Snoozing;
use mm_curation::cleanup_ports::NamedCopy;
use mm_curation::screener::Screener;
use mm_kernel::errors::{Failure, Fault};
use mm_kernel::log;
use mm_mailbox::filing::Filing;
use mm_mailbox::stats::LiveStats;
use mm_mailbox::sync_ports::AroundSync;
use mm_mailbox::undo::{Reversed, Reverser};
use mm_store::curation::CurationStore;
use serde_json::Value;

use crate::workers::Jobs;

/// What Attention queued on the change queue in its own block (a mute marking its thread read, a snooze waking read
/// mail unread), told to the Mailbox's filing: the worker starts on it, and the tray shows it.
#[derive(Debug)]
pub(crate) struct FilingFlags(pub(crate) Arc<Filing>);

impl mm_attention::ports::Flags for FilingFlags {
    fn flags_queued(&self) {
        self.0.flags_queued();
    }
}

/// What Curation asks of the Mailbox (`bootstrap.curation`'s `_Filing`): a blocked sender's copy trashed with nothing
/// for Undo; a cleanup's trashing, under its undo group, and the block's reversal on the undo stack beside it; and the
/// counts let go once a rule moved them.
#[derive(Debug)]
pub(crate) struct CurationFiling {
    pub(crate) filing: Arc<Filing>,
    pub(crate) stats: Arc<LiveStats>,
    /// Where the undo stack is kept.
    pub(crate) store: Arc<CurationStore>,
}

impl mm_curation::ports::Filing for CurationFiling {
    fn trash_now(&self, pk: i64) -> Result<bool, Fault> {
        self.filing.trash_now(pk)
    }

    fn trash(&self, pk: i64, group: &str) -> Result<Value, Failure> {
        self.filing.file("trash", pk, Some(group), None, None)
    }

    fn trash_many(&self, rows: &[NamedCopy], group: &str) -> Result<Value, Failure> {
        let rows: Vec<(NamedCopy, bool)> = rows.iter().map(|copy| (copy.clone(), false)).collect();
        self.filing.file_many("trash", &rows, Some(group), None)
    }

    fn undo_push(&self, entry: &Value, group: &str) -> Result<(), Fault> {
        self.store.push_undo(entry, group)
    }

    fn stats_changed(&self) {
        self.stats.invalidate();
    }
}

fn account(entry: &Value) -> Option<String> {
    entry.get("account").and_then(Value::as_str).map(str::to_owned)
}

/// A reversal that went, or one a refusal stopped, as Undo hears it; a fault goes on up.
fn reversed(entry: &Value, done: Result<(), Failure>) -> Result<Reversed, Fault> {
    match done {
        Ok(()) => Ok((true, account(entry), None)),
        Err(Failure::Problem(_)) => Ok((false, account(entry), None)),
        Err(Failure::Fault(fault)) => Err(fault),
    }
}

/// Undo of a snooze (`Snoozing.reverse`).
#[derive(Debug)]
pub(crate) struct SnoozeReverser(pub(crate) Arc<Snoozing>);

impl Reverser for SnoozeReverser {
    fn reverses(&self, kind: &str) -> bool {
        kind == "snooze"
    }

    fn reverse(&self, entry: &Value) -> Result<Reversed, Fault> {
        reversed(entry, self.0.reverse(entry))
    }
}

/// Undo of a mute (`Muting.reverse`).
#[derive(Debug)]
pub(crate) struct MuteReverser(pub(crate) Arc<Muting>);

impl Reverser for MuteReverser {
    fn reverses(&self, kind: &str) -> bool {
        kind == "mute"
    }

    fn reverse(&self, entry: &Value) -> Result<Reversed, Fault> {
        reversed(entry, self.0.reverse(entry))
    }
}

/// Undo of a sender cleaned up (`Cleanup.reverse_block`, an `unblock` entry): the rule goes, and what it decided is
/// classified again.
#[derive(Debug)]
pub(crate) struct UnblockReverser(pub(crate) Arc<Screener>);

impl Reverser for UnblockReverser {
    fn reverses(&self, kind: &str) -> bool {
        kind == "unblock"
    }

    fn reverse(&self, entry: &Value) -> Result<Reversed, Fault> {
        let token = entry.get("token").and_then(Value::as_str).unwrap_or_default();
        Ok((self.0.reverse_block(token), account(entry), None))
    }
}

/// What a sync round asks of the others (`Syncing`'s Attention, and its kick): what is snoozed and due wakes first,
/// and new mail kicks the job that triages it, where this process runs one.
#[derive(Debug)]
pub(crate) struct AroundRounds {
    pub(crate) snoozing: Arc<Snoozing>,
    pub(crate) jobs: Arc<Jobs>,
}

impl AroundSync for AroundRounds {
    fn wake_due(&self) {
        if let Err(failure) = self.snoozing.wake_due() {
            let class = match &failure {
                Failure::Fault(fault) => fault.class,
                Failure::Problem(_) => "Problem",
            };
            log::warning("snooze", "tick error", &[], Some(class));
        }
    }

    fn new_mail(&self) {
        self.jobs.kick("assess");
    }
}

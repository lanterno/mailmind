//! What a change to the mail needs of the world (`mailbox.application.ports` as `MailCommands`, `Filing`, `Undo`,
//! `Reversals`, `Syncing` and `Reconciler` use them): the accounts, and the store, a block of work at a time, as the
//! Python app's UnitOfWork gives it. The store (`mm-store`) and the program implement them.
//!
//! A block (`Tx`) is one transaction: what a use case reads in it decides what it writes, and no one comes between.
//! No block stays open across a network call: a use case commits before it asks a server anything.

use std::collections::BTreeMap;
use std::fmt;

use mm_kernel::errors::Fault;
use serde_json::Value;

use crate::command::{Command, Message};
use crate::destination::Provider;

/// An account, as a change to its mail needs it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct MoveAccount {
    pub email: String,
    /// Its Trash folder.
    pub trash: String,
    pub provider: Provider,
}

pub trait MoveAccounts: Send + Sync + fmt::Debug {
    /// The account, or None when the Directory knows no such one.
    fn move_account(&self, email: &str) -> Result<Option<MoveAccount>, Fault>;
    /// Every account the app may use, in the file's order.
    fn emails(&self) -> Result<Vec<String>, Fault>;
}

/// Names no one can guess: a request's when the client names none, an undo group's (`secrets.token_hex`).
pub trait Tokens: Send + Sync + fmt::Debug {
    /// `bytes` random bytes, in hex.
    fn hex(&self, bytes: usize) -> String;
}

/// A command's row as it is accepted.
#[derive(Clone, Debug, PartialEq)]
pub struct NewCommand<'a> {
    pub account: &'a str,
    pub idempotency_key: &'a str,
    pub kind: &'a str,
    pub message_id: &'a str,
    pub src_folder: &'a str,
    pub dest: Option<&'a str>,
    pub params: &'a Value,
    pub created_at: &'a str,
}

/// An entry of the undo stack, as stored.
#[derive(Clone, Debug, PartialEq)]
pub struct UndoRow {
    pub id: i64,
    pub group: Option<String>,
    pub entry: Value,
}

/// The store, inside one block of work.
pub trait Tx {
    // ── messages ──
    fn message(&self, pk: i64) -> Result<Option<Message>, Fault>;
    /// The newest row of a message by its Message-ID, in any folder.
    fn message_by_msgid(&self, account: &str, message_id: &str) -> Result<Option<Message>, Fault>;
    /// The pk of the newest copy of a message in `folder`, on its way out or not.
    fn newest_copy_in(&self, account: &str, folder: &str, message_id: &str) -> Result<Option<i64>, Fault>;
    /// Whether a copy of a message is in `folder`; with `listed`, one not on its way out.
    fn copy_in(&self, account: &str, folder: &str, message_id: &str, listed: bool) -> Result<bool, Fault>;
    /// The pks of a conversation's messages in `folder`, none on its way out, newest first.
    fn thread_pks_in(&self, account: &str, folder: &str, thread_id: &str) -> Result<Vec<i64>, Fault>;
    /// The pk the copy of a message in `folder` has now: the one at `uid`, or without one the only copy there.
    fn copy_pk(&self, key: i64, folder: &str, uid: Option<i64>) -> Result<Option<i64>, Fault>;
    /// {pk: (key, folder, uid)} for each of `pks` that has a row.
    fn names_of(&self, pks: &[i64]) -> Result<BTreeMap<i64, (i64, String, i64)>, Fault>;
    /// A message's row goes, and its identity once no copy is left, as of `now` (seconds).
    fn delete_message(&self, pk: i64, now: i64) -> Result<(), Fault>;
    fn set_local_flags(&self, pk: i64, flags: &[(String, bool)]) -> Result<(), Fault>;
    fn mark_pending_removal(&self, pk: i64, command: i64) -> Result<(), Fault>;
    /// The command `command` won't happen: its message is listed again. How many rows came back.
    fn restore_pending(&self, command: i64) -> Result<usize, Fault>;
    fn pending_removal_pks(&self, command: i64) -> Result<Vec<i64>, Fault>;
    fn uidvalidity(&self, account: &str, folder: &str) -> Result<Option<i64>, Fault>;

    // ── the command log ──
    /// (id, created): created is false when the key was seen already.
    fn insert_command(&self, new: &NewCommand<'_>) -> Result<(Option<i64>, bool), Fault>;
    fn command(&self, id: i64) -> Result<Option<Command>, Fault>;
    /// The newest move of a message, in any state.
    fn latest_move(&self, account: &str, message_id: &str) -> Result<Option<Command>, Fault>;
    fn set_begun(&self, id: i64, begun: bool) -> Result<(), Fault>;
    /// Where the message went, and what a reversal needs of the server's answer.
    fn record(&self, id: i64, dest: Option<&str>, outcome: Option<&Value>) -> Result<(), Fault>;
    fn set_params(&self, id: i64, params: &Value) -> Result<(), Fault>;
    /// An Undo took this move back.
    fn mark_reversed(&self, id: i64) -> Result<(), Fault>;
    /// The changes that haven't reached a server, newest first.
    fn open_commands(&self, limit: i64) -> Result<Vec<Command>, Fault>;
    fn open_counts(&self) -> Result<BTreeMap<String, i64>, Fault>;

    // ── the queue over the log (`DurableQueue`) ──
    /// The ids of queued rows due at `now`, oldest first.
    fn due(&self, now: i64) -> Result<Vec<i64>, Fault>;
    /// A queued row flipped to running, as it is now; None to every caller but the one that wins it.
    fn claim(&self, id: i64) -> Result<Option<Command>, Fault>;
    /// A running row back in the queue, due at `due`, no attempt counted.
    fn put_back(&self, id: i64, due: i64) -> Result<(), Fault>;
    /// An attempt failed: to `queued` or, when `terminal`, to `error`, with its attempts, when it is next due and why.
    fn attempt_failed(&self, id: i64, terminal: bool, attempts: i64, due: i64, error: &str) -> Result<(), Fault>;
    /// The server holds the change: it never runs again.
    fn mark_applied(&self, id: i64) -> Result<(), Fault>;
    fn finish(&self, id: i64, at: &str) -> Result<(), Fault>;
    /// Rows a crash left running go back to the queue.
    fn reset_running(&self) -> Result<(), Fault>;
    /// A row in error back in the queue, its attempts counted afresh; false when it isn't in error.
    fn requeue(&self, id: i64) -> Result<bool, Fault>;
    /// Delete a row whose change hasn't happened, claimed or not; false once it has.
    fn cancel_unapplied(&self, id: i64) -> Result<bool, Fault>;
    /// The soonest a queued row comes due after `after`.
    fn next_due(&self, after: i64) -> Result<Option<i64>, Fault>;

    // ── the undo stack ──
    /// Drop the entries made before `oldest_kept`.
    fn undo_expire(&self, oldest_kept: &str) -> Result<(), Fault>;
    /// Every entry, newest first.
    fn undo_rows(&self) -> Result<Vec<UndoRow>, Fault>;
    /// The newest entry of `group`, wherever it stands.
    fn undo_group_row(&self, group: &str) -> Result<Option<UndoRow>, Fault>;
    fn undo_insert(&self, group: Option<&str>, entry: &Value, at: &str) -> Result<(), Fault>;
    fn undo_update(&self, id: i64, entry: &Value) -> Result<(), Fault>;
    fn undo_delete(&self, id: i64) -> Result<(), Fault>;
    /// Keep only the newest `keep`.
    fn undo_keep(&self, keep: i64) -> Result<(), Fault>;
    /// The entries made since `oldest_kept`, oldest first.
    fn undo_entries(&self, oldest_kept: &str) -> Result<Vec<Value>, Fault>;

    // ── the sync's record and reconcile ──
    /// An account's INBOX sync outcome, on its health columns only: its error's class and kind, never its text.
    fn record_sync_result(&self, account: &str, failed: Option<(&str, &str)>, when: &str) -> Result<(), Fault>;
    fn clear_sync_error(&self, account: &str) -> Result<(), Fault>;
    /// Each folder a sync has stored for the account, INBOX first.
    fn synced_folders(&self, account: &str) -> Result<Vec<String>, Fault>;
    fn stored_uids(&self, account: &str, folder: &str) -> Result<Vec<i64>, Fault>;
    /// Prune `folder`'s rows at UIDs not `live` that are `among` those stored before; stamp survivors verified.
    /// How many went.
    fn prune(
        &self,
        account: &str,
        folder: &str,
        live: &[u32],
        among: &[i64],
        when: &str,
        now: i64,
    ) -> Result<usize, Fault>;
    /// Add each of `records` to an audit trail, in the order given.
    fn audit(&self, trail: &str, records: &[Value]) -> Result<(), Fault>;
}

/// The store, a block of work at a time.
pub trait CommandStore: Send + Sync + fmt::Debug {
    /// `work` in one write transaction: committed when it answers, rolled back when it fails.
    fn write(&self, work: &mut dyn FnMut(&dyn Tx) -> Result<(), Fault>) -> Result<(), Fault>;
    /// `work` on a connection of its own, for reading.
    fn read(&self, work: &mut dyn FnMut(&dyn Tx) -> Result<(), Fault>) -> Result<(), Fault>;
}

fn once<T>(
    run: impl FnOnce(&mut dyn FnMut(&dyn Tx) -> Result<(), Fault>) -> Result<(), Fault>,
    work: impl FnOnce(&dyn Tx) -> Result<T, Fault>,
) -> Result<T, Fault> {
    let mut work = Some(work);
    let mut answer = None;
    run(&mut |tx| {
        if let Some(work) = work.take() {
            answer = Some(work(tx)?);
        }
        Ok(())
    })?;
    answer.ok_or(Fault::new("NoAnswer"))
}

/// `work` in one write transaction, and what it answered.
pub fn write<T>(store: &dyn CommandStore, work: impl FnOnce(&dyn Tx) -> Result<T, Fault>) -> Result<T, Fault> {
    once(|w| store.write(w), work)
}

/// `work` on a connection for reading, and what it answered.
pub fn read<T>(store: &dyn CommandStore, work: impl FnOnce(&dyn Tx) -> Result<T, Fault>) -> Result<T, Fault> {
    once(|w| store.read(w), work)
}

//! One path for a change to the mail on a server, online or offline (`mailbox.application.commands`).
//!
//! Accept, in one transaction: the command's row, its message out of the lists (or its flags shown) and what Undo
//! will need. Submit: a try on the account's gate, which a click waits for only so long. Settle, under the same
//! gate: `applied`, what the server's answer left and the local rows commit together. A connection failure leaves
//! the row for the worker, which backs off and tries again, and a try that followed another asks the server first
//! (`ServerOps::apply`), so a change applies at most once.
//!
//! The queue is the Python app's DurableQueue over the mail_commands table: an atomic claim, the `applied` flag as
//! the exactly-once gate, backoff doubling from 20 seconds to 15 minutes, and rows a crash left running put back.

use std::sync::Arc;
use std::time::Duration;

use mm_kernel::errors::Fault;
use mm_kernel::log::{self, Field};
use mm_kernel::seams::Clock;
use mm_kernel::when;
use serde_json::{Value, json};

use crate::command::{self, Accepted, Command, Message, Outcome, ServerAnswer};
use crate::command_ports::{self, CommandStore, MoveAccounts, NewCommand, Tokens, Tx};
use crate::destination::{self, Destination};
use crate::gates::{Gates, Priority, Refused, Wait, on_gate};
use crate::server_ops::ServerOps;
use crate::session::ServerError;
use crate::sync_ports::{Events, MailboxEvent};
use crate::undo;

const TAG: &str = "commands";

/// Why a try at a command didn't come to an outcome.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TryFailed {
    Server(ServerError),
    Store(Fault),
}

impl TryFailed {
    pub fn class(self) -> &'static str {
        match self {
            TryFailed::Server(e) => e.class(),
            TryFailed::Store(fault) => fault.class,
        }
    }
}

impl From<Fault> for TryFailed {
    fn from(fault: Fault) -> TryFailed {
        TryFailed::Store(fault)
    }
}

impl From<ServerError> for TryFailed {
    fn from(error: ServerError) -> TryFailed {
        TryFailed::Server(error)
    }
}

/// What reverses a move as it is accepted: Undo's push, in the accepting block.
pub type PushUndo<'a> = &'a dyn Fn(&dyn Tx, &Value) -> Result<(), Fault>;

#[derive(Debug)]
pub struct MailCommands {
    store: Arc<dyn CommandStore>,
    accounts: Arc<dyn MoveAccounts>,
    server: ServerOps,
    gates: Arc<dyn Gates>,
    clock: Arc<dyn Clock>,
    events: Arc<dyn Events>,
    tokens: Arc<dyn Tokens>,
    /// This process only reads: a change waits here for a full run's worker.
    read_only: bool,
}

/// What wires MailCommands.
#[derive(Debug)]
pub struct CommandParts {
    pub store: Arc<dyn CommandStore>,
    pub accounts: Arc<dyn MoveAccounts>,
    pub server: ServerOps,
    pub gates: Arc<dyn Gates>,
    pub clock: Arc<dyn Clock>,
    pub events: Arc<dyn Events>,
    pub tokens: Arc<dyn Tokens>,
    pub read_only: bool,
}

impl MailCommands {
    /// About a day of tries, as the backoff goes: an outage isn't a reason to put mail back.
    pub const MAX_ATTEMPTS: i64 = 101;
    /// How long a click waits for the account's gate before the worker takes it over.
    pub const FILE_WAIT: Duration = Duration::from_millis(2500);
    /// Seconds a click's try waits for each answer: FILE_WAIT and this are all a hung server costs a click.
    pub const TRY_TIMEOUT: u64 = 12;
    /// Seconds the worker leaves a try that heard nothing: see `submit`.
    pub const AFTER_SILENCE: i64 = 60;
    const BACKOFF_BASE: i64 = 20;
    const BACKOFF_CAP: i64 = 900;

    pub fn new(parts: CommandParts) -> MailCommands {
        let CommandParts { store, accounts, server, gates, clock, events, tokens, read_only } = parts;
        MailCommands { store, accounts, server, gates, clock, events, tokens, read_only }
    }

    pub fn store(&self) -> &dyn CommandStore {
        &*self.store
    }

    pub fn server(&self) -> &ServerOps {
        &self.server
    }

    fn now(&self) -> i64 {
        self.clock.now().whole_seconds()
    }

    fn now_iso(&self) -> String {
        when::iso(self.clock.now())
    }

    /// Seconds to wait after the `attempts`-th attempt failed.
    fn backoff(attempts: i64) -> i64 {
        let doubled = 2_i64.checked_pow(u32::try_from(attempts.max(1) - 1).unwrap_or(u32::MAX)).unwrap_or(i64::MAX);
        Self::BACKOFF_CAP.min(Self::BACKOFF_BASE.saturating_mul(doubled))
    }

    /// The client's mutation_id names one request, so a resubmit of it dedupes; without one, every request is its
    /// own.
    fn request_id(&self, mutation_id: Option<&str>) -> String {
        match mutation_id {
            Some(named) if command::names_a_request(Some(named)) => named.to_owned(),
            _ => self.tokens.hex(16),
        }
    }

    /// The new command's row, claimed for a try now; None when this process only reads.
    fn claim_new(&self, tx: &dyn Tx, id: i64) -> Result<Option<Command>, Fault> {
        if self.read_only {
            return Ok(None);
        }
        tx.set_begun(id, true)?;
        tx.claim(id)
    }

    /// Where a move goes, as far as that is known with no server: kept for an Undo before it lands.
    fn known_destination(&self, kind: &str, email: &str) -> Result<Destination, Fault> {
        Ok(match self.accounts.move_account(email)? {
            Some(account) => {
                destination::known_without_server(kind, account.provider.archive_is_label_removal, Some(&account.trash))
            }
            // The account is gone, which a try finds out.
            None => Destination::default(),
        })
    }

    /// Accept a move of message `pk` in the caller's block, which commits it with whatever else it accepts: None
    /// when there is no such message, it is on its way out already or it is in the folder `to` names already. `to`
    /// is a folder a person picked, for a `move`. `push_undo` keeps what reverses it; without one, nothing will.
    /// Submit only once the block has committed.
    pub fn accept_move_within(
        &self,
        tx: &dyn Tx,
        kind: &str,
        pk: i64,
        mutation_id: Option<&str>,
        push_undo: Option<PushUndo<'_>>,
        to: Option<&str>,
    ) -> Result<Option<Accepted>, Fault> {
        let row = tx.message(pk)?;
        let Some(row) = row.filter(|r| command::takes_a_move(Some(r), to)) else { return Ok(None) };
        let (email, mid) = (row.account.clone(), row.mid());
        let place = match to {
            Some(to) => Destination::folder(Some(to.to_owned())),
            None => self.known_destination(kind, &email)?,
        };
        let params = command::move_params(pk, &row, tx.uidvalidity(&email, &row.folder)?);
        let key = command::idempotency_key(kind, &email, &mid, pk, &self.request_id(mutation_id));
        let (id, created) = tx.insert_command(&NewCommand {
            account: &email,
            idempotency_key: &key,
            kind,
            message_id: &mid,
            src_folder: &row.folder,
            dest: place.folder.as_deref(),
            params: &params,
            created_at: &self.now_iso(),
        })?;
        let Some(id) = id.filter(|_| created) else {
            // A resubmit of the same request.
            return Ok(Some(Accepted { id, account: email, created: false, undoable: false, claimed: None }));
        };
        tx.mark_pending_removal(pk, id)?;
        let undoable = !mid.is_empty() && push_undo.is_some();
        if let (false, Some(push)) = (mid.is_empty(), push_undo) {
            push(tx, &command::undo_entry(kind, id, &row, &mid, &place))?;
        }
        let claimed = self.claim_new(tx, id)?;
        Ok(Some(Accepted { id: Some(id), account: email, created: true, undoable, claimed }))
    }

    /// Accept a move of message `pk` in a block of its own.
    pub fn accept_move(
        &self,
        kind: &str,
        pk: i64,
        mutation_id: Option<&str>,
        push_undo: Option<PushUndo<'_>>,
        to: Option<&str>,
    ) -> Result<Option<Accepted>, Fault> {
        command_ports::write(&*self.store, |tx| self.accept_move_within(tx, kind, pk, mutation_id, push_undo, to))
    }

    /// A flag change on message `row`, for the server, shown on the row at once, in the caller's block. The command
    /// keeps the flags it replaced, which a refusal, or one given up on, puts back.
    fn flag_command(
        &self,
        tx: &dyn Tx,
        row: &Message,
        flags: &[(&str, bool)],
        request: &str,
    ) -> Result<(Option<i64>, bool), Fault> {
        let mid = row.mid();
        let params = command::flag_params(row, tx.uidvalidity(&row.account, &row.folder)?, flags);
        let key = command::idempotency_key("flag", &row.account, &mid, row.pk, request);
        let (id, created) = tx.insert_command(&NewCommand {
            account: &row.account,
            idempotency_key: &key,
            kind: "flag",
            message_id: &mid,
            src_folder: &row.folder,
            dest: None,
            params: &params,
            created_at: &self.now_iso(),
        })?;
        if created {
            let shown: Vec<(String, bool)> = flags.iter().map(|(k, v)| ((*k).to_owned(), *v)).collect();
            tx.set_local_flags(row.pk, &shown)?;
        }
        Ok((id, created))
    }

    /// Accept a flag change on message `pk`, which shows on its row at once: None when there is no such message.
    pub fn accept_flags(
        &self,
        pk: i64,
        flags: &[(&str, bool)],
        mutation_id: Option<&str>,
    ) -> Result<Option<Accepted>, Fault> {
        let request = self.request_id(mutation_id);
        command_ports::write(&*self.store, |tx| {
            let Some(row) = tx.message(pk)? else { return Ok(None) };
            let (id, created) = self.flag_command(tx, &row, flags, &request)?;
            let Some(id) = id.filter(|_| created) else {
                return Ok(Some(Accepted { id, account: row.account, created: false, undoable: false, claimed: None }));
            };
            let claimed = self.claim_new(tx, id)?;
            Ok(Some(Accepted { id: Some(id), account: row.account, created: true, undoable: false, claimed }))
        })
    }

    // ── a try, and what settles it ──

    /// The local row a flag change is for: the one in the folder it was asked in, else the newest with its
    /// Message-ID, else the row it was asked on.
    fn flagged_row(tx: &dyn Tx, row: &Command) -> Result<Option<i64>, Fault> {
        if !row.message_id.is_empty() {
            if let Some(pk) = tx.newest_copy_in(&row.account, &row.src_folder, &row.message_id)? {
                return Ok(Some(pk));
            }
            return Ok(tx.message_by_msgid(&row.account, &row.message_id)?.map(|m| m.pk));
        }
        match row.param("pk").and_then(Value::as_i64) {
            Some(pk) => Ok(tx.message(pk)?.map(|m| m.pk)),
            None => Ok(None),
        }
    }

    /// What the server now holds, on the local rows. A move drops the rows it marked pending_removal or, when there
    /// are none, the source folder's row by Message-ID; a flag change sets the flags on its row. Running it again
    /// changes nothing.
    fn local_mirror(&self, tx: &dyn Tx, row: &Command) -> Result<(), Fault> {
        if row.kind == "flag" {
            if let Some(pk) = Self::flagged_row(tx, row)? {
                tx.set_local_flags(pk, &command::flags_of(row.param("flags")))?;
            }
            return Ok(());
        }
        let mut pks = tx.pending_removal_pks(row.id)?;
        if pks.is_empty() && !row.message_id.is_empty() {
            pks.extend(tx.newest_copy_in(&row.account, &row.src_folder, &row.message_id)?);
        }
        let now = self.now();
        for pk in pks {
            tx.delete_message(pk, now)?;
        }
        Ok(())
    }

    /// The server holds the change: `applied`, what its answer left and the local rows, in one transaction, while
    /// the gate still keeps out a cancel.
    fn settle(&self, row: &Command, answer: &ServerAnswer) -> Result<(), Fault> {
        let at = self.now_iso();
        command_ports::write(&*self.store, |tx| {
            tx.mark_applied(row.id)?;
            let outcome = answer.unseen.then(|| json!({"unseen": true}));
            tx.record(row.id, answer.dest.as_deref(), outcome.as_ref())?;
            self.local_mirror(tx, row)?;
            tx.finish(row.id, &at)
        })
    }

    /// One try, as a job on the account's gate: applied, cancelled or an outcome that won't happen. The row is read
    /// again here, since an Undo or a Discard may have deleted it while this waited. A row with `applied` set has
    /// only its bookkeeping left.
    fn try_on_gate(&self, id: i64, just_accepted: bool) -> Result<Outcome, TryFailed> {
        let Some(fresh) = command_ports::read(&*self.store, |tx| tx.command(id))? else {
            return Ok(Outcome::Cancelled);
        };
        let mut answer = ServerAnswer::default();
        if !fresh.applied {
            let Some(account) = self.accounts.move_account(&fresh.account)? else {
                return Ok(Outcome::Removed);
            };
            let first_try = command::is_first_try(just_accepted, fresh.begun);
            if !fresh.begun {
                command_ports::write(&*self.store, |tx| tx.set_begun(id, true))?;
            }
            let timeout = just_accepted.then_some(Self::TRY_TIMEOUT);
            answer = self.server.apply(&fresh, &account, first_try, timeout)?;
            if let Some(wont) = answer.wont {
                return Ok(wont);
            }
        }
        self.settle(&fresh, &answer)?;
        Ok(Outcome::Applied)
    }

    /// A try now, for the person waiting: what became of the command. It waits for the gate only FILE_WAIT, and for
    /// each answer only TRY_TIMEOUT, and a server that can't be reached leaves the command for the worker. A try that
    /// heard nothing may have left a command running on the server, so the worker waits AFTER_SILENCE before asking
    /// it. A change that won't happen leaves nothing behind, since whoever is waiting hears of it now.
    pub fn submit(&self, accepted: &Accepted) -> Result<Outcome, Fault> {
        let Some(row) = &accepted.claimed else { return Ok(Outcome::Queued) };
        let id = row.id;
        let tried = on_gate(&*self.gates, &row.account, Priority::Interactive, Wait::For(Self::FILE_WAIT), || {
            self.try_on_gate(id, true)
        });
        let outcome = match tried {
            Err(Refused::Busy) => {
                // It never ran.
                command_ports::write(&*self.store, |tx| {
                    tx.put_back(id, 0)?;
                    tx.set_begun(id, false)
                })?;
                return Ok(Outcome::Waiting);
            }
            Err(Refused::Nested) => {
                command_ports::write(&*self.store, |tx| tx.put_back(id, 0))?;
                return Err(Fault::new("NestedGate"));
            }
            Ok(Err(TryFailed::Server(ServerError::Unreachable(class)))) => {
                // A try that heard nothing may still be going on over there.
                let due = if class == "Timeout" { self.now() + Self::AFTER_SILENCE } else { 0 };
                command_ports::write(&*self.store, |tx| tx.put_back(id, due))?;
                return Ok(Outcome::Queued);
            }
            Ok(Err(other)) => {
                // No answer to give, and a hidden message mustn't wait for a restart.
                command_ports::write(&*self.store, |tx| tx.put_back(id, 0))?;
                return Err(match other {
                    TryFailed::Store(fault) => fault,
                    TryFailed::Server(e) => Fault::new(e.class()),
                });
            }
            Ok(Ok(outcome)) => outcome,
        };
        if outcome.wont_happen() {
            command_ports::write(&*self.store, |tx| self.forget(tx, row))?;
        }
        Ok(outcome)
    }

    /// No one waits for a try at this one, as a click on many gives up in the end: the worker has it.
    pub fn leave_to_worker(&self, accepted: &Accepted) -> Result<Outcome, Fault> {
        let Some(row) = &accepted.claimed else { return Ok(Outcome::Queued) };
        command_ports::write(&*self.store, |tx| {
            tx.put_back(row.id, 0)?;
            tx.set_begun(row.id, false)
        })?;
        Ok(Outcome::Waiting)
    }

    /// The move `row` won't happen, so there is nothing to reverse: the newest entry for it goes.
    fn forget_undo(tx: &dyn Tx, row: &Command) -> Result<(), Fault> {
        if row.kind == "flag" || row.message_id.is_empty() {
            return Ok(());
        }
        undo::forget(tx, |entry| command::reverses(entry, row))
    }

    /// The change someone is waiting on won't happen, and they hear so now: as if it was never asked.
    fn forget(&self, tx: &dyn Tx, row: &Command) -> Result<(), Fault> {
        Self::give_back(tx, row)?;
        Self::forget_undo(tx, row)?;
        tx.cancel_unapplied(row.id).map(|_| ())
    }

    /// The change `row` won't happen: a move's message is listed again, and a flag change's row gets back the
    /// flags it had when the change was asked for.
    fn give_back(tx: &dyn Tx, row: &Command) -> Result<(), Fault> {
        tx.restore_pending(row.id)?;
        if row.kind == "flag" {
            let was = command::flags_of(row.param("was"));
            if let (Some(pk), false) = (Self::flagged_row(tx, row)?, was.is_empty()) {
                tx.set_local_flags(pk, &was)?;
            }
        }
        Ok(())
    }

    /// The given-up change `row` is asked for again: as when it was accepted, a move's message leaves the lists and a
    /// flag change shows, its `was` now the flags it replaces this time.
    fn take_up(tx: &dyn Tx, row: &Command) -> Result<(), Fault> {
        if row.kind == "flag" {
            let Some(found) = Self::flagged_row(tx, row)? else { return Ok(()) };
            let Some(now) = tx.message(found)? else { return Ok(()) };
            let flags = command::flags_of(row.param("flags"));
            let named: Vec<(&str, bool)> = flags.iter().map(|(k, v)| (k.as_str(), *v)).collect();
            let mut params = row.params.clone();
            params["was"] = command::held_now(&now, &named);
            tx.set_params(row.id, &params)?;
            return tx.set_local_flags(now.pk, &flags);
        }
        let found = if !row.message_id.is_empty() {
            tx.newest_copy_in(&row.account, &row.src_folder, &row.message_id)?
        } else {
            match row.param("pk").and_then(Value::as_i64) {
                Some(pk) => tx.message(pk)?.map(|m| m.pk),
                None => None,
            }
        };
        if let Some(pk) = found {
            tx.mark_pending_removal(pk, row.id)?;
        }
        Ok(())
    }

    // ── the tray ──

    /// Ask again for a change given up on. False when it isn't one.
    pub fn retry(&self, id: i64) -> Result<bool, Fault> {
        command_ports::write(&*self.store, |tx| {
            let Some(row) = tx.command(id)? else { return Ok(false) };
            if !tx.requeue(id)? {
                return Ok(false);
            }
            Self::take_up(tx, &row)?;
            Ok(true)
        })
    }

    /// Drop a change that hasn't reached a server: its message is as the server has it again, and there is nothing
    /// for Undo to reverse. The row, or None when it had reached one or is gone. The caller holds the account's
    /// gate, so a try at it is over or hasn't begun.
    pub fn discard(&self, id: i64) -> Result<Option<Command>, Fault> {
        command_ports::write(&*self.store, |tx| {
            let Some(row) = tx.command(id)? else { return Ok(None) };
            if !tx.cancel_unapplied(id)? {
                return Ok(None);
            }
            Self::give_back(tx, &row)?;
            Self::forget_undo(tx, &row)?;
            Ok(Some(row))
        })
    }

    /// A change that won't happen, and no one is waiting to hear: to error, for the tray, and the local row as the
    /// server has it.
    fn restore(&self, row: &Command, why: &str) -> Result<(), Fault> {
        command_ports::write(&*self.store, |tx| {
            tx.attempt_failed(row.id, true, row.attempts + 1, 0, why)?;
            Self::give_back(tx, row)
        })
    }

    // ── the worker ──

    /// One queued command, tried as the worker tries it.
    pub fn apply_one(&self, id: i64) -> Result<(), Fault> {
        let Some(row) = command_ports::write(&*self.store, |tx| tx.claim(id))? else {
            // Lost the claim: cancelled, or another worker has it.
            return Ok(());
        };
        let id_text = id.to_string();
        let tried =
            on_gate(&*self.gates, &row.account, Priority::AfterUser, Wait::Ever, || self.try_on_gate(id, false));
        let outcome = match tried {
            Ok(Ok(outcome)) => outcome,
            failed => {
                // A connection failure or anything else: a hidden message can't wait for a restart.
                let class = match failed {
                    Ok(Err(e)) => e.class(),
                    Err(Refused::Busy) => "Busy",
                    _ => "NestedGate",
                };
                let attempts = row.attempts + 1;
                let terminal = attempts >= Self::MAX_ATTEMPTS;
                let delay = Self::backoff(attempts);
                let now = self.now();
                command_ports::write(&*self.store, |tx| {
                    tx.attempt_failed(id, terminal, attempts, now + delay, class)?;
                    if terminal { Self::give_back(tx, &row) } else { Ok(()) }
                })?;
                if terminal {
                    self.events.publish(MailboxEvent::Restored { id, account: row.account.clone() });
                    log::warning(
                        TAG,
                        "#%(id)s unreachable (%(why)s); giving up",
                        &[(Field::Id, &id_text), (Field::Why, class)],
                        None,
                    );
                } else {
                    log::warning(
                        TAG,
                        "#%(id)s unreachable (%(why)s); retry %(count)ss",
                        &[(Field::Id, &id_text), (Field::Why, class), (Field::Count, &delay.to_string())],
                        None,
                    );
                }
                return Ok(());
            }
        };
        if outcome.wont_happen() {
            let why = command::why_given_up(outcome);
            self.restore(&row, why)?;
            self.events.publish(MailboxEvent::Restored { id, account: row.account.clone() });
            log::warning(
                TAG,
                "#%(id)s won't apply (%(why)s); its message is listed again",
                &[(Field::Id, &id_text), (Field::Why, why)],
                None,
            );
        } else if outcome == Outcome::Applied {
            self.events.publish(MailboxEvent::Applied { id, account: row.account.clone() });
            log::info(TAG, "applied #%(id)s %(what)s", &[(Field::Id, &id_text), (Field::What, &row.kind)]);
        }
        Ok(())
    }

    /// Each queued command that is due, oldest first; one that fails is logged and never stops the rest. A read-only
    /// process leaves them for a full run.
    pub fn drain(&self) -> Result<(), Fault> {
        if self.read_only {
            return Ok(());
        }
        let now = self.now();
        for id in command_ports::read(&*self.store, |tx| tx.due(now))? {
            if let Err(fault) = self.apply_one(id) {
                log::error(TAG, "apply_one #%(id)s errored", &[(Field::Id, &id.to_string())], Some(fault.class));
            }
        }
        Ok(())
    }

    /// At boot: rows a crash left running go back to the queue.
    pub fn recover(&self) -> Result<(), Fault> {
        command_ports::write(&*self.store, |tx| tx.reset_running())
    }
}

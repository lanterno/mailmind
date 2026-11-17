//! The change queue's tries, as its worker and a click make them, when one panics: it is an attempt that failed
//! (`Panic`), so its row never stays claimed until a restart with its message hidden; it is tried again after its
//! back-off, its message is listed again at its last attempt, and the rest of the pass still applies. A world of
//! stand-ins; every address is fictional.

#![allow(clippy::unwrap_used)]

use std::collections::BTreeMap;
use std::sync::atomic::{AtomicI64, Ordering};
use std::sync::{Arc, Mutex};

use mm_kernel::errors::{Fault, PANIC, READ_ONLY};
use mm_kernel::seams::{Clock, Moment};
use mm_mailbox::command::{Accepted, Command, Message};
use mm_mailbox::command_ports::{CommandStore, MoveAccount, MoveAccounts, NewCommand, Tokens, Tx, UndoRow};
use mm_mailbox::commands::{CommandParts, MailCommands};
use mm_mailbox::gates::{Gates, Priority, Refused, Wait};
use mm_mailbox::server_ops::ServerOps;
use mm_mailbox::session::{MailServer, MailServers, ServerError};
use mm_mailbox::sync_ports::{Events, MailboxEvent};
use serde_json::{Value, json};

/// An account whose change panics as it is tried, as a malformed answer could make it.
const PANICS: &str = "panics@example.test";
const ADA: &str = "ada@example.test";
const NOW: i64 = 1_793_620_800;

#[derive(Debug, Default)]
struct World {
    rows: Mutex<Vec<Command>>,
    /// The messages each change hid from the lists, by its id.
    hidden: Mutex<BTreeMap<i64, Vec<i64>>>,
    /// The changes whose messages were listed again.
    listed_again: Mutex<Vec<i64>>,
    /// The messages a change that applied took out of the store.
    deleted: Mutex<Vec<i64>>,
    said: Mutex<Vec<MailboxEvent>>,
    /// The clock, in seconds.
    now: AtomicI64,
}

impl World {
    fn new() -> Arc<World> {
        Arc::new(World { now: AtomicI64::new(NOW), ..World::default() })
    }

    /// A change to Trash, its message `pk` hidden from the lists as it was accepted. One `applied` has reached its
    /// server already and has only its bookkeeping left.
    fn trash(&self, id: i64, account: &str, pk: i64, applied: bool, attempts: i64) {
        self.rows.lock().unwrap().push(Command {
            id,
            account: account.to_owned(),
            kind: "trash".to_owned(),
            message_id: format!("<{id}@example.test>"),
            src_folder: "INBOX".to_owned(),
            dest: None,
            params: json!({}),
            state: "queued".to_owned(),
            attempts,
            next_attempt_at: 0,
            applied,
            begun: applied,
            outcome: json!({}),
            last_error: None,
            created_at: "2026-11-16T09:00:00Z".to_owned(),
        });
        self.hidden.lock().unwrap().insert(id, vec![pk]);
    }

    fn row(&self, id: i64) -> Command {
        self.rows.lock().unwrap().iter().find(|r| r.id == id).unwrap().clone()
    }

    fn change(&self, id: i64, change: impl FnOnce(&mut Command)) {
        if let Some(row) = self.rows.lock().unwrap().iter_mut().find(|r| r.id == id) {
            change(row);
        }
    }

    fn said(&self) -> Vec<MailboxEvent> {
        std::mem::take(&mut *self.said.lock().unwrap())
    }
}

impl CommandStore for World {
    fn write(&self, work: &mut dyn FnMut(&dyn Tx) -> Result<(), Fault>) -> Result<(), Fault> {
        work(self)
    }

    fn read(&self, work: &mut dyn FnMut(&dyn Tx) -> Result<(), Fault>) -> Result<(), Fault> {
        work(self)
    }
}

/// Only what the worker's try and a click's ask of a block of work.
impl Tx for World {
    fn command(&self, id: i64) -> Result<Option<Command>, Fault> {
        Ok(self.rows.lock().unwrap().iter().find(|r| r.id == id).cloned())
    }

    fn due(&self, now: i64) -> Result<Vec<i64>, Fault> {
        let rows = self.rows.lock().unwrap();
        Ok(rows.iter().filter(|r| r.state == "queued" && r.next_attempt_at <= now).map(|r| r.id).collect())
    }

    fn claim(&self, id: i64) -> Result<Option<Command>, Fault> {
        let mut rows = self.rows.lock().unwrap();
        let Some(row) = rows.iter_mut().find(|r| r.id == id && r.state == "queued") else { return Ok(None) };
        "running".clone_into(&mut row.state);
        Ok(Some(row.clone()))
    }

    fn put_back(&self, id: i64, due: i64) -> Result<(), Fault> {
        self.change(id, |r| {
            if r.state == "running" {
                "queued".clone_into(&mut r.state);
                r.next_attempt_at = due;
            }
        });
        Ok(())
    }

    fn attempt_failed(&self, id: i64, terminal: bool, attempts: i64, due: i64, error: &str) -> Result<(), Fault> {
        self.change(id, |r| {
            r.state = if terminal { "error" } else { "queued" }.to_owned();
            (r.attempts, r.next_attempt_at, r.last_error) = (attempts, due, Some(error.to_owned()));
        });
        Ok(())
    }

    fn restore_pending(&self, command: i64) -> Result<usize, Fault> {
        let listed = self.hidden.lock().unwrap().remove(&command).map_or(0, |pks| pks.len());
        self.listed_again.lock().unwrap().push(command);
        Ok(listed)
    }

    fn pending_removal_pks(&self, command: i64) -> Result<Vec<i64>, Fault> {
        Ok(self.hidden.lock().unwrap().get(&command).cloned().unwrap_or_default())
    }

    fn delete_message(&self, pk: i64, _now: i64) -> Result<(), Fault> {
        self.deleted.lock().unwrap().push(pk);
        Ok(())
    }

    fn mark_applied(&self, id: i64) -> Result<(), Fault> {
        self.change(id, |r| r.applied = true);
        Ok(())
    }

    fn record(&self, _id: i64, _dest: Option<&str>, _outcome: Option<&Value>) -> Result<(), Fault> {
        Ok(())
    }

    fn finish(&self, id: i64, _at: &str) -> Result<(), Fault> {
        self.change(id, |r| {
            "done".clone_into(&mut r.state);
            r.last_error = None;
        });
        Ok(())
    }

    fn message(&self, _pk: i64) -> Result<Option<Message>, Fault> {
        unreachable!("the worker's try never asks it")
    }

    fn message_by_msgid(&self, _account: &str, _message_id: &str) -> Result<Option<Message>, Fault> {
        unreachable!("the worker's try never asks it")
    }

    fn newest_copy_in(&self, _account: &str, _folder: &str, _message_id: &str) -> Result<Option<i64>, Fault> {
        unreachable!("the worker's try never asks it")
    }

    fn copy_in(&self, _account: &str, _folder: &str, _message_id: &str, _listed: bool) -> Result<bool, Fault> {
        unreachable!("the worker's try never asks it")
    }

    fn thread_pks_in(&self, _account: &str, _folder: &str, _thread_id: &str) -> Result<Vec<i64>, Fault> {
        unreachable!("the worker's try never asks it")
    }

    fn copy_pk(&self, _key: i64, _folder: &str, _uid: Option<i64>) -> Result<Option<i64>, Fault> {
        unreachable!("the worker's try never asks it")
    }

    fn names_of(&self, _pks: &[i64]) -> Result<BTreeMap<i64, (i64, String, i64)>, Fault> {
        unreachable!("the worker's try never asks it")
    }

    fn set_local_flags(&self, _pk: i64, _flags: &[(String, bool)]) -> Result<(), Fault> {
        unreachable!("the worker's try never asks it")
    }

    fn mark_pending_removal(&self, _pk: i64, _command: i64) -> Result<(), Fault> {
        unreachable!("the worker's try never asks it")
    }

    fn uidvalidity(&self, _account: &str, _folder: &str) -> Result<Option<i64>, Fault> {
        unreachable!("the worker's try never asks it")
    }

    fn insert_command(&self, _new: &NewCommand<'_>) -> Result<(Option<i64>, bool), Fault> {
        unreachable!("the worker's try never asks it")
    }

    fn latest_move(&self, _account: &str, _message_id: &str) -> Result<Option<Command>, Fault> {
        unreachable!("the worker's try never asks it")
    }

    fn set_begun(&self, _id: i64, _begun: bool) -> Result<(), Fault> {
        unreachable!("the worker's try never asks it")
    }

    fn set_params(&self, _id: i64, _params: &Value) -> Result<(), Fault> {
        unreachable!("the worker's try never asks it")
    }

    fn mark_reversed(&self, _id: i64) -> Result<(), Fault> {
        unreachable!("the worker's try never asks it")
    }

    fn open_commands(&self, _limit: i64) -> Result<Vec<Command>, Fault> {
        unreachable!("the worker's try never asks it")
    }

    fn open_counts(&self) -> Result<BTreeMap<String, i64>, Fault> {
        unreachable!("the worker's try never asks it")
    }

    fn reset_running(&self) -> Result<(), Fault> {
        unreachable!("the worker's try never asks it")
    }

    fn requeue(&self, _id: i64) -> Result<bool, Fault> {
        unreachable!("the worker's try never asks it")
    }

    fn cancel_unapplied(&self, _id: i64) -> Result<bool, Fault> {
        unreachable!("the worker's try never asks it")
    }

    fn next_due(&self, _after: i64) -> Result<Option<i64>, Fault> {
        unreachable!("the worker's try never asks it")
    }

    fn counts(&self) -> Result<BTreeMap<String, i64>, Fault> {
        unreachable!("the worker's try never asks it")
    }

    fn undo_expire(&self, _oldest_kept: &str) -> Result<(), Fault> {
        unreachable!("the worker's try never asks it")
    }

    fn undo_rows(&self) -> Result<Vec<UndoRow>, Fault> {
        unreachable!("the worker's try never asks it")
    }

    fn undo_group_row(&self, _group: &str) -> Result<Option<UndoRow>, Fault> {
        unreachable!("the worker's try never asks it")
    }

    fn undo_insert(&self, _group: Option<&str>, _entry: &Value, _at: &str) -> Result<(), Fault> {
        unreachable!("the worker's try never asks it")
    }

    fn undo_update(&self, _id: i64, _entry: &Value) -> Result<(), Fault> {
        unreachable!("the worker's try never asks it")
    }

    fn undo_delete(&self, _id: i64) -> Result<(), Fault> {
        unreachable!("the worker's try never asks it")
    }

    fn undo_keep(&self, _keep: i64) -> Result<(), Fault> {
        unreachable!("the worker's try never asks it")
    }

    fn undo_entries(&self, _oldest_kept: &str) -> Result<Vec<Value>, Fault> {
        unreachable!("the worker's try never asks it")
    }

    fn record_sync_result(&self, _account: &str, _failed: Option<(&str, &str)>, _when: &str) -> Result<(), Fault> {
        unreachable!("the worker's try never asks it")
    }

    fn clear_sync_error(&self, _account: &str) -> Result<(), Fault> {
        unreachable!("the worker's try never asks it")
    }

    fn synced_folders(&self, _account: &str) -> Result<Vec<String>, Fault> {
        unreachable!("the worker's try never asks it")
    }

    fn stored_uids(&self, _account: &str, _folder: &str) -> Result<Vec<i64>, Fault> {
        unreachable!("the worker's try never asks it")
    }

    fn prune(
        &self,
        _account: &str,
        _folder: &str,
        _live: &[u32],
        _among: &[i64],
        _when: &str,
        _now: i64,
    ) -> Result<usize, Fault> {
        unreachable!("the worker's try never asks it")
    }

    fn audit(&self, _trail: &str, _records: &[Value]) -> Result<(), Fault> {
        unreachable!("the worker's try never asks it")
    }
}

impl MoveAccounts for World {
    fn move_account(&self, email: &str) -> Result<Option<MoveAccount>, Fault> {
        assert_ne!(email, PANICS, "a panic whose words could quote mail");
        unreachable!("only a change that has reached its server is tried here")
    }

    fn emails(&self) -> Result<Vec<String>, Fault> {
        Ok(vec![PANICS.to_owned(), ADA.to_owned()])
    }
}

impl MailServers for World {
    fn open(&self, _account: &str, _timeout_seconds: u64) -> Result<Box<dyn MailServer>, ServerError> {
        unreachable!("no change here reaches a server")
    }
}

/// Every account's gate, always free.
impl Gates for World {
    fn run(&self, _account: &str, _priority: Priority, _wait: Wait, job: &mut dyn FnMut()) -> Result<(), Refused> {
        job();
        Ok(())
    }

    fn held(&self, _account: &str) -> bool {
        false
    }
}

impl Clock for World {
    fn now(&self) -> Moment {
        Moment::from_micros(self.now.load(Ordering::SeqCst) * 1_000_000)
    }
}

impl Events for World {
    fn publish(&self, event: MailboxEvent) {
        self.said.lock().unwrap().push(event);
    }
}

impl Tokens for World {
    fn hex(&self, bytes: usize) -> String {
        "0".repeat(bytes * 2)
    }
}

fn commands(world: &Arc<World>) -> MailCommands {
    commands_of(world, false)
}

fn commands_of(world: &Arc<World>, read_only: bool) -> MailCommands {
    MailCommands::new(CommandParts {
        store: world.clone(),
        accounts: world.clone(),
        server: ServerOps::new(world.clone(), read_only),
        gates: world.clone(),
        clock: world.clone(),
        events: world.clone(),
        tokens: world.clone(),
        read_only,
    })
}

#[test]
fn a_read_only_process_refuses_a_change_before_anything_is_written_and_leaves_what_a_full_run_queued() {
    let world = World::new();
    world.trash(1, ADA, 10, false, 0);
    let commands = commands_of(&world, true);
    assert_eq!(commands.accept_move("archive", 20, None, None, None).err(), Some(READ_ONLY));
    assert_eq!(commands.accept_flags(20, &[("seen", true)], None).err(), Some(READ_ONLY));
    assert_eq!(commands.retry(1).err(), Some(READ_ONLY), "nor does the tray's Retry ask again");
    commands.drain().unwrap();
    assert_eq!(world.rows.lock().unwrap().len(), 1, "nothing was queued");
    assert_eq!((world.row(1).state.as_str(), world.row(1).attempts), ("queued", 0), "it waits for a full run");
    assert_eq!(world.hidden.lock().unwrap().get(&1), Some(&vec![10]));
    assert_eq!(world.said(), []);
}

#[test]
fn a_try_that_panics_is_an_attempt_that_failed_and_the_next_row_still_applies() {
    let world = World::new();
    world.trash(1, PANICS, 10, false, 0);
    world.trash(2, ADA, 20, true, 0);
    let commands = commands(&world);
    commands.drain().unwrap();
    let panicked = world.row(1);
    assert_eq!(
        (panicked.state.as_str(), panicked.attempts, panicked.last_error.as_deref()),
        ("queued", 1, Some("Panic"))
    );
    assert_eq!(panicked.next_attempt_at, NOW + 20, "it backs off as a lost connection does");
    assert_eq!(world.hidden.lock().unwrap().get(&1), Some(&vec![10]), "its message waits hidden for the next try");
    assert_eq!(world.row(2).state, "done");
    assert_eq!(*world.deleted.lock().unwrap(), [20]);
    assert_eq!(world.said(), [MailboxEvent::Applied { id: 2, account: ADA.to_owned() }]);

    // Its claim was let go: the pass after its back-off tries it again.
    world.now.fetch_add(20, Ordering::SeqCst);
    commands.drain().unwrap();
    assert_eq!(world.row(1).attempts, 2);
}

#[test]
fn a_try_that_panics_at_its_last_attempt_lists_its_message_again() {
    let world = World::new();
    world.trash(1, PANICS, 10, false, MailCommands::MAX_ATTEMPTS - 1);
    commands(&world).drain().unwrap();
    let gave_up = world.row(1);
    assert_eq!(
        (gave_up.state.as_str(), gave_up.attempts, gave_up.last_error.as_deref()),
        ("error", MailCommands::MAX_ATTEMPTS, Some("Panic"))
    );
    assert_eq!(*world.listed_again.lock().unwrap(), [1]);
    assert_eq!(world.hidden.lock().unwrap().get(&1), None);
    assert_eq!(world.said(), [MailboxEvent::Restored { id: 1, account: PANICS.to_owned() }]);
}

#[test]
fn a_click_whose_try_panics_leaves_the_change_to_the_worker_at_once() {
    let world = World::new();
    world.trash(1, PANICS, 10, false, 0);
    // Accepting it claimed it for the click's try.
    let claimed = world.claim(1).unwrap();
    let accepted = Accepted { id: Some(1), account: PANICS.to_owned(), created: true, undoable: true, claimed };
    assert_eq!(commands(&world).submit(&accepted), Err(PANIC));
    let left = world.row(1);
    assert_eq!((left.state.as_str(), left.next_attempt_at, left.attempts), ("queued", 0, 0));
    assert_eq!(world.hidden.lock().unwrap().get(&1), Some(&vec![10]));
}

//! The change queue's commands and Undo's stack as the store keeps them: a request seen again finds its row, a claim
//! is had once, a failed block leaves nothing behind, and the stack keeps its newest.

#![allow(clippy::unwrap_used)]

use std::path::Path;
use std::sync::Arc;

use mm_kernel::errors::Fault;
use mm_kernel::seams::{Outside, Tripped};
use mm_mailbox::command_ports::{self, NewCommand};
use mm_store::{Database, DbKey, Store};
use serde_json::json;

#[derive(Debug)]
struct Open;

impl Outside for Open {
    fn open(&self, _path: &Path) -> Result<(), Tripped> {
        Ok(())
    }

    fn reach(&self, _host: &str) -> Result<(), Tripped> {
        Ok(())
    }

    fn run(&self, _program: &str) -> Result<(), Tripped> {
        Ok(())
    }
}

/// The two tables, as the newest schema golden has them.
const TABLES: &str = "
CREATE TABLE mail_commands(id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, message_id TEXT, src_folder TEXT NOT NULL, dest TEXT,
  params TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt_at INTEGER NOT NULL DEFAULT 0, applied INTEGER NOT NULL DEFAULT 0, begun INTEGER NOT NULL DEFAULT 0,
  outcome TEXT, last_error TEXT, created_at TEXT NOT NULL, done_at TEXT);
CREATE TABLE undo_entries (id INTEGER PRIMARY KEY AUTOINCREMENT, grp TEXT, entry TEXT NOT NULL,
  created_at TEXT NOT NULL);
CREATE TABLE sync_state (account TEXT NOT NULL, folder TEXT NOT NULL DEFAULT 'INBOX', uidvalidity INTEGER,
  PRIMARY KEY(account, folder));
";

fn database(dir: &Path) -> Database {
    let key = DbKey::from_bytes([0x5e; 32]);
    let path = dir.join("mailmind.db");
    Store::open(&path, &key).unwrap().create_list_tables().unwrap();
    let database = Database::new(path, key, Arc::new(Open));
    database.read(|c| c.execute_batch(TABLES)).unwrap();
    database
}

fn new<'a>(key: &'a str, params: &'a serde_json::Value) -> NewCommand<'a> {
    NewCommand {
        account: "ada@example.test",
        idempotency_key: key,
        kind: "trash",
        message_id: "<m@example.test>",
        src_folder: "INBOX",
        dest: Some("Trash"),
        params,
        created_at: "2026-11-02T12:00:00+00:00",
    }
}

#[test]
fn a_request_seen_again_finds_its_row_and_writes_nothing() {
    let dir = tempfile::tempdir().unwrap();
    let store = database(dir.path());
    let params = json!({"pk": 1, "uid": 5});
    let first = command_ports::write(&store, |tx| tx.insert_command(&new("trash:a:m:r1", &params))).unwrap();
    let again = command_ports::write(&store, |tx| tx.insert_command(&new("trash:a:m:r1", &params))).unwrap();
    assert_eq!((first.1, again), (true, (first.0, false)));
    let row = command_ports::read(&store, |tx| tx.command(first.0.unwrap())).unwrap().unwrap();
    assert_eq!((row.state.as_str(), row.params, row.dest.as_deref()), ("queued", params, Some("Trash")));
}

#[test]
fn a_claim_is_had_once_and_a_put_back_is_due_when_it_says() {
    let dir = tempfile::tempdir().unwrap();
    let store = database(dir.path());
    let params = json!({});
    let ids: Vec<i64> = ["r1", "r2"]
        .iter()
        .map(|r| command_ports::write(&store, |tx| tx.insert_command(&new(r, &params))).unwrap().0.unwrap())
        .collect();
    assert_eq!(command_ports::read(&store, |tx| tx.due(0)).unwrap(), ids);
    let claimed = command_ports::write(&store, |tx| tx.claim(ids[0])).unwrap();
    assert_eq!(claimed.map(|c| c.state), Some("running".to_owned()));
    assert_eq!(command_ports::write(&store, |tx| tx.claim(ids[0])).unwrap(), None);
    assert_eq!(command_ports::read(&store, |tx| tx.due(0)).unwrap(), [ids[1]]);

    command_ports::write(&store, |tx| tx.put_back(ids[0], 100)).unwrap();
    assert_eq!(command_ports::read(&store, |tx| tx.due(99)).unwrap(), [ids[1]]);
    assert_eq!(command_ports::read(&store, |tx| tx.due(100)).unwrap(), ids);
    assert_eq!(command_ports::read(&store, |tx| tx.next_due(0)).unwrap(), Some(100));

    command_ports::write(&store, |tx| tx.claim(ids[1]).map(|_| ())).unwrap();
    command_ports::write(&store, |tx| tx.reset_running()).unwrap();
    assert_eq!(command_ports::read(&store, |tx| tx.due(100)).unwrap(), ids);
}

#[test]
fn a_row_given_up_on_is_asked_again_and_only_one_not_applied_is_cancelled() {
    let dir = tempfile::tempdir().unwrap();
    let store = database(dir.path());
    let params = json!({});
    let id = command_ports::write(&store, |tx| tx.insert_command(&new("r1", &params))).unwrap().0.unwrap();
    command_ports::write(&store, |tx| tx.attempt_failed(id, true, 101, 0, "refused")).unwrap();
    let given_up = command_ports::read(&store, |tx| tx.command(id)).unwrap().unwrap();
    assert_eq!(
        (given_up.state.as_str(), given_up.attempts, given_up.last_error.as_deref()),
        ("error", 101, Some("refused"))
    );
    assert_eq!(command_ports::read(&store, |tx| tx.open_counts()).unwrap().get("error"), Some(&1));

    assert!(command_ports::write(&store, |tx| tx.requeue(id)).unwrap());
    assert!(!command_ports::write(&store, |tx| tx.requeue(id)).unwrap(), "only a row given up on goes back");
    let asked = command_ports::read(&store, |tx| tx.command(id)).unwrap().unwrap();
    assert_eq!((asked.state.as_str(), asked.attempts, asked.last_error), ("queued", 0, None));

    command_ports::write(&store, |tx| tx.mark_applied(id)).unwrap();
    assert!(!command_ports::write(&store, |tx| tx.cancel_unapplied(id)).unwrap());
    command_ports::write(&store, |tx| tx.finish(id, "2026-11-02T12:01:00+00:00")).unwrap();
    assert_eq!(command_ports::read(&store, |tx| tx.command(id)).unwrap().unwrap().state, "done");

    let other = command_ports::write(&store, |tx| tx.insert_command(&new("r2", &params))).unwrap().0.unwrap();
    assert!(command_ports::write(&store, |tx| tx.cancel_unapplied(other)).unwrap());
    assert_eq!(command_ports::read(&store, |tx| tx.command(other)).unwrap(), None);
}

#[test]
fn a_block_that_fails_leaves_nothing_behind() {
    let dir = tempfile::tempdir().unwrap();
    let store = database(dir.path());
    let params = json!({});
    let failed: Result<(), Fault> = command_ports::write(&store, |tx| {
        tx.insert_command(&new("r1", &params))?;
        Err(Fault::new("Boom"))
    });
    assert_eq!(failed, Err(Fault::new("Boom")));
    assert_eq!(command_ports::read(&store, |tx| tx.due(0)).unwrap(), Vec::<i64>::new());
}

#[test]
fn the_undo_stack_keeps_its_newest_and_finds_a_group() {
    let dir = tempfile::tempdir().unwrap();
    let store = database(dir.path());
    let at = |hour: u32| format!("2026-11-02T{hour:02}:00:00+00:00");
    command_ports::write(&store, |tx| {
        tx.undo_insert(None, &json!({"kind": "move", "n": 1}), &at(1))?;
        tx.undo_insert(Some("bulk:1"), &json!({"kind": "bulk", "group": "bulk:1", "children": []}), &at(2))?;
        tx.undo_insert(None, &json!({"kind": "move", "n": 3}), &at(3))
    })
    .unwrap();
    let rows = command_ports::read(&store, |tx| tx.undo_rows()).unwrap();
    assert_eq!(rows.iter().map(|r| r.entry["kind"].as_str().unwrap()).collect::<Vec<_>>(), ["move", "bulk", "move"]);
    let group = command_ports::read(&store, |tx| tx.undo_group_row("bulk:1")).unwrap().unwrap();
    assert_eq!(group.group.as_deref(), Some("bulk:1"));

    command_ports::write(&store, |tx| tx.undo_update(group.id, &json!({"kind": "bulk", "children": [1]}))).unwrap();
    let entries = command_ports::read(&store, |tx| tx.undo_entries(&at(2))).unwrap();
    assert_eq!(entries, [json!({"kind": "bulk", "children": [1]}), json!({"kind": "move", "n": 3})]);

    command_ports::write(&store, |tx| tx.undo_keep(2)).unwrap();
    assert_eq!(command_ports::read(&store, |tx| tx.undo_rows()).unwrap().len(), 2);
    command_ports::write(&store, |tx| tx.undo_expire(&at(3))).unwrap();
    let left = command_ports::read(&store, |tx| tx.undo_rows()).unwrap();
    assert_eq!(left.iter().map(|r| r.entry.clone()).collect::<Vec<_>>(), [json!({"kind": "move", "n": 3})]);
    command_ports::write(&store, |tx| tx.undo_delete(left[0].id)).unwrap();
    assert!(command_ports::read(&store, |tx| tx.undo_rows()).unwrap().is_empty());
}

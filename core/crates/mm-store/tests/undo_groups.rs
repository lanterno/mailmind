//! Undo of a group on the store's stack (`platform.undo`'s group, as tests/unit/platform/test_undo_stack.py holds the
//! Python one): a run of one kind and one account whose reverser takes them together goes back at one turn of the
//! account's gate, the rest one at a time, and what didn't go back is on the stack again for another try.

#![allow(clippy::unwrap_used)]

use std::path::Path;
use std::sync::{Arc, Mutex};

use mm_kernel::errors::{Failure, Fault};
use mm_kernel::seams::{Clock, Moment, Outside, Tripped};
use mm_mailbox::command_ports;
use mm_mailbox::gates::{Gates, Priority, Refused, Wait};
use mm_mailbox::sync_ports::{Events, MailboxEvent};
use mm_mailbox::undo::{self, Reversed, Reverser, Undo};
use mm_store::{Database, DbKey, Store};
use serde_json::{Value, json};

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

fn database(dir: &Path) -> Database {
    let key = DbKey::from_bytes([0x5e; 32]);
    let path = dir.join("mailmind.db");
    Store::open(&path, &key).unwrap().create_list_tables().unwrap();
    let database = Database::new(path, key, Arc::new(Open));
    database
        .read(|c| {
            c.execute_batch(
                "CREATE TABLE undo_entries (id INTEGER PRIMARY KEY AUTOINCREMENT, grp TEXT, entry TEXT NOT NULL, \
                 created_at TEXT NOT NULL);",
            )
        })
        .unwrap();
    database
}

#[derive(Debug)]
struct Frozen;

impl Clock for Frozen {
    fn now(&self) -> Moment {
        Moment::from_micros(1_793_620_800_000_000)
    }
}

#[derive(Debug, Default)]
struct Quiet;

impl Events for Quiet {
    fn publish(&self, _event: MailboxEvent) {}
}

/// What was asked, in order: each turn of a gate, and each reversal.
#[derive(Debug, Default)]
struct Asked(Mutex<Vec<String>>);

impl Asked {
    fn note(&self, what: String) {
        self.0.lock().unwrap().push(what);
    }

    fn all(&self) -> Vec<String> {
        self.0.lock().unwrap().clone()
    }
}

#[derive(Debug)]
struct Watched(Arc<Asked>);

impl Gates for Watched {
    fn run(&self, account: &str, _: Priority, _: Wait, job: &mut dyn FnMut()) -> Result<(), Refused> {
        self.0.note(format!("gate {account}"));
        job();
        Ok(())
    }

    fn held(&self, _: &str) -> bool {
        false
    }
}

/// Reverses `swept` entries together and `move` ones alone. One whose `mid` is `missing` doesn't go back; a run
/// holding a `lost` one fails whole.
#[derive(Debug)]
struct Swept(Arc<Asked>);

fn mid(entry: &Value) -> &str {
    entry["message_id"].as_str().unwrap()
}

fn answer(entry: &Value) -> Reversed {
    (mid(entry) != "missing", entry["account"].as_str().map(str::to_owned), Some("INBOX".to_owned()))
}

impl Reverser for Swept {
    fn reverses(&self, kind: &str) -> bool {
        matches!(kind, "swept" | "move")
    }

    fn reverse(&self, entry: &Value) -> Result<Reversed, Fault> {
        self.0.note(format!("one {}", mid(entry)));
        Ok(answer(entry))
    }

    fn together(&self, kind: &str) -> bool {
        kind == "swept"
    }

    fn reverse_together(&self, entries: &[Value]) -> Result<Vec<Reversed>, Fault> {
        let mids: Vec<&str> = entries.iter().map(mid).collect();
        self.0.note(format!("together {}", mids.join(" ")));
        if mids.contains(&"lost") {
            return Err(Fault::new("ConnectionLost"));
        }
        Ok(entries.iter().map(answer).collect())
    }
}

fn child(kind: &str, account: &str, mid: &str) -> Value {
    json!({"kind": kind, "account": account, "message_id": mid, "label": "Swept junk"})
}

/// An Undo over a fresh stack holding one group of `children`, oldest first, and what it asks.
fn stack(dir: &Path, children: &[Value]) -> (Undo, Arc<Asked>) {
    let store = Arc::new(database(dir));
    command_ports::write(&*store, |tx| {
        for c in children {
            undo::push(tx, c, Some("ug-1"), Frozen.now())?;
        }
        Ok(())
    })
    .unwrap();
    let asked = Arc::new(Asked::default());
    let undo = Undo::new(
        store,
        Arc::new(Watched(asked.clone())),
        Arc::new(Frozen),
        Arc::new(Quiet),
        vec![Arc::new(Swept(asked.clone()))],
    );
    (undo, asked)
}

fn mids(entry: &Value) -> Vec<&str> {
    entry["children"].as_array().unwrap().iter().map(mid).collect()
}

#[test]
fn a_run_of_one_kind_and_one_account_goes_back_together_newest_first_and_the_rest_one_at_a_time() {
    let dir = tempfile::tempdir().unwrap();
    let ada = "ada@example.test";
    let children = [
        child("swept", ada, "m1"),
        child("swept", ada, "missing"),
        child("move", ada, "m3"),
        child("swept", ada, "m4"),
        child("swept", ada, "m5"),
        child("swept", "bea@example.test", "m6"),
    ];
    let (undo, asked) = stack(dir.path(), &children);
    let done = undo.undo_group("ug-1").unwrap();
    assert_eq!((done["reversed"].clone(), done["failed"].clone()), (json!(5), json!(1)));
    assert_eq!(
        asked.all(),
        [
            "gate bea@example.test",
            "together m6",
            "gate ada@example.test",
            "together m5 m4",
            "gate ada@example.test",
            "one m3",
            "gate ada@example.test",
            "together missing m1",
        ]
    );
    let [left] = &undo.entries().unwrap()[..] else { panic!("one group left") };
    assert_eq!((left["group"].clone(), mids(left)), (json!("ug-1"), vec!["missing"]));
}

#[test]
fn a_run_that_fails_whole_is_on_the_stack_again_and_stops_no_other() {
    let dir = tempfile::tempdir().unwrap();
    let ada = "ada@example.test";
    let children = [child("swept", ada, "m1"), child("swept", ada, "lost"), child("move", ada, "m3")];
    let (undo, asked) = stack(dir.path(), &children);
    let done = undo.undo_group("ug-1").unwrap();
    assert_eq!((done["reversed"].clone(), done["failed"].clone()), (json!(1), json!(2)));
    assert_eq!(asked.all(), ["gate ada@example.test", "one m3", "gate ada@example.test", "together lost m1"]);
    let [left] = &undo.entries().unwrap()[..] else { panic!("one group left") };
    assert_eq!(mids(left), ["m1", "lost"]);
    // Nothing of it going back is a refusal, and all of it waits for another try.
    let none = undo.undo_group("ug-1");
    assert!(matches!(none, Err(Failure::Problem(_))));
    assert_eq!(mids(&undo.entries().unwrap()[0]), ["m1", "lost"]);
}

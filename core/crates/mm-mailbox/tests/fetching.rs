//! A message's body fetched as it opens (`Ingest.fetch_body` as `bodies.message_body` runs it), against a server
//! that writes down what it was asked and a store in memory: on the account's gate as a person waits, or as a
//! prefetch that gives way to a held gate; kept with what opening it records; read from the store once it is there.

#![allow(clippy::unwrap_used)]

use std::collections::{BTreeMap, HashMap};
use std::sync::{Arc, Mutex};

use mm_content::mime::Attachment;
use mm_kernel::errors::Fault;
use mm_kernel::seams::{Clock, Moment};
use mm_mailbox::fetch_ports::{KeptBodies, KeptBody};
use mm_mailbox::fetching::{Fetching, FetchingParts};
use mm_mailbox::gates::{Gates, Priority, Refused, Wait};
use mm_mailbox::ports::{BodyFetch, MailSession, Row, SessionError, StoredBodies};
use mm_mailbox::roles::Folder;
use mm_mailbox::session::{Headers, MailServer, MailServers, Search, Selected, ServerError};
use serde_json::json;

const ADA: &str = "ada@example.test";
const RAW: &[u8] = b"From: Lin <lin@example.test>\r\nTo: Ada <ada@example.test>\r\nCc: bo@example.test\r\n\
Subject: Lunch\r\nContent-Type: multipart/mixed; boundary=b\r\n\r\n--b\r\nContent-Type: text/plain\r\n\r\n\
Lunch at noon?\r\n--b\r\nContent-Type: application/pdf; name=menu.pdf\r\nContent-Disposition: attachment; \
filename=menu.pdf\r\n\r\nPDF\r\n--b--\r\n";

#[derive(Debug)]
struct Frozen;

impl Clock for Frozen {
    fn now(&self) -> Moment {
        Moment::from_micros(1_793_620_800_000_000)
    }
}

/// What the server is to do with a body asked for.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Body {
    Gives,
    Refuses,
    Lost,
    RefusesTheSelect,
}

/// A server with one message at UID 7, which writes down what it was asked.
#[derive(Debug)]
struct Server {
    body: Body,
    asked: Arc<Mutex<Vec<String>>>,
}

impl MailSession for Server {
    fn folders(&mut self) -> Result<Vec<Folder>, SessionError> {
        Ok(Vec::new())
    }

    fn status(&mut self, _folder: &str) -> Result<(i64, i64), SessionError> {
        Ok((0, 0))
    }

    fn logout(&mut self) {
        self.asked.lock().unwrap().push("LOGOUT".to_owned());
    }
}

impl MailServer for Server {
    fn capabilities(&self) -> Vec<String> {
        Vec::new()
    }

    fn select(&mut self, folder: &str, readonly: bool) -> Result<Selected, ServerError> {
        self.asked.lock().unwrap().push(format!("{} {folder}", if readonly { "EXAMINE" } else { "SELECT" }));
        if self.body == Body::RefusesTheSelect {
            return Err(ServerError::Refused("error"));
        }
        Ok(Selected::default())
    }

    fn search(&mut self, _criteria: Search<'_>) -> Result<Vec<u32>, ServerError> {
        Ok(Vec::new())
    }

    fn flags(&mut self, _uids: &[u32]) -> Result<BTreeMap<u32, Vec<String>>, ServerError> {
        Ok(BTreeMap::new())
    }

    fn enable_condstore(&mut self) -> Result<bool, ServerError> {
        Ok(false)
    }

    fn headers(&mut self, _uids: &[u32], _fields: &[&str], _gmail: bool) -> Result<Vec<Headers>, ServerError> {
        Ok(Vec::new())
    }

    fn body(&mut self, uid: u32) -> Result<Option<Vec<u8>>, ServerError> {
        self.asked.lock().unwrap().push(format!("BODY.PEEK[] {uid}"));
        match self.body {
            Body::Refuses => Err(ServerError::Refused("error")),
            Body::Lost => Err(ServerError::Unreachable("abort")),
            _ if uid == 7 => Ok(Some(RAW.to_vec())),
            _ => Ok(None),
        }
    }

    fn arrivals(&mut self, _uids: &[u32]) -> Result<BTreeMap<u32, i64>, ServerError> {
        Ok(BTreeMap::new())
    }

    fn store_flags(&mut self, _: &[u32], _: bool, _: &[&str]) -> Result<(), ServerError> {
        Err(ServerError::Refused("write"))
    }

    fn store_labels(&mut self, _: &[u32], _: bool, _: &[&str]) -> Result<(), ServerError> {
        Err(ServerError::Refused("write"))
    }

    fn copy(&mut self, _: &[u32], _: &str) -> Result<(), ServerError> {
        Err(ServerError::Refused("write"))
    }

    fn uid_move(&mut self, _: &[u32], _: &str) -> Result<(), ServerError> {
        Err(ServerError::Refused("write"))
    }

    fn expunge(&mut self, _: &[u32]) -> Result<(), ServerError> {
        Err(ServerError::Refused("write"))
    }

    fn append(&mut self, _: &str, _: &[u8], _: &[&str], _: Option<&str>) -> Result<Option<u32>, ServerError> {
        Err(ServerError::Refused("write"))
    }

    fn shutdown(&mut self) {}
}

#[derive(Debug)]
struct Servers {
    body: Mutex<Body>,
    asked: Arc<Mutex<Vec<String>>>,
}

impl MailServers for Servers {
    fn open(&self, account: &str, _timeout_seconds: u64) -> Result<Box<dyn MailServer>, ServerError> {
        self.asked.lock().unwrap().push(format!("LOGIN {account}"));
        Ok(Box::new(Server { body: *self.body.lock().unwrap(), asked: self.asked.clone() }))
    }
}

/// Gates that write down each job's account, priority and wait, and are held elsewhere when `held` says.
#[derive(Debug, Default)]
struct Watched {
    held: Mutex<bool>,
    jobs: Mutex<Vec<(String, Priority, Wait)>>,
}

impl Gates for Watched {
    fn run(&self, account: &str, priority: Priority, wait: Wait, job: &mut dyn FnMut()) -> Result<(), Refused> {
        self.jobs.lock().unwrap().push((account.to_owned(), priority, wait));
        if *self.held.lock().unwrap() && wait == Wait::Not {
            return Err(Refused::Busy);
        }
        job();
        Ok(())
    }

    fn held(&self, _account: &str) -> bool {
        *self.held.lock().unwrap()
    }
}

/// One message's row, its body and parts as kept, in memory.
#[derive(Debug, Default)]
struct Store {
    rows: Mutex<HashMap<i64, Row>>,
    bodies: Mutex<HashMap<i64, KeptBody>>,
    parts: Mutex<HashMap<i64, Vec<Attachment>>>,
}

impl Store {
    fn with_message(pk: i64) -> Store {
        let store = Store::default();
        let row = json!({"pk": pk, "account": ADA, "folder": "Archive", "uid": 7, "body_fetched": 0});
        store.rows.lock().unwrap().insert(pk, row.as_object().unwrap().clone());
        store
    }
}

impl StoredBodies for Store {
    fn copy_pk(&self, _key: i64, _folder: &str, _uid: Option<i64>) -> Result<Option<i64>, Fault> {
        Ok(None)
    }

    fn message(&self, pk: i64) -> Result<Option<Row>, Fault> {
        Ok(self.rows.lock().unwrap().get(&pk).cloned())
    }

    fn raw(&self, pk: i64) -> Result<Option<Vec<u8>>, Fault> {
        Ok(self.bodies.lock().unwrap().get(&pk).map(|b| b.raw.clone()))
    }

    fn part(&self, _pk: i64, _part_id: &str) -> Result<Option<Row>, Fault> {
        Ok(None)
    }

    fn has_parts(&self, pk: i64) -> Result<bool, Fault> {
        Ok(self.parts.lock().unwrap().get(&pk).is_some_and(|p| !p.is_empty()))
    }

    fn keep_parts(&self, pk: i64, parts: &[Attachment]) -> Result<(), Fault> {
        self.parts.lock().unwrap().entry(pk).or_default().extend_from_slice(parts);
        Ok(())
    }
}

impl KeptBodies for Store {
    fn keep_body(&self, pk: i64, body: &KeptBody) -> Result<(), Fault> {
        self.bodies.lock().unwrap().insert(pk, body.clone());
        self.parts.lock().unwrap().insert(pk, body.parts.clone());
        if let Some(row) = self.rows.lock().unwrap().get_mut(&pk) {
            row.insert("body_fetched".to_owned(), json!(1));
        }
        Ok(())
    }
}

struct World {
    store: Arc<Store>,
    servers: Arc<Servers>,
    gates: Arc<Watched>,
    fetching: Fetching,
}

impl World {
    fn new(body: Body) -> World {
        let store = Arc::new(Store::with_message(1));
        let servers = Arc::new(Servers { body: Mutex::new(body), asked: Arc::default() });
        let gates = Arc::new(Watched::default());
        let fetching = Fetching::new(FetchingParts {
            stored: store.clone(),
            kept: store.clone(),
            servers: servers.clone(),
            gates: gates.clone(),
            clock: Arc::new(Frozen),
        });
        World { store, servers, gates, fetching }
    }

    fn asked(&self) -> Vec<String> {
        std::mem::take(&mut *self.servers.asked.lock().unwrap())
    }

    fn jobs(&self) -> Vec<(String, Priority, Wait)> {
        std::mem::take(&mut *self.gates.jobs.lock().unwrap())
    }
}

#[test]
fn a_body_not_stored_is_fetched_on_the_gate_examined_and_peeked_then_logged_out() {
    let world = World::new(Body::Gives);
    assert_eq!(world.fetching.fetch(1).unwrap().unwrap(), RAW);
    assert_eq!(
        world.asked(),
        [format!("LOGIN {ADA}"), "EXAMINE Archive".into(), "BODY.PEEK[] 7".into(), "LOGOUT".into()]
    );
    assert_eq!(world.jobs(), [(ADA.to_owned(), Priority::Interactive, Wait::Ever)]);
}

#[test]
fn a_body_fetched_is_kept_with_what_opening_it_records() {
    let world = World::new(Body::Gives);
    world.fetching.fetch(1).unwrap();
    let kept = world.store.bodies.lock().unwrap().get(&1).cloned().unwrap();
    assert_eq!(kept.raw, RAW);
    assert_eq!(kept.when, "2026-11-02T12:00:00+00:00");
    assert_eq!(kept.recipients, "Ada <ada@example.test> bo@example.test");
    assert_eq!(kept.recipient_addrs, ["ada@example.test", "bo@example.test"]);
    assert_eq!(kept.body_text, "Lunch at noon?");
    assert_eq!(kept.snippet, "Lunch at noon?");
    assert_eq!(kept.parts.iter().map(|p| p.filename.as_str()).collect::<Vec<_>>(), ["menu.pdf"]);
}

#[test]
fn a_body_stored_is_read_from_the_store_without_the_gate_or_the_server() {
    let world = World::new(Body::Gives);
    world.fetching.fetch(1).unwrap();
    world.asked();
    world.jobs();
    *world.gates.held.lock().unwrap() = true;
    assert_eq!(world.fetching.fetch(1).unwrap().unwrap(), RAW);
    assert_eq!((world.asked(), world.jobs()), (Vec::new(), Vec::new()));
}

#[test]
fn a_part_a_parsing_change_took_away_comes_back_as_the_body_is_read() {
    let world = World::new(Body::Gives);
    world.fetching.fetch(1).unwrap();
    world.store.parts.lock().unwrap().clear();
    world.fetching.fetch(1).unwrap();
    assert_eq!(world.store.parts.lock().unwrap()[&1].len(), 1);
}

#[test]
fn a_refused_fetch_answers_an_empty_body_and_keeps_nothing() {
    let world = World::new(Body::Refuses);
    assert_eq!(world.fetching.fetch(1).unwrap(), Some(Vec::new()));
    assert!(world.store.bodies.lock().unwrap().is_empty());
    assert_eq!(world.asked().last().map(String::as_str), Some("LOGOUT"));
}

#[test]
fn a_message_its_server_no_longer_has_is_an_empty_body() {
    let world = World::new(Body::Gives);
    world.store.rows.lock().unwrap().get_mut(&1).unwrap().insert("uid".to_owned(), json!(8));
    assert_eq!(world.fetching.fetch(1).unwrap(), Some(Vec::new()));
    assert!(world.store.bodies.lock().unwrap().is_empty());
}

#[test]
fn a_lost_connection_or_a_refused_select_fails_and_still_logs_out() {
    for (body, class) in [(Body::Lost, "abort"), (Body::RefusesTheSelect, "error")] {
        let world = World::new(body);
        assert_eq!(world.fetching.fetch(1), Err(Fault::new(class)));
        assert_eq!(world.asked().last().map(String::as_str), Some("LOGOUT"));
        assert!(world.store.bodies.lock().unwrap().is_empty());
    }
}

#[test]
fn a_row_gone_is_none_and_asks_no_server() {
    let world = World::new(Body::Gives);
    assert_eq!(world.fetching.fetch(2).unwrap(), None);
    assert!(!world.fetching.prefetch(2).unwrap());
    assert_eq!((world.asked(), world.jobs()), (Vec::new(), Vec::new()));
}

#[test]
fn a_prefetch_fetches_at_maintenance_priority_without_waiting() {
    let world = World::new(Body::Gives);
    assert!(world.fetching.prefetch(1).unwrap());
    assert_eq!(world.jobs(), [(ADA.to_owned(), Priority::Maintenance, Wait::Not)]);
    assert!(world.store.bodies.lock().unwrap().contains_key(&1));
}

#[test]
fn a_prefetch_gives_way_to_a_held_gate_and_fetches_nothing() {
    let world = World::new(Body::Gives);
    *world.gates.held.lock().unwrap() = true;
    assert!(!world.fetching.prefetch(1).unwrap());
    assert!(world.asked().is_empty());
    assert!(world.store.bodies.lock().unwrap().is_empty());
}

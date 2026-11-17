//! A message's body fetched as it opens (`Ingest.fetch_body` as `bodies.message_body` runs it), against a server
//! that writes down what it was asked and a store in memory: on the account's gate as a person waits, or as a
//! prefetch that gives way to a held gate; kept with what opening it records; read from the store once it is there.
//! And as the body backfill fetches a folder's few: as a prefetch gives way, their openings in one FETCH, the whole
//! of those cut off several a FETCH where small enough and only the text of one too large, a lost connection told
//! apart from the rest.

#![allow(clippy::unwrap_used)]

use std::collections::{BTreeMap, HashMap};
use std::sync::{Arc, Mutex};

use mm_content::mime::Attachment;
use mm_kernel::errors::Fault;
use mm_kernel::seams::{Clock, Moment};
use mm_mailbox::backfill_ports::{BackfillFetch, Tried};
use mm_mailbox::fetch_ports::{KeptBodies, KeptBody, KeptText};
use mm_mailbox::fetching::{FETCH_BYTES, Fetching, FetchingParts, OPENING_BYTES, WHOLE_MOST};
use mm_mailbox::gates::{Gates, Priority, Refused, Wait};
use mm_mailbox::ports::{BodyFetch, MailSession, Row, SessionError, StoredBodies};
use mm_mailbox::roles::Folder;
use mm_mailbox::session::{Headers, MailServer, MailServers, Opening, Search, Selected, ServerError};
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
    /// Gives, and says each message is 3 KiB shorter than it is, as an estimate of its size can (Exchange's).
    SaysItShorter,
    Refuses,
    Lost,
    RefusesTheSelect,
}

/// A server with the messages it is given, RAW at UID 7 unless told else, which writes down what it was asked.
#[derive(Debug)]
struct Server {
    body: Body,
    asked: Arc<Mutex<Vec<String>>>,
    messages: BTreeMap<u32, Vec<u8>>,
}

/// UIDs as a set says them.
fn set(uids: &[u32]) -> String {
    uids.iter().map(u32::to_string).collect::<Vec<_>>().join(",")
}

/// A message of about `kib` KiB: a line of text, then an attachment.
fn sized(kib: usize) -> Vec<u8> {
    let mut raw =
        b"Subject: Plans\r\nContent-Type: multipart/mixed; boundary=b\r\n\r\n--b\r\nContent-Type: text/plain\r\n\r\n\
The plans, attached.\r\n--b\r\nContent-Type: application/pdf; name=plans.pdf\r\n\r\n"
            .to_vec();
    raw.resize(kib * 1024, b'x');
    raw
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
            _ => Ok(self.messages.get(&uid).cloned()),
        }
    }

    fn openings(&mut self, uids: &[u32], bytes: u32) -> Result<BTreeMap<u32, Opening>, ServerError> {
        self.asked.lock().unwrap().push(format!("OPENINGS {} {bytes}", set(uids)));
        match self.body {
            // Each refused is left out, as a server's NO for them leaves them.
            Body::Refuses => Ok(BTreeMap::new()),
            Body::Lost => Err(ServerError::Unreachable("abort")),
            _ => Ok(uids
                .iter()
                .filter_map(|uid| self.messages.get(uid).map(|raw| (*uid, raw)))
                .map(|(uid, raw)| {
                    let short = if self.body == Body::SaysItShorter { 3 * 1024 } else { 0 };
                    let size = Some(u32::try_from(raw.len().saturating_sub(short)).unwrap());
                    (uid, Opening { raw: raw[..raw.len().min(bytes as usize)].to_vec(), size })
                })
                .collect()),
        }
    }

    fn bodies(&mut self, uids: &[u32]) -> Result<BTreeMap<u32, Vec<u8>>, ServerError> {
        self.asked.lock().unwrap().push(format!("BODIES {}", set(uids)));
        Ok(uids.iter().filter_map(|uid| self.messages.get(uid).map(|raw| (*uid, raw.clone()))).collect())
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

    fn shutdown(&mut self) {
        self.asked.lock().unwrap().push("SHUTDOWN".to_owned());
    }
}

#[derive(Debug)]
struct Servers {
    body: Mutex<Body>,
    asked: Arc<Mutex<Vec<String>>>,
    messages: Mutex<BTreeMap<u32, Vec<u8>>>,
}

impl MailServers for Servers {
    fn open(&self, account: &str, _timeout_seconds: u64) -> Result<Box<dyn MailServer>, ServerError> {
        self.asked.lock().unwrap().push(format!("LOGIN {account}"));
        let messages = self.messages.lock().unwrap().clone();
        Ok(Box::new(Server { body: *self.body.lock().unwrap(), asked: self.asked.clone(), messages }))
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

/// One message's row, its body and parts as kept, and the text kept of one left to be opened, in memory.
#[derive(Debug, Default)]
struct Store {
    rows: Mutex<HashMap<i64, Row>>,
    bodies: Mutex<HashMap<i64, KeptBody>>,
    parts: Mutex<HashMap<i64, Vec<Attachment>>>,
    texts: Mutex<HashMap<i64, KeptText>>,
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

    fn keep_text(&self, pk: i64, text: &KeptText) -> Result<(), Fault> {
        self.texts.lock().unwrap().insert(pk, text.clone());
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
        let messages = Mutex::new(BTreeMap::from([(7, RAW.to_vec())]));
        let servers = Arc::new(Servers { body: Mutex::new(body), asked: Arc::default(), messages });
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

impl World {
    /// The server holds `messages` too, as the store holds rows of pks `uid - 100`.
    fn holding(self, messages: impl IntoIterator<Item = (u32, Vec<u8>)>) -> World {
        for (uid, raw) in messages {
            self.servers.messages.lock().unwrap().insert(uid, raw);
            let row =
                json!({"pk": i64::from(uid) - 100, "account": ADA, "folder": "Archive", "uid": uid, "body_fetched": 0});
            self.store.rows.lock().unwrap().insert(i64::from(uid) - 100, row.as_object().unwrap().clone());
        }
        self
    }

    fn kept(&self) -> Vec<i64> {
        let mut kept: Vec<i64> = self.store.bodies.lock().unwrap().keys().copied().collect();
        kept.sort_unstable();
        kept
    }

    fn texts(&self) -> Vec<i64> {
        let mut texts: Vec<i64> = self.store.texts.lock().unwrap().keys().copied().collect();
        texts.sort_unstable();
        texts
    }
}

#[test]
fn the_backfill_keeps_a_folders_bodies_from_one_fetch_of_their_openings_at_maintenance_priority() {
    let world = World::new(Body::Gives).holding([(108, b"Subject: Hi\r\n\r\nHello\r\n".to_vec())]);
    assert_eq!(world.fetching.try_bodies(ADA, "Archive", &[(1, 7), (8, 108)]), Tried::Ran);
    assert_eq!(world.jobs(), [(ADA.to_owned(), Priority::Maintenance, Wait::Not)]);
    assert_eq!(world.asked()[1..], ["EXAMINE Archive".to_owned(), "OPENINGS 7,108 65536".into(), "LOGOUT".into()]);
    assert_eq!(world.kept(), [1, 8]);
    assert_eq!(world.store.bodies.lock().unwrap()[&1].raw, RAW);
    assert_eq!(OPENING_BYTES, 65_536);
}

#[test]
fn a_message_cut_off_is_fetched_whole_several_a_fetch_and_one_too_large_keeps_only_its_texts() {
    assert_eq!((WHOLE_MOST, FETCH_BYTES), (256 * 1024, 2 * 1024 * 1024));
    let world = World::new(Body::Gives).holding([(101, sized(100)), (102, sized(300)), (103, sized(256))]);
    let batch = [(1, 101), (2, 102), (3, 103), (7, 7)];
    assert_eq!(world.fetching.try_bodies(ADA, "Archive", &batch), Tried::Ran);
    assert_eq!(
        world.asked()[1..],
        ["EXAMINE Archive".to_owned(), "OPENINGS 101,102,103,7 65536".into(), "BODIES 101,103".into(), "LOGOUT".into()]
    );
    assert_eq!(world.kept(), [1, 3, 7]);
    assert_eq!(world.store.bodies.lock().unwrap()[&3].raw, sized(256), "kept whole");
    // The one over a quarter of a megabyte keeps its opening's text, and its attachment waits to be opened.
    assert_eq!(world.texts(), [2]);
    assert_eq!(world.store.texts.lock().unwrap()[&2].body_text.trim(), "The plans, attached.");
}

#[test]
fn a_message_its_server_says_is_shorter_than_its_opening_counts_as_cut_off_and_is_fetched_whole() {
    let world = World::new(Body::SaysItShorter).holding([(101, sized(66)), (102, sized(64)), (103, sized(10))]);
    let batch = [(1, 101), (2, 102), (3, 103)];
    assert_eq!(world.fetching.try_bodies(ADA, "Archive", &batch), Tried::Ran);
    // 66 KiB said to be 63 came cut off at 64 KiB, as did one of exactly 64 said to be 61: each is fetched whole. One
    // that ended before 64 KiB is whole, whatever its size is said to be.
    assert_eq!(
        world.asked()[1..],
        ["EXAMINE Archive".to_owned(), "OPENINGS 101,102,103 65536".into(), "BODIES 101,102".into(), "LOGOUT".into()]
    );
    assert_eq!(world.kept(), [1, 2, 3]);
    assert_eq!(world.store.bodies.lock().unwrap()[&1].raw, sized(66), "kept whole, not its first 64 KiB");
    assert!(world.texts().is_empty());
}

#[test]
fn an_opening_is_whole_where_it_ended_before_what_was_asked_or_is_exactly_the_size_said() {
    let opening = |bytes: usize, size: Option<u32>| Opening { raw: vec![b'x'; bytes], size };
    assert!(opening(10, None).is_whole(64));
    assert!(opening(10, Some(9)).is_whole(64));
    assert!(opening(64, Some(64)).is_whole(64));
    assert!(!opening(64, None).is_whole(64));
    assert!(!opening(64, Some(65)).is_whole(64));
    assert!(!opening(64, Some(63)).is_whole(64), "a size shorter than what came is wrong");
}

#[test]
fn whole_messages_go_about_two_megabytes_a_fetch() {
    let world = World::new(Body::Gives).holding((101..=112).map(|uid| (uid, sized(250))));
    let batch: Vec<(i64, u32)> = (101..=112).map(|uid| (i64::from(uid) - 100, uid)).collect();
    assert_eq!(world.fetching.try_bodies(ADA, "Archive", &batch), Tried::Ran);
    let bodies: Vec<String> = world.asked().into_iter().filter(|a| a.starts_with("BODIES")).collect();
    assert_eq!(bodies, ["BODIES 101,102,103,104,105,106,107,108", "BODIES 109,110,111,112"]);
    assert_eq!(world.kept().len(), 12);
}

#[test]
fn the_backfill_hears_of_a_held_gate_and_a_lost_connection_apart_from_the_rest() {
    let world = World::new(Body::Gives);
    *world.gates.held.lock().unwrap() = true;
    assert_eq!(world.fetching.try_bodies(ADA, "Archive", &[(1, 7)]), Tried::Busy);
    assert!(world.asked().is_empty());
    let lost = World::new(Body::Lost);
    assert_eq!(lost.fetching.try_bodies(ADA, "Archive", &[(1, 7)]), Tried::Unreachable);
    assert_eq!(lost.asked().last().map(String::as_str), Some("SHUTDOWN"), "a lost connection isn't kept");
    // A refusal, a folder the server won't select and a message gone each ran, and kept nothing.
    for (body, uid) in [(Body::Refuses, 7), (Body::RefusesTheSelect, 7), (Body::Gives, 8)] {
        let world = World::new(body);
        assert_eq!(world.fetching.try_bodies(ADA, "Archive", &[(1, uid)]), Tried::Ran);
        assert!(world.kept().is_empty() && world.texts().is_empty());
        assert_eq!(world.asked().last().map(String::as_str), Some("LOGOUT"));
    }
}

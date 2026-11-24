//! An account's first sync on the Rust server, against an IMAP server on loopback that holds fictional mail and writes
//! down what each account was sent: the Inbox's newest 500 stored at once, the rest newest first a few steps a round,
//! each step one transaction, so a round that stops goes on where the last left off; each message's text read from
//! its first 64 KiB, only an invite cut off fetched whole; how far the scan has got in the sync's status; none of it
//! new mail, though what it kept is summarized at once; each account syncing on its own; and an Inbox opened while its first sync runs joining it, holding no
//! gate, so a message opens meanwhile.
//!
//! A process that only reads reads the older mail too. A message whose opening the server won't send costs only its
//! own text, one whose headers it won't send is left out of the older mail, and a connection lost part way stores
//! nothing of its step. A sync that fails while older mail is still being read keeps saying how far it got, and an
//! Inbox opened during a sync that fails says so, though the failure may pass. A read of older mail that met trouble
//! that may pass goes on as soon as the account's next sync works, not at its next round.

#![allow(clippy::unwrap_used)]

use std::collections::{BTreeMap, BTreeSet};
use std::io::{BufRead, BufReader, Write};
use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::pin::Pin;
use std::sync::{Arc, Mutex, MutexGuard};
use std::task::{Context, Poll, Waker};
use std::time::{Duration, Instant};

use futures_core::Stream;
use mm_kernel::seams::{Clock, Keychain, KeychainProblem, Moment, Outside, Secret, Tripped};
use mm_mailbox::gates::{Priority, Wait, on_gate};
use mm_mailbox::lists::Paging;
use mm_server::environment::{Environment, Settings};
use mm_server::events::TabStream;
use mm_server::workers::{OLDER_MAIL, Workers};
use mm_server::{App, Seams};
use rcgen::{CertificateParams, KeyPair};
use rustls::pki_types::{PrivateKeyDer, PrivatePkcs8KeyDer};
use rustls::{ServerConfig, ServerConnection, StreamOwned};
use serde_json::{Value, json};

const ADA: &str = "ada@example.test";
const BEA: &str = "bea@example.test";
const PASSWORD: &str = "app-password";
const KEY: [u8; 32] = [0x5e; 32];

fn until(mut what: impl FnMut() -> bool) {
    let deadline = Instant::now() + Duration::from_secs(20);
    while !what() {
        assert!(Instant::now() < deadline, "it never came");
        std::thread::sleep(Duration::from_millis(5));
    }
}

/// A short note, as most mail is.
fn note(uid: u32) -> Vec<u8> {
    format!(
        "From: Lin <lin@example.test>\r\nTo: Ada <{ADA}>\r\nSubject: Note {uid}\r\n\
         Date: Mon, 02 Nov 2026 09:00:00 +0000\r\nMessage-ID: <note-{uid}@example.test>\r\n\r\nNote {uid} says hello.\r\n"
    )
    .into_bytes()
}

/// A message of parts: a line of text, then each part named, then a PDF of about 110 KiB when `big`.
fn parts(uid: u32, text: &str, calendar: bool, big: bool) -> Vec<u8> {
    let mut raw = format!(
        "From: Lin <lin@example.test>\r\nTo: Ada <{ADA}>\r\nSubject: Parts {uid}\r\n\
         Date: Mon, 02 Nov 2026 09:00:00 +0000\r\nMessage-ID: <parts-{uid}@example.test>\r\nMIME-Version: 1.0\r\n\
         Content-Type: multipart/mixed; boundary=b\r\n\r\n--b\r\nContent-Type: text/plain\r\n\r\n{text}\r\n"
    );
    if calendar {
        raw.push_str(
            "--b\r\nContent-Type: text/calendar; method=REQUEST\r\n\r\nBEGIN:VCALENDAR\r\nMETHOD:REQUEST\r\n\
             BEGIN:VEVENT\r\nUID:lunch@example.test\r\nDTSTART:20261105T120000Z\r\nDTEND:20261105T130000Z\r\n\
             SUMMARY:Lunch\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n",
        );
    }
    if big {
        raw.push_str(
            "--b\r\nContent-Type: application/pdf; name=plans.pdf\r\nContent-Transfer-Encoding: base64\r\n\r\n",
        );
        raw.push_str(&"JVBERi0xLjQKJcfsj6IKNSAwIG9iago8PC9MZW5ndGggNiAwIFI+PgpzdHJlYW0K\r\n".repeat(1700));
    }
    raw.push_str("--b--\r\n");
    raw.into_bytes()
}

#[derive(Debug, Default)]
struct State {
    passwords: BTreeMap<String, String>,
    /// Each account's INBOX, its messages by UID.
    inbox: BTreeMap<String, BTreeMap<u32, Vec<u8>>>,
    /// Every command each account was sent, without its tag, in order.
    sent: Vec<(String, String)>,
    /// The accounts whose FETCHes wait until they are let go.
    held: BTreeSet<String>,
    /// FETCHes after this many wait until let go too.
    held_after: Option<usize>,
    /// Messages whose opening the server can't send, and those whose headers it can't: a FETCH of either naming one
    /// says NO, sending the rest first where `sends_the_rest` (as Gmail does) and nothing where not.
    unsendable: BTreeSet<u32>,
    headless: BTreeSet<u32>,
    sends_the_rest: bool,
    /// A FETCH of openings naming this message ends the connection unanswered, once.
    cut_at: Option<u32>,
    /// The accounts whose LOGIN the server can't take for now ([UNAVAILABLE]).
    unavailable: BTreeSet<String>,
}

/// An IMAP server on loopback, over TLS from the first byte, with an INBOX for each account. It answers what a sync
/// asks: a SEARCH of every UID or of a range, and a FETCH of the header fields, the first bytes of each message, a
/// whole message or the flags. It can refuse a FETCH that names a message it can't send, and end the connection at
/// one.
#[derive(Clone, Debug)]
struct Imap {
    port: u16,
    state: Arc<Mutex<State>>,
}

impl Imap {
    fn start() -> Imap {
        let key = KeyPair::generate().unwrap();
        let cert = CertificateParams::new(vec!["127.0.0.1".to_owned()]).unwrap().self_signed(&key).unwrap();
        let config = ServerConfig::builder_with_provider(Arc::new(rustls::crypto::ring::default_provider()))
            .with_safe_default_protocol_versions()
            .unwrap()
            .with_no_client_auth()
            .with_single_cert(
                vec![cert.der().clone()],
                PrivateKeyDer::Pkcs8(PrivatePkcs8KeyDer::from(key.serialize_der())),
            )
            .unwrap();
        let config = Arc::new(config);
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let imap = Imap { port: listener.local_addr().unwrap().port(), state: Arc::default() };
        let state = imap.state.clone();
        std::thread::spawn(move || {
            for tcp in listener.incoming() {
                let Ok(tcp) = tcp else { return };
                let tls = StreamOwned::new(ServerConnection::new(config.clone()).unwrap(), tcp);
                let state = state.clone();
                std::thread::spawn(move || {
                    let _ = Connection { io: BufReader::new(tls), state, user: String::new() }.serve();
                });
            }
        });
        imap
    }

    fn held(&self) -> MutexGuard<'_, State> {
        self.state.lock().unwrap()
    }

    fn add(&self, email: &str, messages: impl IntoIterator<Item = (u32, Vec<u8>)>) {
        let mut state = self.held();
        state.passwords.insert(email.to_owned(), PASSWORD.to_owned());
        state.inbox.entry(email.to_owned()).or_default().extend(messages);
    }

    fn hold(&self, email: &str) {
        self.held().held.insert(email.to_owned());
    }

    fn let_go(&self, email: &str) {
        let mut state = self.held();
        state.held.remove(email);
        state.held_after = None;
    }

    /// FETCHes after the first `fetches` wait until let go.
    fn hold_after(&self, fetches: usize) {
        self.held().held_after = Some(fetches);
    }

    /// The server can't send these messages' openings.
    fn refuse_openings(&self, uids: &[u32], sends_the_rest: bool) {
        let mut state = self.held();
        state.unsendable.extend(uids);
        state.sends_the_rest = sends_the_rest;
    }

    /// The server can't send these messages' headers.
    fn refuse_headers(&self, uids: impl IntoIterator<Item = u32>) {
        self.held().headless.extend(uids);
    }

    /// The connection ends at the next FETCH of openings naming this message.
    fn cut_at(&self, uid: u32) {
        self.held().cut_at = Some(uid);
    }

    /// The server can't take the account's login for now, or can again.
    fn unavailable(&self, email: &str, unavailable: bool) {
        let mut state = self.held();
        if unavailable {
            state.unavailable.insert(email.to_owned());
        } else {
            state.unavailable.remove(email);
        }
    }

    /// What `user` was sent that starts with `start`, in order.
    fn sent(&self, user: &str, start: &str) -> Vec<String> {
        let state = self.held();
        state.sent.iter().filter(|(who, c)| who == user && c.starts_with(start)).map(|(_, c)| c.clone()).collect()
    }

    /// The first UID of each FETCH of header fields `user` was sent, in order.
    fn headers_from(&self, user: &str) -> Vec<u32> {
        let fetches = self.sent(user, "UID FETCH ");
        let first = |c: &String| c[10..].split([',', ' ']).next().unwrap().parse().unwrap();
        fetches.iter().filter(|c| c.contains("HEADER.FIELDS")).map(first).collect()
    }
}

/// The UIDs a set names (`1,2,5`, `3:9`, `701:*`), of those held.
fn named(set: &str, held: &BTreeMap<u32, Vec<u8>>) -> Vec<u32> {
    let top = held.keys().max().copied().unwrap_or(0);
    let mut out = BTreeSet::new();
    for part in set.split(',') {
        let (from, to) = match part.split_once(':') {
            Some((from, "*")) => (from.parse::<u32>().unwrap().min(top), top),
            Some((from, to)) => (from.parse().unwrap(), to.parse().unwrap()),
            None => (part.parse().unwrap(), part.parse().unwrap()),
        };
        out.extend(held.keys().copied().filter(|uid| (from..=to).contains(uid)));
    }
    out.into_iter().collect()
}

struct Connection {
    io: BufReader<StreamOwned<ServerConnection, TcpStream>>,
    state: Arc<Mutex<State>>,
    user: String,
}

impl Connection {
    fn say(&mut self, said: &[u8]) -> std::io::Result<()> {
        let stream = self.io.get_mut();
        stream.write_all(said)?;
        stream.flush()
    }

    fn held(&self) -> MutexGuard<'_, State> {
        self.state.lock().unwrap()
    }

    fn serve(mut self) -> std::io::Result<()> {
        self.say(b"* OK [CAPABILITY IMAP4rev1] ready\r\n")?;
        let mut line = String::new();
        loop {
            line.clear();
            if self.io.read_line(&mut line)? == 0 {
                return Ok(());
            }
            let line = line.trim_end_matches(['\r', '\n']).to_owned();
            let (tag, command) = line.split_once(' ').unwrap_or((&line, ""));
            if !self.answer(tag, command)? {
                return Ok(());
            }
        }
    }

    /// Answer one command: false once the connection is to end.
    fn answer(&mut self, tag: &str, command: &str) -> std::io::Result<bool> {
        let verb = command.split(' ').next().unwrap_or("").to_uppercase();
        if verb == "LOGIN" {
            let quoted: Vec<&str> = command.split('"').collect();
            let (user, password) = (quoted.get(1).copied().unwrap_or(""), quoted.get(3).copied().unwrap_or(""));
            if self.held().unavailable.contains(user) {
                self.say(format!("{tag} NO [UNAVAILABLE] try again later\r\n").as_bytes())?;
            } else if self.held().passwords.get(user).is_some_and(|p| p == password) {
                self.user = user.to_owned();
                self.say(format!("{tag} OK [CAPABILITY IMAP4rev1] logged in\r\n").as_bytes())?;
            } else {
                self.say(format!("{tag} NO [AUTHENTICATIONFAILED] invalid credentials\r\n").as_bytes())?;
            }
            return Ok(true);
        }
        let user = self.user.clone();
        // Which of the account's FETCHes this is, if it is one.
        let ordinal = {
            let mut state = self.held();
            state.sent.push((user.clone(), command.to_owned()));
            state.sent.iter().filter(|(who, c)| *who == user && c.starts_with("UID FETCH")).count()
        };
        let inbox = self.held().inbox.get(&user).cloned().unwrap_or_default();
        match verb.as_str() {
            "CAPABILITY" => self.say(format!("* CAPABILITY IMAP4rev1\r\n{tag} OK\r\n").as_bytes())?,
            "SELECT" | "EXAMINE" => {
                let exists = inbox.len();
                self.say(format!("* {exists} EXISTS\r\n* OK [UIDVALIDITY 7] ok\r\n{tag} OK done\r\n").as_bytes())?;
            }
            "LOGOUT" => {
                self.say(format!("* BYE\r\n{tag} OK\r\n").as_bytes())?;
                return Ok(false);
            }
            "UID" if command.to_uppercase().starts_with("UID SEARCH") => {
                let criteria = &command["UID SEARCH ".len()..];
                let found = match criteria.strip_prefix("UID ") {
                    Some(set) => named(set, &inbox),
                    None => inbox.keys().copied().collect(),
                };
                let found: Vec<String> = found.iter().map(u32::to_string).collect();
                self.say(format!("* SEARCH {}\r\n{tag} OK\r\n", found.join(" ")).as_bytes())?;
            }
            "UID" if command.to_uppercase().starts_with("UID FETCH") => {
                while {
                    let state = self.held();
                    state.held.contains(&user) || state.held_after.is_some_and(|after| ordinal > after)
                } {
                    std::thread::sleep(Duration::from_millis(5));
                }
                let (set, items) = command["UID FETCH ".len()..].split_once(' ').unwrap();
                let uids = named(set, &inbox);
                let (openings, headers) = (items.contains("BODY.PEEK[]<0."), items.contains("HEADER.FIELDS"));
                if openings && self.held().cut_at.is_some_and(|cut| uids.contains(&cut)) {
                    self.held().cut_at = None;
                    return Ok(false);
                }
                let (refused, sends_the_rest) = {
                    let state = self.held();
                    let unsendable = |uid: &u32| {
                        (openings && state.unsendable.contains(uid)) || (headers && state.headless.contains(uid))
                    };
                    (uids.iter().copied().filter(unsendable).collect::<BTreeSet<u32>>(), state.sends_the_rest)
                };
                for uid in uids {
                    if !refused.is_empty() && (refused.contains(&uid) || !sends_the_rest) {
                        continue;
                    }
                    let raw = &inbox[&uid];
                    let seq = inbox.keys().position(|u| *u == uid).unwrap() + 1;
                    let (name, data) = if let Some(at) = items.find("BODY.PEEK[HEADER.FIELDS") {
                        let section = &items[at + "BODY.PEEK[".len()..at + items[at..].find(']').unwrap()];
                        let end = raw.windows(4).position(|w| w == b"\r\n\r\n").unwrap() + 4;
                        (format!("FLAGS () BODY[{section}]"), raw[..end].to_vec())
                    } else if let Some(at) = items.find("BODY.PEEK[]<0.") {
                        let rest = &items[at + "BODY.PEEK[]<0.".len()..];
                        let bytes: usize = rest[..rest.find('>').unwrap()].parse().unwrap();
                        ("BODY[]<0>".to_owned(), raw[..raw.len().min(bytes)].to_vec())
                    } else if items.contains("BODY.PEEK[]") {
                        ("BODY[]".to_owned(), raw.clone())
                    } else {
                        self.say(format!("* {seq} FETCH (UID {uid} FLAGS ())\r\n").as_bytes())?;
                        continue;
                    };
                    let mut said = format!("* {seq} FETCH (UID {uid} {name} {{{}}}\r\n", data.len()).into_bytes();
                    said.extend(data);
                    said.extend(b")\r\n");
                    self.say(&said)?;
                }
                if refused.is_empty() {
                    self.say(format!("{tag} OK done\r\n").as_bytes())?;
                } else {
                    self.say(format!("{tag} NO Some messages could not be FETCHed (Failure)\r\n").as_bytes())?;
                }
            }
            _ => self.say(format!("{tag} OK\r\n").as_bytes())?,
        }
        Ok(true)
    }
}

#[derive(Debug)]
struct Frozen;

impl Clock for Frozen {
    fn now(&self) -> Moment {
        Moment::from_micros(1_793_620_800_000_000)
    }
}

#[derive(Debug)]
struct Loopback;

impl Outside for Loopback {
    fn open(&self, _path: &Path) -> Result<(), Tripped> {
        Ok(())
    }

    fn reach(&self, host: &str) -> Result<(), Tripped> {
        if host == "127.0.0.1" { Ok(()) } else { Err(Tripped(host.to_owned())) }
    }

    fn run(&self, program: &str) -> Result<(), Tripped> {
        Err(Tripped(program.to_owned()))
    }
}

/// A Keychain that keeps what it is given.
#[derive(Debug, Default)]
struct Kept(Mutex<BTreeMap<String, String>>);

impl Keychain for Kept {
    fn find(&self, item: &str) -> Result<Option<Secret>, KeychainProblem> {
        Ok(self.0.lock().unwrap().get(item).map(|s| Secret::new(s.clone())))
    }

    fn put(&self, item: &str, secret: &Secret) -> Result<(), KeychainProblem> {
        self.0.lock().unwrap().insert(item.to_owned(), secret.expose().to_owned());
        Ok(())
    }

    fn delete(&self, item: &str) -> Result<bool, KeychainProblem> {
        Ok(self.0.lock().unwrap().remove(item).is_some())
    }
}

struct World {
    app: Arc<App>,
    imap: Imap,
    home: tempfile::TempDir,
    keychain: Arc<Kept>,
}

/// The app over a data directory of a database at the newest schema, its accounts on one IMAP server on loopback.
fn world(imap: Imap, emails: &[&str]) -> World {
    world_as(imap, emails, "full")
}

/// The same, run with the profile named (`full` or `read-only`).
fn world_as(imap: Imap, emails: &[&str], profile: &str) -> World {
    let keychain = Arc::new(Kept::default());
    for email in emails {
        keychain.put(&format!("account:{email}"), &Secret::new(PASSWORD.to_owned())).unwrap();
    }
    let home = tempfile::tempdir().unwrap();
    let accounts: Vec<Value> = emails
        .iter()
        .map(|email| {
            json!({"email": email, "type": "imap", "host": "127.0.0.1", "port": imap.port, "starttls": false,
                   "credential": {"keychain": format!("account:{email}")}})
        })
        .collect();
    std::fs::write(home.path().join("mailmind.config.json"), json!({"accounts": accounts}).to_string()).unwrap();
    database(home.path()).ready(&Frozen).unwrap();
    let app = assemble(home.path(), &keychain, profile);
    World { app, imap, home, keychain }
}

fn database(home: &Path) -> mm_store::Database {
    mm_store::Database::new(home.join("mailmind.db"), mm_store::DbKey::from_bytes(KEY), Arc::new(Loopback))
}

/// The app over `home`, as a start assembles it.
fn assemble(home: &Path, keychain: &Arc<Kept>, profile: &str) -> Arc<App> {
    let hex = "5e".repeat(32);
    let environment = Environment::from_pairs([("MAILMIND_DB_KEY", hex.as_str()), ("MAILMIND_PROFILE", profile)]);
    let settings = Settings { home: home.to_path_buf() };
    let seams = Seams {
        clock: Arc::new(Frozen),
        outside: Arc::new(Loopback),
        keychain: keychain.clone(),
        sanitizer: Arc::new(mm_server::real::Sanitizing),
        mail_hosts: Arc::new(mm_server::mail_hosts::NoLookups),
        local_ai: Arc::new(mm_server::local_ai::NoLocalServers),
    };
    let dist: PathBuf = home.join("dist");
    Arc::new(App::assemble(&environment, &settings, dist, 1, &seams).unwrap())
}

impl World {
    /// The UIDs stored of the account's INBOX, in order.
    fn stored(&self, email: &str) -> Vec<u32> {
        database(self.home.path())
            .read(|c| {
                let mut rows = c.prepare("SELECT uid FROM messages WHERE account=? AND folder='INBOX' ORDER BY uid")?;
                rows.query_map([email], |r| r.get(0))?.collect()
            })
            .unwrap()
    }

    /// The UIDs of the account's INBOX whose whole message is kept, in order.
    fn kept_whole(&self, email: &str) -> Vec<u32> {
        database(self.home.path())
            .read(|c| {
                let mut rows = c.prepare(
                    "SELECT m.uid FROM bodies b JOIN messages m ON m.pk=b.message_pk WHERE m.account=? ORDER BY m.uid",
                )?;
                rows.query_map([email], |r| r.get(0))?.collect()
            })
            .unwrap()
    }

    fn snippet(&self, email: &str, uid: u32) -> String {
        database(self.home.path())
            .read(|c| {
                c.query_row(
                    "SELECT snippet FROM messages WHERE account=? AND folder='INBOX' AND uid=?",
                    (email, uid),
                    |r| r.get(0),
                )
            })
            .unwrap()
    }

    /// The account's sync as the digest says it, which the page and the Mac app's status read.
    fn said(&self, email: &str) -> Value {
        self.app.reading.digest.answer().unwrap()["status"][email].clone()
    }

    /// The background reads older mail here, as `Workers::start` has it read: its rounds are run by hand.
    fn reads_older(&self) {
        self.app.jobs.add(OLDER_MAIL, 600, 600);
    }

    /// How many rounds of reading older mail have ended.
    fn older_rounds(&self) -> u64 {
        let jobs = self.app.jobs.snapshot();
        let older = jobs.as_array().unwrap().iter().find(|job| job["name"] == OLDER_MAIL).cloned();
        older.map_or(0, |job| job["runs"].as_u64().unwrap())
    }
}

fn notes(from: u32, to: u32) -> Vec<(u32, Vec<u8>)> {
    (from..=to).map(|uid| (uid, note(uid))).collect()
}

fn range(from: u32, to: u32) -> Vec<u32> {
    (from..=to).collect()
}

#[test]
fn a_first_sync_stores_the_newest_500_then_the_rest_newest_first_and_says_how_far_it_got() {
    let imap = Imap::start();
    imap.add(ADA, notes(1, 1200));
    let w = world(imap, &[ADA]);
    w.reads_older();
    w.app.syncing.sync_once();
    assert_eq!(w.stored(ADA), range(701, 1200));
    let said = w.said(ADA);
    assert_eq!(
        (said["health"].clone(), said["done"].clone(), said["total"].clone()),
        (json!("ok"), json!(500), json!(1200))
    );
    // Each step's headers in batches of 300, the step's own oldest first.
    assert_eq!(w.imap.headers_from(ADA), [701, 1001]);
    assert!(!w.app.syncing.read_older().unwrap(), "two steps of 500 read the rest");
    assert_eq!(w.stored(ADA), range(1, 1200));
    assert_eq!(w.imap.headers_from(ADA), [701, 1001, 201, 501, 1]);
    assert_eq!(w.imap.sent(ADA, "UID SEARCH"), ["UID SEARCH ALL", "UID SEARCH UID 1:700"]);
    let said = w.said(ADA);
    assert!(said.get("done").is_none() && said.get("total").is_none(), "{said}");
    // Every message's text came from its opening: none was fetched whole.
    let fetches = w.imap.sent(ADA, "UID FETCH ");
    assert!(fetches.iter().all(|c| !c.ends_with("(BODY.PEEK[])")), "a whole message was fetched");
    assert_eq!(fetches.iter().filter(|c| c.ends_with("(UID BODY.PEEK[]<0.65536>)")).count(), 24);
    assert_eq!(w.snippet(ADA, 3), "Note 3 says hello.");
}

#[test]
fn a_first_scan_stopped_part_way_goes_on_where_its_last_step_left_it() {
    let imap = Imap::start();
    imap.add(ADA, notes(1, 3000));
    let w = world(imap, &[ADA]);
    w.reads_older();
    w.app.syncing.sync_once();
    assert!(w.app.syncing.read_older().unwrap(), "four steps a round leave one");
    assert_eq!(w.stored(ADA), range(501, 3000));
    assert_eq!((w.said(ADA)["done"].clone(), w.said(ADA)["total"].clone()), (json!(2500), json!(3000)));
    // MailMind quits, and starts again.
    let World { app, imap, home, keychain } = w;
    drop(app);
    let w = World { app: assemble(home.path(), &keychain, "full"), imap, home, keychain };
    assert!(!w.app.syncing.read_older().unwrap());
    assert_eq!(w.stored(ADA), range(1, 3000));
    assert_eq!(w.imap.sent(ADA, "UID SEARCH").last().unwrap(), "UID SEARCH UID 1:500");
}

#[test]
fn only_an_invite_whose_opening_was_cut_off_is_fetched_whole() {
    let imap = Imap::start();
    imap.add(
        ADA,
        [
            (1, parts(1, "The plans, attached.", false, true)),
            (2, parts(2, "Lunch?", true, false)),
            (3, parts(3, "Lunch, and the plans.", true, true)),
        ],
    );
    let w = world(imap, &[ADA]);
    w.app.syncing.sync_once();
    assert_eq!(w.stored(ADA), [1, 2, 3]);
    assert_eq!(
        w.imap.sent(ADA, "UID FETCH ").iter().filter(|c| c.ends_with("(BODY.PEEK[])")).collect::<Vec<_>>(),
        ["UID FETCH 3 (BODY.PEEK[])"]
    );
    // Today & next reads invites from stored bodies: both are kept, whole.
    assert_eq!(w.kept_whole(ADA), [2, 3]);
    assert_eq!(w.snippet(ADA, 1), "The plans, attached.");
}

#[test]
fn a_first_sync_is_no_new_mail_and_what_comes_after_it_is() {
    let imap = Imap::start();
    imap.add(ADA, notes(1, 3));
    let w = world(imap, &[ADA]);
    let runtime = tokio::runtime::Builder::new_current_thread().enable_time().build().unwrap();
    let _entered = runtime.enter();
    let mut tab = TabStream::new(w.app.events.listen(None).unwrap(), "b1");
    let mut frames = String::new();
    let mut heard = |frames: &mut String| {
        while let Poll::Ready(Some(Ok(frame))) = Pin::new(&mut tab).poll_next(&mut Context::from_waker(Waker::noop())) {
            frames.push_str(&frame);
        }
    };
    w.app.syncing.sync_once();
    heard(&mut frames);
    assert!(frames.contains("event: sync-done"), "{frames}");
    assert!(!frames.contains("event: new-mail"), "{frames}");
    assert_eq!(w.app.syncing.sync().unwrap().result, "0 new");
    w.imap.add(ADA, notes(4, 5));
    w.app.syncing.sync_once();
    heard(&mut frames);
    assert!(
        frames.contains(
            "event: new-mail\ndata: {\"account\": \"ada@example.test\", \"folder\": \"INBOX\", \"count\": 2}"
        ),
        "{frames}"
    );
    assert_eq!(w.app.syncing.sync().unwrap().result, "2 new");
}

#[test]
fn what_a_first_sync_kept_is_summarized_at_once_not_at_the_next_round() {
    let imap = Imap::start();
    imap.add(ADA, notes(1, 3));
    let w = world(imap, &[ADA, BEA]);
    // Bea's Inbox is empty: her first sync keeps nothing to summarize.
    w.app.syncing.sync_guarded(Some(BTreeSet::from([BEA.to_owned()])));
    assert!(w.app.jobs.kicked().is_empty());
    // Ada's keeps three, none of them new: the summaries' round is asked for now all the same.
    w.app.syncing.sync_guarded(Some(BTreeSet::from([ADA.to_owned()])));
    assert_eq!(w.app.syncing.sync().unwrap().result, "0 new");
    assert_eq!(w.app.jobs.kicked(), ["assess"]);
    // A sync that finds nothing more asks for none; one that finds new mail does, as before.
    w.app.syncing.sync_once();
    assert!(w.app.jobs.kicked().is_empty());
    w.imap.add(ADA, notes(4, 4));
    w.app.syncing.sync_once();
    assert_eq!(w.app.jobs.kicked(), ["assess"]);
}

#[test]
fn one_accounts_slow_server_holds_up_no_other() {
    let imap = Imap::start();
    imap.add(ADA, notes(1, 3));
    imap.add(BEA, notes(1, 3));
    imap.hold(ADA);
    let w = world(imap, &[ADA, BEA]);
    std::thread::scope(|scope| {
        let round = scope.spawn(|| w.app.syncing.sync_once());
        until(|| w.stored(BEA).len() == 3 && w.said(BEA)["health"] == "ok");
        assert_eq!(w.said(ADA)["health"], "syncing");
        assert!(w.stored(ADA).is_empty());
        w.imap.let_go(ADA);
        round.join().unwrap();
    });
    assert_eq!(w.stored(ADA), [1, 2, 3]);
}

#[test]
fn an_inbox_opened_during_its_first_sync_joins_it_and_a_message_opens_meanwhile() {
    let imap = Imap::start();
    imap.add(ADA, notes(1, 3));
    imap.hold(ADA);
    let w = &world(imap, &[ADA]);
    std::thread::scope(|scope| {
        let round = scope.spawn(|| w.app.syncing.sync_once());
        until(|| w.imap.sent(ADA, "UID FETCH ").len() == 1);
        let page = Paging { offset: 0, limit: 50, grouped: false };
        let opened = scope.spawn(move || w.app.reading.lists.messages_in(Some(ADA), "INBOX", page).unwrap());
        std::thread::sleep(Duration::from_millis(300));
        assert_eq!(w.imap.sent(ADA, "UID SEARCH").len(), 1, "the open read the Inbox a second time");
        assert!(!w.app.gates.held(ADA));
        assert_eq!(on_gate(&*w.app.gates, ADA, Priority::Interactive, Wait::Not, || 7), Ok(7));
        w.imap.let_go(ADA);
        round.join().unwrap();
        let listed = opened.join().unwrap();
        assert_eq!(listed["items"].as_array().unwrap().len(), 3);
        assert!(listed.get("syncing").is_none(), "{listed}");
    });
}

#[test]
fn a_process_that_only_reads_reads_a_first_scans_older_mail_too_and_says_how_far_it_got() {
    let imap = Imap::start();
    imap.add(ADA, notes(1, 1200));
    // The first sync's FETCHes go, two of headers and ten of openings; what comes after waits.
    imap.hold_after(12);
    let w = world_as(imap, &[ADA], "read-only");
    let workers = Workers::start(&w.app, true, 0, 0);
    // Nothing syncs in the background: opening the Inbox reads it first.
    let page = Paging { offset: 0, limit: 50, grouped: false };
    w.app.reading.lists.messages_in(Some(ADA), "INBOX", page).unwrap();
    until(|| w.said(ADA)["health"] == "ok");
    assert_eq!(w.stored(ADA), range(701, 1200));
    let said = w.said(ADA);
    assert_eq!((said["done"].clone(), said["total"].clone()), (json!(500), json!(1200)));
    w.imap.let_go(ADA);
    until(|| w.stored(ADA) == range(1, 1200) && w.said(ADA).get("done").is_none());
    workers.stop();
    assert_eq!(w.imap.sent(ADA, "UID SEARCH"), ["UID SEARCH ALL", "UID SEARCH UID 1:700"]);
    assert_eq!(w.snippet(ADA, 3), "Note 3 says hello.");
}

#[test]
fn a_message_whose_opening_the_server_refuses_costs_only_its_own_text() {
    for sends_the_rest in [true, false] {
        let imap = Imap::start();
        imap.add(ADA, notes(1, 120));
        imap.refuse_openings(&[60], sends_the_rest);
        let w = world(imap, &[ADA]);
        w.app.syncing.sync_once();
        assert_eq!(w.said(ADA)["health"], "ok");
        assert_eq!(w.stored(ADA), range(1, 120));
        assert_eq!(w.snippet(ADA, 60), "");
        for uid in (1..=120).filter(|uid| *uid != 60) {
            assert_eq!(w.snippet(ADA, uid), format!("Note {uid} says hello."), "{uid}");
        }
        // Of the FETCH the server refused, only what it didn't send was asked again, each alone.
        let alone: Vec<String> = w
            .imap
            .sent(ADA, "UID FETCH ")
            .into_iter()
            .filter(|c| c.contains("<0.") && !c.split(' ').nth(2).unwrap().contains(','))
            .collect();
        let again: Vec<u32> = if sends_the_rest { vec![60] } else { range(51, 100) };
        let asked: Vec<String> =
            again.iter().map(|uid| format!("UID FETCH {uid} (UID BODY.PEEK[]<0.65536>)")).collect();
        assert_eq!(alone, asked);
    }
}

#[test]
fn a_connection_lost_while_openings_are_read_stores_nothing_of_the_step_and_the_next_sync_reads_it() {
    let imap = Imap::start();
    imap.add(ADA, notes(1, 3));
    imap.cut_at(2);
    let w = world(imap, &[ADA]);
    w.app.syncing.sync_once();
    // A connection lost may pass: the account says only that it is trying again, nothing louder unless the next sync
    // fails too.
    assert_eq!(w.said(ADA), json!({"health": "", "msg": "", "trying_again": true}));
    assert!(w.stored(ADA).is_empty(), "mail was stored with no text");
    w.app.syncing.sync_once();
    assert_eq!(w.said(ADA)["health"], "ok");
    assert_eq!(w.stored(ADA), [1, 2, 3]);
    assert_eq!(w.snippet(ADA, 2), "Note 2 says hello.");
}

#[test]
fn a_read_of_older_mail_leaves_out_a_message_whose_headers_the_server_refuses_and_reaches_the_oldest() {
    let imap = Imap::start();
    imap.add(ADA, notes(1, 1200));
    imap.refuse_headers([300]);
    let w = world(imap, &[ADA]);
    w.reads_older();
    w.app.syncing.sync_once();
    assert_eq!(w.stored(ADA), range(701, 1200));
    assert!(!w.app.syncing.read_older().unwrap(), "the scan stopped short of the oldest");
    let all_but: Vec<u32> = (1..=1200).filter(|uid| *uid != 300).collect();
    assert_eq!(w.stored(ADA), all_but);
    let said = w.said(ADA);
    assert!(said.get("done").is_none() && said.get("total").is_none(), "{said}");
    // The refused FETCH's messages were asked alone.
    assert_eq!(w.imap.sent(ADA, "UID FETCH 300 ").len(), 1);
    assert_eq!(w.imap.headers_from(ADA).iter().filter(|uid| (201..=500).contains(*uid)).count(), 301);
}

#[test]
fn a_step_of_older_mail_none_of_which_the_server_sends_stores_nothing_and_the_scan_waits() {
    let imap = Imap::start();
    imap.add(ADA, notes(1, 1200));
    imap.refuse_headers(1..=700);
    let w = world(imap, &[ADA]);
    w.reads_older();
    w.app.syncing.sync_once();
    assert!(!w.app.syncing.read_older().unwrap());
    // The folder isn't taken as read whole: the next round asks again.
    assert_eq!(w.stored(ADA), range(701, 1200));
    let said = w.said(ADA);
    assert_eq!((said["done"].clone(), said["total"].clone()), (json!(500), json!(1200)));
}

#[test]
fn a_read_of_older_mail_that_met_trouble_that_may_pass_goes_on_as_soon_as_the_next_sync_works() {
    let imap = Imap::start();
    imap.add(ADA, notes(1, 600));
    let w = world(imap, &[ADA]);
    w.app.syncing.sync_once();
    assert_eq!(w.stored(ADA), range(101, 600));
    // The background's first round of older mail finds the server can't take a login for now.
    w.imap.unavailable(ADA, true);
    let workers = Workers::start(&w.app, true, 0, 0);
    w.app.jobs.kick(OLDER_MAIL);
    until(|| w.older_rounds() == 1);
    assert_eq!(w.stored(ADA), range(101, 600));
    // It takes one again, and the next sync works: the rest is read now, not at the round's next turn ten minutes on.
    w.imap.unavailable(ADA, false);
    w.app.syncing.sync_once();
    until(|| w.stored(ADA) == range(1, 600));
    workers.stop();
}

#[test]
fn a_sync_that_fails_while_a_first_scan_reads_older_mail_keeps_saying_how_far_it_got() {
    let imap = Imap::start();
    imap.add(ADA, notes(1, 1200));
    let w = world(imap, &[ADA]);
    w.reads_older();
    w.app.syncing.sync_once();
    let standing = |said: Value| (said["health"].clone(), said["done"].clone(), said["total"].clone());
    assert_eq!(standing(w.said(ADA)), (json!("ok"), json!(500), json!(1200)));
    // The server can't take a login for now, which may pass: the account shows as it did, how far it got with it.
    w.imap.unavailable(ADA, true);
    w.app.syncing.sync_once();
    assert_eq!(standing(w.said(ADA)), (json!("ok"), json!(500), json!(1200)));
    // A second in a row shows, and still says how far the scan got.
    w.app.syncing.sync_once();
    assert_eq!(standing(w.said(ADA)), (json!("error"), json!(500), json!(1200)));
    assert_eq!(w.said(ADA)["msg"], "LoginUnavailable");
    // Once the server takes it again, the older mail is read to the oldest and the next sync works.
    w.imap.unavailable(ADA, false);
    assert!(!w.app.syncing.read_older().unwrap());
    assert_eq!(w.stored(ADA), range(1, 1200));
    w.app.syncing.sync_once();
    let said = w.said(ADA);
    assert_eq!(said["health"], "ok");
    assert!(said.get("done").is_none() && said.get("total").is_none(), "{said}");
}

#[test]
fn an_inbox_opened_during_a_first_sync_that_fails_in_a_way_that_may_pass_says_its_sync_failed() {
    let imap = Imap::start();
    imap.add(ADA, notes(1, 3));
    let w = world(imap, &[ADA]);
    w.app.syncing.sync_once();
    assert_eq!(w.said(ADA)["health"], "ok");
    // The account is read afresh, as after it was removed and added again: no scan of its Inbox has begun.
    database(w.home.path()).write(|c| c.execute("DELETE FROM sync_state WHERE account=?", [ADA])).unwrap();
    w.imap.unavailable(ADA, true);
    let page = Paging { offset: 0, limit: 50, grouped: false };
    let listed = w.app.reading.lists.messages_in(Some(ADA), "INBOX", page).unwrap();
    assert_eq!(listed["sync_failed"], true, "{listed}");
    // The account still shows as it did: a failure that may pass shows once the next fails too.
    assert_eq!(w.said(ADA)["health"], "ok");
}

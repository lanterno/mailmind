//! An account's first sync on the Rust server, against an IMAP server on loopback that holds fictional mail and writes
//! down what each account was sent: the Inbox's newest 500 stored at once, the rest newest first a few steps a round,
//! each step one transaction, so a round that stops goes on where the last left off; each message's text read from
//! its first 64 KiB, only an invite cut off fetched whole; how far the scan has got in the sync's status; none of it
//! new mail; and each account syncing on its own.

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
use mm_server::environment::{Environment, Settings};
use mm_server::events::TabStream;
use mm_server::workers::OLDER_MAIL;
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
}

/// An IMAP server on loopback, over TLS from the first byte, with an INBOX for each account. It answers what a sync
/// asks: a SEARCH of every UID or of a range, and a FETCH of the header fields, the first bytes of each message, a
/// whole message or the flags.
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
        self.held().held.remove(email);
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
            if self.held().passwords.get(user).is_some_and(|p| p == password) {
                self.user = user.to_owned();
                self.say(format!("{tag} OK [CAPABILITY IMAP4rev1] logged in\r\n").as_bytes())?;
            } else {
                self.say(format!("{tag} NO [AUTHENTICATIONFAILED] invalid credentials\r\n").as_bytes())?;
            }
            return Ok(true);
        }
        let user = self.user.clone();
        self.held().sent.push((user.clone(), command.to_owned()));
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
                while self.held().held.contains(&user) {
                    std::thread::sleep(Duration::from_millis(5));
                }
                let (set, items) = command["UID FETCH ".len()..].split_once(' ').unwrap();
                for uid in named(set, &inbox) {
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
                self.say(format!("{tag} OK done\r\n").as_bytes())?;
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
    let app = assemble(home.path(), &keychain);
    World { app, imap, home, keychain }
}

fn database(home: &Path) -> mm_store::Database {
    mm_store::Database::new(home.join("mailmind.db"), mm_store::DbKey::from_bytes(KEY), Arc::new(Loopback))
}

/// The app over `home`, as a start assembles it.
fn assemble(home: &Path, keychain: &Arc<Kept>) -> Arc<App> {
    let hex = "5e".repeat(32);
    let environment = Environment::from_pairs([("MAILMIND_DB_KEY", hex.as_str()), ("MAILMIND_PROFILE", "full")]);
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
    let w = World { app: assemble(home.path(), &keychain), imap, home, keychain };
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

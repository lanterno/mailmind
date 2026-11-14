//! IDLE on the Rust server (`idle_wiring`), against an IMAP server on loopback that speaks IDLE and counts what each
//! account is sent: one worker for each account, logging in as every session does, so a refused login is held back;
//! the workers following the accounts as they are added, changed and removed; new mail heard in IDLE syncing that
//! account, which the open tabs hear of; and `Workers::start` starting them unless the process only reads (the
//! Python app's tests/integration/mailbox/test_idle_supervisor.py and tests/characterization/mailbox/
//! test_pool_and_idle.py). mm-mailbox's tests/realtime.rs holds the workers' own rules over sessions of its own.

#![allow(clippy::unwrap_used)]

use std::collections::{BTreeMap, BTreeSet};
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::Path;
use std::pin::Pin;
use std::sync::{Arc, Mutex, MutexGuard};
use std::task::{Context, Poll, Waker};
use std::time::{Duration, Instant};

use futures_core::Stream;
use mm_kernel::seams::{Clock, Keychain, KeychainProblem, Moment, Outside, Secret, Tripped};
use mm_mailbox::idle_supervisor::Rests;
use mm_mailbox::realtime::Pace;
use mm_server::environment::{Environment, Settings};
use mm_server::events::TabStream;
use mm_server::workers::Workers;
use mm_server::{App, Seams};
use rcgen::{CertificateParams, KeyPair};
use rustls::pki_types::{PrivateKeyDer, PrivatePkcs8KeyDer};
use rustls::{ServerConfig, ServerConnection, StreamOwned};
use serde_json::{Map, Value, json};

const ADA: &str = "ada@example.test";
const BEA: &str = "bea@example.test";
const CY: &str = "cy@example.test";
const DEE: &str = "dee@example.test";
const PASSWORD: &str = "app-password";

fn until(mut what: impl FnMut() -> bool) {
    let deadline = Instant::now() + Duration::from_secs(20);
    while !what() {
        assert!(Instant::now() < deadline, "it never came");
        std::thread::sleep(Duration::from_millis(5));
    }
}

#[derive(Debug, Default)]
struct State {
    passwords: BTreeMap<String, String>,
    /// Every command each connection sent, with whose it was: the user it logged in as, or the one LOGIN named.
    sent: Vec<(usize, String, String)>,
    /// How many messages each INBOX holds.
    delivered: BTreeMap<String, u32>,
    /// Logins answered with a refusal, whatever the password.
    refusing: BTreeSet<String>,
    without_idle: bool,
}

/// An IMAP server on loopback, over TLS from the first byte, with an INBOX for each account. It answers enough for a
/// sync of an INBOX that holds nothing it lists, says `* n EXISTS` to an IDLE when mail is delivered, and records
/// every command.
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
            for (n, tcp) in listener.incoming().enumerate() {
                let Ok(tcp) = tcp else { return };
                let tls = StreamOwned::new(ServerConnection::new(config.clone()).unwrap(), tcp);
                let state = state.clone();
                std::thread::spawn(move || {
                    let _ = Connection { n, tls, state, user: None, idling: None }.serve();
                });
            }
        });
        imap
    }

    fn held(&self) -> MutexGuard<'_, State> {
        self.state.lock().unwrap()
    }

    fn add(&self, email: &str, password: &str) {
        self.held().passwords.insert(email.to_owned(), password.to_owned());
    }

    fn deliver(&self, email: &str) {
        *self.held().delivered.entry(email.to_owned()).or_default() += 1;
    }

    fn count(&self, user: &str, command: &str) -> usize {
        self.held().sent.iter().filter(|(_, who, c)| who == user && c == command).count()
    }

    fn idling(&self, user: &str, n: usize) -> bool {
        self.count(user, "IDLE") >= n
    }

    /// What the connection that sent `user`'s first IDLE sent, in order.
    fn idle_connection(&self, user: &str) -> Vec<String> {
        let state = self.held();
        let Some((n, _, _)) = state.sent.iter().find(|(_, who, c)| who == user && c == "IDLE") else {
            return Vec::new();
        };
        state.sent.iter().filter(|(m, _, _)| m == n).map(|(_, _, c)| c.clone()).collect()
    }
}

struct Connection {
    n: usize,
    tls: StreamOwned<ServerConnection, TcpStream>,
    state: Arc<Mutex<State>>,
    user: Option<String>,
    /// The IDLE's tag, and how many messages its INBOX held when last said.
    idling: Option<(String, u32)>,
}

impl Connection {
    fn say(&mut self, said: &str) -> std::io::Result<()> {
        self.tls.write_all(said.as_bytes())?;
        self.tls.flush()
    }

    fn held(&self) -> MutexGuard<'_, State> {
        self.state.lock().unwrap()
    }

    fn serve(mut self) -> std::io::Result<()> {
        // The handshake, then reads that wait moments, so mail delivered reaches an IDLE between them.
        self.say("* OK [CAPABILITY IMAP4rev1] ready\r\n")?;
        self.tls.sock.set_read_timeout(Some(Duration::from_millis(10)))?;
        let mut pending = Vec::new();
        loop {
            while let Some(end) = pending.windows(2).position(|w| w == b"\r\n") {
                let line = String::from_utf8_lossy(&pending[..end]).into_owned();
                pending.drain(..end + 2);
                if !self.answer(&line)? {
                    return Ok(());
                }
            }
            if let (Some(user), Some((_, said))) = (self.user.clone(), self.idling.clone()) {
                let holds = self.held().delivered.get(&user).copied().unwrap_or(0);
                if holds > said {
                    self.idling.as_mut().unwrap().1 = holds;
                    self.say(&format!("* {holds} EXISTS\r\n"))?;
                }
            }
            let mut chunk = [0u8; 4096];
            match self.tls.read(&mut chunk) {
                Ok(0) => return Ok(()),
                Ok(n) => pending.extend_from_slice(&chunk[..n]),
                Err(e) if matches!(e.kind(), std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut) => {}
                Err(e) => return Err(e),
            }
        }
    }

    fn record(&self, user: &str, command: &str) {
        self.held().sent.push((self.n, user.to_owned(), command.to_owned()));
    }

    fn capabilities(&self) -> &'static str {
        if self.held().without_idle { "IMAP4rev1" } else { "IMAP4rev1 IDLE" }
    }

    /// Answer one line: false once the connection is to end.
    fn answer(&mut self, line: &str) -> std::io::Result<bool> {
        let user = self.user.clone().unwrap_or_default();
        if let Some((tag, _)) = self.idling.clone() {
            if line.eq_ignore_ascii_case("DONE") {
                self.record(&user, "DONE");
                self.idling = None;
                self.say(&format!("{tag} OK IDLE terminated\r\n"))?;
            }
            return Ok(true);
        }
        let mut words = line.split(' ');
        let tag = words.next().unwrap_or("*").to_owned();
        let mut command = words.next().unwrap_or("").to_uppercase();
        if command == "UID" {
            command = format!("UID {}", words.next().unwrap_or("").to_uppercase());
        }
        if command == "LOGIN" {
            let quoted: Vec<&str> = line.split('"').collect();
            let (asked, password) = (quoted.get(1).copied().unwrap_or(""), quoted.get(3).copied().unwrap_or(""));
            self.record(asked, "LOGIN");
            let (refusing, right) = {
                let state = self.held();
                (state.refusing.contains(asked), state.passwords.get(asked).is_some_and(|p| p == password))
            };
            if refusing || !right {
                self.say(&format!("{tag} NO [AUTHENTICATIONFAILED] invalid credentials\r\n"))?;
            } else {
                self.user = Some(asked.to_owned());
                let capabilities = self.capabilities();
                self.say(&format!("{tag} OK [CAPABILITY {capabilities}] logged in\r\n"))?;
            }
            return Ok(true);
        }
        self.record(&user, &command);
        match command.as_str() {
            "CAPABILITY" => {
                let capabilities = self.capabilities();
                self.say(&format!("* CAPABILITY {capabilities}\r\n{tag} OK\r\n"))?;
            }
            "SELECT" | "EXAMINE" => {
                let holds = self.held().delivered.get(&user).copied().unwrap_or(0);
                self.say(&format!("* {holds} EXISTS\r\n* OK [UIDVALIDITY 7] ok\r\n{tag} OK done\r\n"))?;
            }
            "IDLE" if self.held().without_idle => self.say(&format!("{tag} BAD unknown command\r\n"))?,
            "IDLE" => {
                let holds = self.held().delivered.get(&user).copied().unwrap_or(0);
                self.idling = Some((tag, holds));
                self.say("+ idling\r\n")?;
            }
            "UID SEARCH" => self.say(&format!("* SEARCH\r\n{tag} OK\r\n"))?,
            "LOGOUT" => {
                self.say(&format!("* BYE\r\n{tag} OK\r\n"))?;
                return Ok(false);
            }
            _ => self.say(&format!("{tag} OK\r\n"))?,
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

/// What a sync of an INBOX touches beyond the list's tables, as the newest schema golden has it.
const TABLES: &str = "
CREATE TABLE sync_state (account TEXT NOT NULL, folder TEXT NOT NULL DEFAULT 'INBOX', last_sync TEXT,
  last_uid INTEGER DEFAULT 0, uidvalidity INTEGER, last_ok_ts TEXT, last_error TEXT, last_error_ts TEXT,
  last_error_kind TEXT, sync_count INTEGER DEFAULT 0, scan_state TEXT NOT NULL DEFAULT 'never',
  highest_modseq INTEGER, PRIMARY KEY(account, folder));
CREATE TABLE snoozes(message_key INTEGER NOT NULL REFERENCES message_identity(key) ON DELETE CASCADE,
  folder TEXT NOT NULL, until INTEGER NOT NULL, PRIMARY KEY(message_key, folder));
CREATE TABLE mail_commands(id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, message_id TEXT, src_folder TEXT NOT NULL, dest TEXT,
  params TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt_at INTEGER NOT NULL DEFAULT 0, applied INTEGER NOT NULL DEFAULT 0, begun INTEGER NOT NULL DEFAULT 0,
  outcome TEXT, last_error TEXT, created_at TEXT NOT NULL, done_at TEXT);
CREATE TABLE followups (account TEXT NOT NULL, thread_id TEXT NOT NULL, kind TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'pending', first_sent_ts INTEGER, last_msg_ts INTEGER, due_ts INTEGER, created_at TEXT,
  resolved_at TEXT, PRIMARY KEY(account, thread_id));
CREATE TABLE muted (account TEXT NOT NULL, thread_id TEXT NOT NULL, muted_at TEXT, PRIMARY KEY(account, thread_id));
";

struct World {
    app: Arc<App>,
    imap: Imap,
    home: tempfile::TempDir,
}

impl Drop for World {
    fn drop(&mut self) {
        self.app.realtime.stop();
        let deadline = Instant::now() + Duration::from_secs(30);
        while self.app.realtime.running() > 0 && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(5));
        }
        let left = self.app.realtime.running();
        self.app.realtime.join();
        if !std::thread::panicking() {
            assert_eq!(left, 0, "an IDLE worker didn't stop");
        }
    }
}

fn entry(email: &str, port: u16) -> Value {
    json!({"email": email, "type": "imap", "host": "127.0.0.1", "port": port, "starttls": false,
           "credential": {"keychain": format!("account:{email}")}})
}

/// The app over a data directory whose accounts are on one IMAP server on loopback, each with its password kept.
fn world(emails: &[&str], profile: &str) -> World {
    let imap = Imap::start();
    let keychain = Arc::new(Kept::default());
    for email in emails {
        imap.add(email, PASSWORD);
        keychain.put(&format!("account:{email}"), &Secret::new(PASSWORD.to_owned())).unwrap();
    }
    let home = tempfile::tempdir().unwrap();
    let accounts: Vec<Value> = emails.iter().map(|e| entry(e, imap.port)).collect();
    std::fs::write(home.path().join("mailmind.config.json"), json!({"accounts": accounts}).to_string()).unwrap();
    let key = mm_store::DbKey::from_bytes([0x5e; 32]);
    let path = home.path().join("mailmind.db");
    let store = mm_store::Store::open(&path, &key).unwrap();
    store.create_list_tables().unwrap();
    store.set_version(mm_store::schema::VERSION).unwrap();
    mm_store::Database::new(path, key, Arc::new(Loopback)).read(|c| c.execute_batch(TABLES)).unwrap();
    let hex = "5e".repeat(32);
    let environment = Environment::from_pairs([("MAILMIND_DB_KEY", hex.as_str()), ("MAILMIND_PROFILE", profile)]);
    let settings = Settings { home: home.path().to_path_buf() };
    let seams = Seams {
        clock: Arc::new(Frozen),
        outside: Arc::new(Loopback),
        keychain,
        sanitizer: Arc::new(mm_server::real::Sanitizing),
    };
    let app = Arc::new(App::assemble(&environment, &settings, home.path().join("dist"), 1, &seams).unwrap());
    World { app, imap, home }
}

/// A worker that fails rests moments rather than seconds, so it has every chance to try again.
fn quick() -> Pace {
    Pace { rests: Rests { first: Duration::from_millis(10), longest: Duration::from_millis(20) }, ..Pace::default() }
}

fn object(value: Value) -> Map<String, Value> {
    match value {
        Value::Object(map) => map,
        _ => unreachable!(),
    }
}

impl World {
    fn start(&self, pace: Pace) {
        self.app.realtime.start(pace);
    }

    fn all_idling(&self, emails: &[&str]) {
        until(|| emails.iter().all(|e| self.imap.idling(e, 1)));
    }

    fn watching(&self) -> Vec<(String, bool)> {
        self.app.realtime.watching()
    }

    fn keys(&self) -> Vec<String> {
        self.watching().into_iter().map(|(k, _)| k).collect()
    }

    fn edit(&self, body: Value) {
        self.app.connections.edit(&object(body)).unwrap();
    }

    /// Change an account in the config file as an editor would, which says nothing. Asked once every account
    /// idles, so each has pinned its first certificate and no worker writes an older copy of the file over the edit.
    fn edit_by_hand(&self, email: &str, field: &str, value: Value) {
        let file = self.home.path().join("mailmind.config.json");
        let mut raw: Value = serde_json::from_str(&std::fs::read_to_string(&file).unwrap()).unwrap();
        let accounts = raw["accounts"].as_array_mut().unwrap();
        let entry = accounts.iter_mut().find(|a| a["email"] == email).unwrap();
        entry[field] = value;
        std::fs::write(&file, raw.to_string()).unwrap();
    }
}

fn watched(pairs: &[(&str, bool)]) -> Vec<(String, bool)> {
    pairs.iter().map(|(k, running)| ((*k).to_owned(), *running)).collect()
}

#[test]
fn start_watches_every_account() {
    let w = world(&[ADA, BEA, CY], "full");
    w.start(quick());
    w.all_idling(&[ADA, BEA, CY]);
    assert_eq!(w.watching(), watched(&[(ADA, true), (BEA, true), (CY, true)]));
    // Logged in as every session is, its INBOX selected, then IDLE.
    assert_eq!(w.imap.idle_connection(ADA), ["LOGIN", "SELECT", "IDLE"]);
}

#[test]
fn new_mail_heard_in_idle_syncs_that_account_alone_and_the_page_hears_of_it() {
    let w = world(&[ADA, BEA], "full");
    let runtime = tokio::runtime::Builder::new_current_thread().enable_time().build().unwrap();
    let _entered = runtime.enter();
    let mut tab = TabStream::new(w.app.events.listen(None).unwrap(), "b1");
    w.start(quick());
    w.all_idling(&[ADA, BEA]);
    w.imap.deliver(ADA);
    until(|| w.app.syncing.status().unwrap().get(ADA).is_some_and(|s| s.health != "syncing"));
    let status = w.app.syncing.status().unwrap();
    assert_eq!((status[ADA].health, status[ADA].msg.as_str()), ("ok", "+0"));
    assert!(!status.contains_key(BEA));
    // Its sync went on a connection of its own: the IDLE's left IDLE, and idles again.
    until(|| w.imap.idling(ADA, 2));
    assert_eq!(w.imap.idle_connection(ADA), ["LOGIN", "SELECT", "IDLE", "DONE", "IDLE"]);
    assert_eq!(w.imap.count(ADA, "EXAMINE"), 1);
    // The page hears of it as of any sync.
    let said = "event: sync-done\ndata: {\"account\": \"ada@example.test\", \"kept\": 0, \"junk\": 0}";
    let mut frames = String::new();
    until(|| {
        while let Poll::Ready(Some(Ok(frame))) = Pin::new(&mut tab).poll_next(&mut Context::from_waker(Waker::noop())) {
            frames.push_str(&frame);
        }
        frames.contains(said)
    });
    assert!(!frames.contains("bea@example.test"));
}

#[test]
fn a_removed_account_stops_being_watched_at_once() {
    let w = world(&[ADA, BEA, CY], "full");
    w.start(quick());
    w.all_idling(&[ADA, BEA, CY]);
    w.app.connections.delete(&object(json!({"email": BEA}))).unwrap();
    // Out of an IDLE it would hold for 24 minutes, and none is left to talk to her server.
    until(|| w.app.realtime.running() == 2);
    assert_eq!(w.keys(), [ADA, CY]);
}

#[test]
fn a_disabled_account_stops_being_watched() {
    let w = world(&[ADA, CY], "full");
    w.start(quick());
    w.all_idling(&[ADA, CY]);
    w.edit(json!({"email": CY, "disabled": true}));
    until(|| w.app.realtime.running() == 1);
    assert_eq!(w.keys(), [ADA]);
}

#[test]
fn a_changed_password_gets_a_fresh_worker_that_logs_in_with_it() {
    let w = world(&[ADA], "full");
    w.start(quick());
    w.all_idling(&[ADA]);
    let logins = w.imap.count(ADA, "LOGIN");
    w.imap.add(ADA, "a-new-app-password");
    w.edit(json!({"email": ADA, "credential": {"style": "keychain", "value": "a-new-app-password"}}));
    until(|| w.imap.idling(ADA, 2));
    assert_eq!(w.imap.count(ADA, "LOGIN"), logins + 1);
    until(|| w.app.realtime.running() == 1);
    assert_eq!(w.watching(), watched(&[(ADA, true)]));
}

#[test]
fn a_new_alias_leaves_the_worker_be() {
    let w = world(&[ADA], "full");
    w.start(quick());
    w.all_idling(&[ADA]);
    let logins = w.imap.count(ADA, "LOGIN");
    // Whose change the workers hear before this returns.
    w.edit(json!({"email": ADA, "alias": "Ada at home"}));
    assert_eq!(w.app.realtime.running(), 1);
    assert_eq!((w.imap.count(ADA, "LOGIN"), w.imap.count(ADA, "DONE")), (logins, 0));
}

#[test]
fn an_added_account_is_watched() {
    let w = world(&[ADA], "full");
    w.start(quick());
    w.imap.add(DEE, PASSWORD);
    w.app
        .connections
        .add(&object(json!({"email": DEE, "type": "imap", "host": "127.0.0.1", "port": w.imap.port,
                            "starttls": false, "credential": {"style": "keychain", "value": PASSWORD}})))
        .unwrap();
    until(|| w.imap.idling(DEE, 1));
    assert_eq!(w.keys(), [ADA, DEE]);
}

#[test]
fn past_the_cap_a_removed_account_makes_room_for_the_next() {
    let w = world(&[ADA, BEA, CY], "full");
    w.start(Pace { most: 2, ..quick() });
    assert_eq!(w.keys(), [ADA, BEA]);
    w.app.connections.delete(&object(json!({"email": ADA}))).unwrap();
    until(|| w.imap.idling(CY, 1));
    assert_eq!(w.keys(), [BEA, CY]);
}

#[test]
fn a_refused_login_stops_a_worker_sending_it() {
    let w = world(&[ADA, BEA, CY], "full");
    w.imap.held().refusing.insert(ADA.to_owned());
    w.start(quick());
    // The others are no worse off.
    w.all_idling(&[BEA, CY]);
    until(|| w.imap.count(ADA, "LOGIN") == 1);
    // Its worker tries again many times in this, each held back before anything is sent.
    std::thread::sleep(Duration::from_millis(300));
    assert_eq!(w.imap.count(ADA, "LOGIN"), 1);
    // It is still there for when the hold ends.
    assert!(w.keys().contains(&ADA.to_owned()));
}

#[test]
fn a_worker_for_a_refused_login_starts_afresh_on_a_new_password() {
    let w = world(&[ADA], "full");
    w.imap.add(ADA, "the-new-password");
    w.start(quick());
    until(|| w.imap.count(ADA, "LOGIN") == 1);
    w.edit(json!({"email": ADA, "credential": {"style": "keychain", "value": "the-new-password"}}));
    until(|| w.imap.idling(ADA, 1));
    assert_eq!(w.imap.count(ADA, "LOGIN"), 2);
}

#[test]
fn a_server_without_idle_ends_its_worker_and_is_logged_out_of() {
    let w = world(&[ADA], "full");
    w.imap.held().without_idle = true;
    w.start(quick());
    until(|| w.imap.count(ADA, "LOGOUT") == 1);
    until(|| w.app.realtime.running() == 0);
    // The periodic sync has it.
    assert_eq!(w.watching(), watched(&[(ADA, false)]));
    assert_eq!((w.imap.count(ADA, "IDLE"), w.imap.count(ADA, "SELECT")), (0, 0));
}

#[test]
fn a_worker_whose_account_was_changed_by_hand_hands_over_to_a_fresh_one() {
    let w = world(&[ADA, BEA], "full");
    w.start(Pace { idle: Duration::from_millis(100), ..quick() });
    w.all_idling(&[ADA, BEA]);
    let logins = w.imap.count(ADA, "LOGIN");
    // No event: as an edit of the file would be.
    w.edit_by_hand(ADA, "strip_spaces", json!(true));
    until(|| w.imap.count(ADA, "LOGIN") == logins + 1);
    until(|| w.app.realtime.running() == 2);
    assert_eq!(w.watching(), watched(&[(BEA, true), (ADA, true)]));
}

#[test]
fn before_start_and_after_stop_nothing_is_watched() {
    let w = world(&[ADA], "full");
    w.edit(json!({"email": ADA, "strip_spaces": true}));
    assert!(w.watching().is_empty());
    w.start(quick());
    w.all_idling(&[ADA]);
    w.app.realtime.stop();
    assert_eq!(w.app.realtime.reconcile(), 0);
    assert!(w.watching().is_empty());
}

#[test]
fn a_read_only_process_opens_its_inbox_read_only_to_idle() {
    let w = world(&[ADA], "read-only");
    w.start(quick());
    w.all_idling(&[ADA]);
    assert_eq!(w.imap.idle_connection(ADA), ["LOGIN", "EXAMINE", "IDLE"]);
}

#[test]
fn the_workers_start_idle_and_their_stop_ends_it() {
    let w = world(&[ADA], "full");
    let workers = Workers::start(&w.app, false, 0, 0);
    until(|| w.imap.idling(ADA, 1));
    let stopping = Instant::now();
    workers.stop();
    // Out of an IDLE that would last 24 minutes, at once.
    assert!(stopping.elapsed() < Duration::from_secs(10));
    assert_eq!(w.app.realtime.running(), 0);
}

#[test]
fn a_read_only_process_starts_no_idle() {
    let w = world(&[ADA], "read-only");
    let workers = Workers::start(&w.app, true, 0, 0);
    std::thread::sleep(Duration::from_millis(300));
    workers.stop();
    assert_eq!(w.imap.count(ADA, "IDLE"), 0);
    assert!(w.watching().is_empty());
}

//! A login its server refused, held back on the Rust server (`login_wiring`, `heard::AccountsHeard`), against an IMAP
//! server on loopback that counts the connections and logins it is sent: a session held back never connects, what is
//! no refusal holds nothing, and a test of the account, a login that works and the account's change each do as the
//! Python app's do (tests/integration/accounts/test_refused_login.py). The served suites hold the routes to the
//! Python app's answers (tests/api/test_http_login_hold.py).

#![allow(clippy::unwrap_used)]

use std::collections::VecDeque;
use std::io::{BufRead, BufReader, Write};
use std::net::TcpListener;
use std::path::Path;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use mm_accounts::Account;
use mm_accounts::login_hold::FIRST;
use mm_kernel::seams::{Clock, Keychain, KeychainProblem, Moment, Outside, Secret, Tripped};
use mm_server::environment::{Environment, Settings};
use mm_server::{App, Seams};
use rcgen::{CertificateParams, KeyPair};
use rustls::pki_types::{PrivateKeyDer, PrivatePkcs8KeyDer};
use rustls::{ServerConfig, ServerConnection, StreamOwned};
use serde_json::{Map, Value, json};

const ADA: &str = "ada@example.test";
const PASSWORD: &str = "app-password";
const REFUSED: &str = "NO [AUTHENTICATIONFAILED] invalid credentials";
const IN: &str = "OK [CAPABILITY IMAP4rev1] logged in";

/// An IMAP server on loopback, over TLS from the first byte: it answers each LOGIN with the next answer it was given,
/// or with `otherwise` once they are spent, and every other command but LOGOUT with NO.
#[derive(Debug)]
struct Imap {
    port: u16,
    connections: Arc<AtomicUsize>,
    logins: Arc<AtomicUsize>,
    answers: Arc<Mutex<VecDeque<&'static str>>>,
}

/// A loopback server's TLS: a certificate of its own for 127.0.0.1.
fn tls() -> Arc<ServerConfig> {
    let key = KeyPair::generate().unwrap();
    let cert = CertificateParams::new(vec!["127.0.0.1".to_owned()]).unwrap().self_signed(&key).unwrap();
    let config = ServerConfig::builder_with_provider(Arc::new(rustls::crypto::ring::default_provider()))
        .with_safe_default_protocol_versions()
        .unwrap()
        .with_no_client_auth()
        .with_single_cert(vec![cert.der().clone()], PrivateKeyDer::Pkcs8(PrivatePkcs8KeyDer::from(key.serialize_der())))
        .unwrap();
    Arc::new(config)
}

/// An SMTP server on loopback, over TLS from the first byte, that lets any login in: where a test of the account logs
/// in once its IMAP server has.
fn smtp() -> u16 {
    let config = tls();
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    std::thread::spawn(move || {
        for tcp in listener.incoming() {
            let Ok(tcp) = tcp else { return };
            let tls = StreamOwned::new(ServerConnection::new(config.clone()).unwrap(), tcp);
            std::thread::spawn(move || {
                let _ = let_in(BufReader::new(tls));
            });
        }
    });
    port
}

fn let_in(mut stream: BufReader<StreamOwned<ServerConnection, std::net::TcpStream>>) -> std::io::Result<()> {
    stream.get_mut().write_all(b"220 ready\r\n")?;
    stream.get_mut().flush()?;
    let mut line = String::new();
    while stream.read_line(&mut line)? > 0 {
        let command = line.split_whitespace().next().unwrap_or("").to_uppercase();
        let said: &[u8] = match command.as_str() {
            "EHLO" => b"250-fake\r\n250 AUTH PLAIN LOGIN\r\n",
            "AUTH" => b"235 ok\r\n",
            "QUIT" => b"221 bye\r\n",
            _ => b"502 not here\r\n",
        };
        stream.get_mut().write_all(said)?;
        stream.get_mut().flush()?;
        if command == "QUIT" {
            return Ok(());
        }
        line.clear();
    }
    Ok(())
}

impl Imap {
    fn start(otherwise: &'static str) -> Imap {
        let config = tls();
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let imap = Imap {
            port,
            connections: Arc::default(),
            logins: Arc::default(),
            answers: Arc::new(Mutex::new(VecDeque::new())),
        };
        let (connections, logins, answers) = (imap.connections.clone(), imap.logins.clone(), imap.answers.clone());
        std::thread::spawn(move || {
            for tcp in listener.incoming() {
                let Ok(tcp) = tcp else { return };
                connections.fetch_add(1, Ordering::SeqCst);
                let tls = StreamOwned::new(ServerConnection::new(config.clone()).unwrap(), tcp);
                let (logins, answers) = (logins.clone(), answers.clone());
                std::thread::spawn(move || {
                    let _ = serve(BufReader::new(tls), &logins, &answers, otherwise);
                });
            }
        });
        imap
    }

    /// Answer the next LOGINs with these, in turn.
    fn answer(&self, answers: &[&'static str]) {
        self.answers.lock().unwrap().extend(answers);
    }

    fn connections(&self) -> usize {
        self.connections.load(Ordering::SeqCst)
    }

    fn logins(&self) -> usize {
        self.logins.load(Ordering::SeqCst)
    }
}

fn serve(
    mut stream: BufReader<StreamOwned<ServerConnection, std::net::TcpStream>>,
    logins: &AtomicUsize,
    answers: &Mutex<VecDeque<&'static str>>,
    otherwise: &'static str,
) -> std::io::Result<()> {
    stream.get_mut().write_all(b"* OK [CAPABILITY IMAP4rev1] ready\r\n")?;
    stream.get_mut().flush()?;
    let mut line = String::new();
    while stream.read_line(&mut line)? > 0 {
        let mut words = line.split_whitespace();
        let (tag, command) = (words.next().unwrap_or("*").to_owned(), words.next().unwrap_or("").to_uppercase());
        let said = match command.as_str() {
            "LOGIN" => {
                logins.fetch_add(1, Ordering::SeqCst);
                let answer = answers.lock().unwrap().pop_front().unwrap_or(otherwise);
                format!("{tag} {answer}\r\n")
            }
            "LOGOUT" => format!("* BYE\r\n{tag} OK bye\r\n"),
            _ => format!("{tag} NO not here\r\n"),
        };
        stream.get_mut().write_all(said.as_bytes())?;
        stream.get_mut().flush()?;
        if command == "LOGOUT" {
            return Ok(());
        }
        line.clear();
    }
    Ok(())
}

/// A clock the test moves.
#[derive(Debug, Default)]
struct Moving(Mutex<i64>);

impl Moving {
    fn shift(&self, seconds: i64) {
        *self.0.lock().unwrap() += seconds * 1_000_000;
    }
}

impl Clock for Moving {
    fn now(&self) -> Moment {
        Moment::from_micros(1_793_620_800_000_000 + *self.0.lock().unwrap())
    }
}

#[derive(Debug)]
struct Open;

impl Outside for Open {
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

/// A Keychain that holds Ada's password and lets an item go.
#[derive(Debug)]
struct Kept;

impl Keychain for Kept {
    fn find(&self, item: &str) -> Result<Option<Secret>, KeychainProblem> {
        Ok((item == format!("account:{ADA}")).then(|| Secret::new(PASSWORD.to_owned())))
    }

    fn delete(&self, _item: &str) -> Result<bool, KeychainProblem> {
        Ok(true)
    }
}

struct World {
    app: App,
    imap: Imap,
    smtp: u16,
    clock: Arc<Moving>,
    _home: tempfile::TempDir,
}

/// The app over a data directory whose one account, Ada's, is on an IMAP server that answers LOGIN with `otherwise`,
/// and sends through an SMTP server that lets it in.
fn world(otherwise: &'static str) -> World {
    let (imap, smtp) = (Imap::start(otherwise), smtp());
    let home = tempfile::tempdir().unwrap();
    let config = json!({"accounts": [{"email": ADA, "type": "imap", "host": "127.0.0.1", "port": imap.port,
                                      "starttls": false, "smtp_host": "127.0.0.1", "smtp_port": smtp,
                                      "smtp_starttls": false, "credential": {"keychain": format!("account:{ADA}")}}]});
    std::fs::write(home.path().join("mailmind.config.json"), config.to_string()).unwrap();
    // At the version the server reads, as the Python app's migrations leave it.
    let key = mm_store::DbKey::from_bytes([0x5e; 32]);
    mm_store::Store::open(&home.path().join("mailmind.db"), &key)
        .unwrap()
        .set_version(mm_store::schema::VERSION)
        .unwrap();
    let clock = Arc::new(Moving::default());
    let environment = Environment::from_pairs([("MAILMIND_DB_KEY", "5e".repeat(32).as_str())]);
    let settings = Settings { home: home.path().to_path_buf() };
    let seams = Seams {
        clock: clock.clone(),
        outside: Arc::new(Open),
        keychain: Arc::new(Kept),
        sanitizer: Arc::new(mm_server::real::Sanitizing),
        mail_hosts: Arc::new(mm_server::mail_hosts::NoLookups),
        local_ai: Arc::new(mm_server::local_ai::NoLocalServers),
    };
    let app = App::assemble(&environment, &settings, home.path().join("dist"), 1, &seams).unwrap();
    World { app, imap, smtp, clock, _home: home }
}

impl World {
    /// A sync of Ada's INBOX: the class of what stopped it, if anything did. Past the login, this server says NO.
    fn sync(&self) -> &'static str {
        self.app.ingest.sync_folder(ADA, "INBOX", false, None).map(|_| "").unwrap_or_else(|e| e.class())
    }

    fn ada(&self) -> Account {
        self.app.directory.get(ADA).unwrap().unwrap()
    }

    fn held(&self) -> bool {
        self.app.logins.held(&self.ada())
    }

    /// A person's test of a saved account, or of a draft.
    fn test(&self, body: Value) -> Value {
        self.app.connections.test(&object(body)).unwrap()
    }
}

fn object(value: Value) -> Map<String, Value> {
    match value {
        Value::Object(map) => map,
        _ => unreachable!(),
    }
}

#[test]
fn a_refused_login_is_held_back_without_connecting_until_its_hold_ends() {
    let w = world(REFUSED);
    assert_eq!(w.sync(), "LoginRefused");
    assert!(w.held());
    // Held back: it fails as the refusal did, and nothing reaches the server.
    assert_eq!(w.sync(), "LoginRefused");
    assert_eq!((w.imap.connections(), w.imap.logins()), (1, 1));
    w.clock.shift(FIRST - 1);
    assert_eq!(w.sync(), "LoginRefused");
    assert_eq!(w.imap.connections(), 1);
    w.clock.shift(2);
    assert_eq!(w.sync(), "LoginRefused");
    assert_eq!((w.imap.connections(), w.imap.logins()), (2, 2));
}

#[test]
fn a_server_down_for_now_holds_nothing() {
    let w = world("NO [UNAVAILABLE] busy");
    assert_eq!(w.sync(), "LoginUnavailable");
    assert!(!w.held());
    assert_eq!(w.sync(), "LoginUnavailable");
    assert_eq!(w.imap.logins(), 2);
}

#[test]
fn a_login_that_works_ends_the_hold() {
    let w = world(IN);
    w.imap.answer(&[REFUSED]);
    assert_eq!(w.sync(), "LoginRefused");
    assert!(w.held());
    // A person's test goes whatever the hold says, and its login, which works, ends it.
    let tested = w.test(json!({"email": ADA}));
    assert_eq!((&tested["connect"], w.imap.logins()), (&json!(true), 2));
    assert!(!w.held());
    assert_ne!(w.sync(), "LoginRefused");
    assert_eq!(w.imap.logins(), 3);
}

#[test]
fn a_saved_accounts_test_its_server_refuses_holds_the_login_back() {
    let w = world(REFUSED);
    let tested = w.test(json!({"email": ADA}));
    let says = "The server didn't accept that address and password. Check them; if your provider offers app passwords, \
                make one and paste it here.";
    assert_eq!(
        tested,
        json!({"ok": true, "connect": false, "email": ADA, "error": "AUTHENTICATIONFAILED", "says": says, "link": null})
    );
    assert!(w.held());
    assert_eq!(w.sync(), "LoginRefused");
    assert_eq!(w.imap.logins(), 1);
}

#[test]
fn a_draft_asks_nothing_of_a_hold_and_tells_it_nothing() {
    let w = world(REFUSED);
    let draft = |password: &str| {
        json!({"draft": {"email": ADA, "type": "imap", "host": "127.0.0.1", "port": w.imap.port, "starttls": false,
                         "smtp_host": "127.0.0.1", "smtp_port": w.smtp, "smtp_starttls": false,
                         "credential": {"style": "keychain", "value": password}}})
    };
    assert_eq!(w.test(draft("wrong"))["connect"], json!(false));
    assert!(!w.held());
    assert_eq!(w.sync(), "LoginRefused");
    w.imap.answer(&[IN]);
    assert_eq!(w.test(draft(PASSWORD))["connect"], json!(true));
    assert!(w.held());
    assert_eq!(w.imap.logins(), 3);
}

#[test]
fn an_edit_or_a_removal_of_the_account_ends_its_hold() {
    let w = world(REFUSED);
    assert_eq!(w.sync(), "LoginRefused");
    w.app.connections.edit(&object(json!({"email": ADA, "alias": "Ada"}))).unwrap();
    assert!(!w.held());
    assert_eq!(w.sync(), "LoginRefused");
    assert_eq!(w.imap.logins(), 2);
    let ada = w.ada();
    assert!(w.app.logins.held(&ada));
    w.app.connections.delete(&object(json!({"email": ADA}))).unwrap();
    assert!(!w.app.logins.held(&ada));
}

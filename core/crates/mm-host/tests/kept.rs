//! Each account's kept IMAP connection in the Mac app's host (#5): a wake closes it, so the next job logs in afresh
//! rather than finding a connection the sleep left dead; a close logs it out; and the debug route says how each
//! stands. Against an IMAP server on loopback that writes down each connection's commands, over a fictional home.

#![allow(clippy::unwrap_used)]

use std::collections::{BTreeMap, BTreeSet};
use std::io::{BufRead, BufReader, Write};
use std::net::{TcpListener, TcpStream};
use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use mm_host::stand_in::FileKeychain;
use mm_host::{Home, Host};
use mm_kernel::seams::{Keychain, Secret};
use mm_mailbox::gates::{Priority, Wait, on_gate};
use mm_mailbox::session::MailServers;
use rcgen::{CertificateParams, KeyPair};
use rustls::pki_types::{PrivateKeyDer, PrivatePkcs8KeyDer};
use rustls::{ServerConfig, ServerConnection, StreamOwned};
use serde_json::{Value, json};

const KEY: &str = "0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0";
const ADA: &str = "ada@example.test";
const PASSWORD: &str = "app-password";

#[derive(Debug, Default)]
struct Heard {
    /// Each connection's commands, without tags.
    commands: BTreeMap<usize, Vec<String>>,
    ended: BTreeSet<usize>,
}

/// An IMAP server on loopback, TLS from the first byte, that takes Ada's login and says OK to the rest.
#[derive(Clone)]
struct Imap {
    port: u16,
    heard: Arc<Mutex<Heard>>,
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
        let imap = Imap { port: listener.local_addr().unwrap().port(), heard: Arc::default() };
        let heard = imap.heard.clone();
        std::thread::spawn(move || {
            for (id, tcp) in listener.incoming().enumerate() {
                let Ok(tcp) = tcp else { return };
                let tls = StreamOwned::new(ServerConnection::new(config.clone()).unwrap(), tcp);
                let heard = heard.clone();
                std::thread::spawn(move || {
                    let _ = serve(BufReader::new(tls), id + 1, &heard);
                    heard.lock().unwrap().ended.insert(id + 1);
                });
            }
        });
        imap
    }

    /// The connection that was asked `command`, and everything it was asked.
    fn one_asked(&self, command: &str) -> (usize, Vec<String>) {
        let heard = self.heard.lock().unwrap();
        let (id, commands) = heard.commands.iter().find(|(_, c)| c.iter().any(|c| c == command)).unwrap();
        (*id, commands.clone())
    }

    fn ended(&self, id: usize) -> bool {
        self.heard.lock().unwrap().ended.contains(&id)
    }
}

fn serve(
    mut io: BufReader<StreamOwned<ServerConnection, TcpStream>>,
    id: usize,
    heard: &Mutex<Heard>,
) -> std::io::Result<()> {
    let say = |io: &mut BufReader<StreamOwned<ServerConnection, TcpStream>>, said: String| {
        let stream = io.get_mut();
        stream.write_all(said.as_bytes())?;
        stream.flush()
    };
    say(&mut io, "* OK [CAPABILITY IMAP4rev1] ready\r\n".to_owned())?;
    let mut line = String::new();
    loop {
        line.clear();
        if io.read_line(&mut line)? == 0 {
            return Ok(());
        }
        let line = line.trim_end_matches(['\r', '\n']).to_owned();
        let (tag, command) = line.split_once(' ').unwrap_or((&line, ""));
        let verb = command.split(' ').next().unwrap_or("").to_uppercase();
        let written = if verb == "LOGIN" { "LOGIN".to_owned() } else { command.to_owned() };
        heard.lock().unwrap().commands.entry(id).or_default().push(written);
        match verb.as_str() {
            "LOGIN" if command.contains(&format!("\"{ADA}\" \"{PASSWORD}\"")) => {
                say(&mut io, format!("{tag} OK logged in\r\n"))?;
            }
            "LOGIN" => say(&mut io, format!("{tag} NO [AUTHENTICATIONFAILED] no\r\n"))?,
            "SELECT" | "EXAMINE" => say(&mut io, format!("* 0 EXISTS\r\n* OK [UIDVALIDITY 7] ok\r\n{tag} OK\r\n"))?,
            "UID" if command.to_uppercase().starts_with("UID SEARCH") => {
                say(&mut io, format!("* SEARCH\r\n{tag} OK\r\n"))?;
            }
            "LOGOUT" => {
                say(&mut io, format!("* BYE\r\n{tag} OK\r\n"))?;
                return Ok(());
            }
            _ => say(&mut io, format!("{tag} OK\r\n"))?,
        }
    }
}

fn until(what: impl Fn() -> bool) {
    let deadline = Instant::now() + Duration::from_secs(10);
    while !what() {
        assert!(Instant::now() < deadline, "it never came");
        std::thread::sleep(Duration::from_millis(10));
    }
}

/// A fictional home whose one account, Ada's, is on `imap`.
fn host(folder: &Path, imap: &Imap) -> Host {
    let keychain = folder.join("keychain.json");
    FileKeychain::new(keychain.clone()).put(&format!("account:{ADA}"), &Secret::new(PASSWORD.to_owned())).unwrap();
    let accounts = json!({"accounts": [{"email": ADA, "type": "imap", "host": "127.0.0.1", "port": imap.port,
        "starttls": false, "credential": {"keychain": format!("account:{ADA}")}}]});
    std::fs::write(folder.join("mailmind.config.json"), accounts.to_string()).unwrap();
    let home = Home {
        folder: folder.to_path_buf(),
        variables: vec![("MAILMIND_DB_KEY".into(), KEY.into())],
        stand_in: Some(keychain),
    };
    Host::open(home).unwrap()
}

/// A job on Ada's gate that looks in the folder `marker`, as opening a message there does: what tells its connection
/// apart from the sync's.
fn job(host: &Host, marker: &str) {
    let app = host.app();
    on_gate(&*app.gates, ADA, Priority::Interactive, Wait::Ever, || {
        let mut s = app.kept.open(ADA, 60).unwrap();
        s.select(marker, true).unwrap();
        s.logout();
    })
    .unwrap();
}

#[test]
fn a_wake_closes_the_kept_connection_and_the_next_job_logs_in_afresh() {
    let imap = Imap::start();
    let folder = tempfile::tempdir().unwrap();
    let host = host(folder.path(), &imap);
    job(&host, "First");
    let (kept, _) = imap.one_asked("EXAMINE \"First\"");
    host.woke();
    until(|| imap.ended(kept));
    let (_, asked) = imap.one_asked("EXAMINE \"First\"");
    assert!(!asked.contains(&"LOGOUT".to_owned()), "closed without a word: {asked:?}");
    job(&host, "Second");
    let (after, asked) = imap.one_asked("EXAMINE \"Second\"");
    assert_ne!(after, kept);
    assert!(!asked.contains(&"NOOP".to_owned()), "{asked:?}");
    host.close();
}

#[test]
fn a_close_logs_the_kept_connection_out() {
    let imap = Imap::start();
    let folder = tempfile::tempdir().unwrap();
    let host = host(folder.path(), &imap);
    job(&host, "First");
    job(&host, "Again");
    let (kept, asked) = imap.one_asked("EXAMINE \"Again\"");
    assert!(asked.contains(&"NOOP".to_owned()), "the second job had the first one's connection: {asked:?}");
    host.close();
    let (_, asked) = imap.one_asked("EXAMINE \"Again\"");
    assert_eq!(asked.last().unwrap(), "LOGOUT");
    until(|| imap.ended(kept));
}

#[test]
fn the_debug_route_says_how_the_kept_connections_stand() {
    let imap = Imap::start();
    let folder = tempfile::tempdir().unwrap();
    let host = host(folder.path(), &imap);
    job(&host, "First");
    job(&host, "Again");
    let answer = host.request("GET", "/api/debug/pool", b"");
    assert_eq!(answer.status, 200);
    let pool: Value = serde_json::from_slice(&answer.body).unwrap();
    assert_eq!((&pool["enabled"], &pool["target"]), (&json!(true), &json!("all")));
    assert_eq!(pool["counters"], json!({"created": 1, "reused": 1, "validate_fail": 0, "discard_dirty": 0}));
    let [slot] = pool["slots"].as_array().unwrap().as_slice() else { panic!("{pool}") };
    assert_eq!((&slot["open"], &slot["uses"]), (&json!(true), &json!(2)));
    assert!(slot["age"].as_f64().unwrap() >= 0.0);
    assert!(!String::from_utf8_lossy(&answer.body).contains("example.test"), "no account named");
    host.close();
}

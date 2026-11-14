//! 1Password on the Rust server (`one_password`), through a program that speaks as `op read` does, in a folder of a
//! PATH the test gives: the reference alone on its command line and the secret back on its standard output, a timeout
//! or an unanswered prompt tried again after a pause, three times at most, and nothing op says in a fault; op found
//! only in an absolute folder PATH names, and never started when the tripwire refuses it or the reference isn't one.
//! Then the app over it: an account whose password is a 1Password reference logging in with it, read once a process
//! until its credential changes; and the Claude API key as one. No test starts the real op: the PATH it is looked for
//! in is the test's own.

#![cfg(unix)]
#![allow(clippy::unwrap_used)]

use std::io::{BufRead, BufReader, Write};
use std::net::TcpListener;
use std::os::unix::fs::PermissionsExt;
use std::path::Path;
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};

use mm_accounts::ports::OnePassword;
use mm_kernel::errors::Fault;
use mm_kernel::seams::{Clock, Keychain, KeychainProblem, Moment, Outside, Secret, Tripped};
use mm_server::environment::{Environment, Settings};
use mm_server::one_password::OpCli;
use mm_server::{App, Seams};
use rcgen::{CertificateParams, KeyPair};
use rustls::pki_types::{PrivateKeyDer, PrivatePkcs8KeyDer};
use rustls::{ServerConfig, ServerConnection, StreamOwned};
use serde_json::{Map, Value, json};

/// `op read <reference>`, answering by the reference; each command line it was given, in argv beside it, and how many
/// times each reference was asked, in a file of its own. It names each program it starts in full: the PATH it is
/// given holds only the test's folder.
const OP: &str = r#"#!/bin/sh
here=$(/usr/bin/dirname "$0")
printf '%s\n' "$*" >> "$here/argv"
[ "$1" = read ] && [ $# -eq 2 ] || exit 2
tries="$here/tries-$(printf '%s' "$2" | /usr/bin/tr -c 'A-Za-z0-9' _)"
n=$(( $(/bin/cat "$tries" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$tries"
case "$2" in
op://Tests/Read/password) printf '  a pass "word" with '"'"'quotes'"'"' $HOME \n' ;;
op://Tests/Path/password) echo found ;;
op://Tests/Ada/password) echo ada-op-password ;;
op://Tests/Ada/new-password) echo ada-new-password ;;
op://Tests/Warm/password) echo warm-password ;;
op://Tests/Claude/credential) echo sk-ant-op-key ;;
op://Tests/Late/password)
    if [ "$n" -lt 3 ]; then echo "[ERROR] 2026/11/14 connecting to desktop app: timeout" >&2; exit 1; fi
    echo late-password ;;
op://Tests/Prompt/password) echo "[ERROR] 2026/11/14 authorization prompt dismissed, please try again" >&2; exit 1 ;;
op://Tests/Hang/password) exec /bin/sleep 30 ;;
op://Tests/Binary/password) printf '\377\376\n' ;;
op://Tests/Stdin/password) if read -r line; then echo "read $line"; else echo "nothing on stdin"; fi ;;
op://Tests/Environment/password)
    echo "db key ${MAILMIND_DB_KEY:-withheld}, api key ${ANTHROPIC_API_KEY:-withheld}, account ${OP_ACCOUNT:-none}" ;;
*) echo "[ERROR] 2026/11/14 could not read secret '$2': the item secret-shaped-words isn't in the vault" >&2; exit 1 ;;
esac
"#;

/// The folder the programs are in: `bin/op`, and `plain/op`, which nobody may run.
fn programs() -> &'static Path {
    static FOLDER: OnceLock<tempfile::TempDir> = OnceLock::new();
    FOLDER
        .get_or_init(|| {
            let folder = tempfile::tempdir().unwrap();
            for (name, mode) in [("bin", 0o755), ("plain", 0o644)] {
                std::fs::create_dir(folder.path().join(name)).unwrap();
                let path = folder.path().join(name).join("op");
                let text = if mode == 0o755 { OP.to_owned() } else { OP.replace("echo found", "echo plain") };
                std::fs::write(&path, text).unwrap();
                std::fs::set_permissions(&path, std::fs::Permissions::from_mode(mode)).unwrap();
            }
            folder
        })
        .path()
}

fn bin() -> String {
    programs().join("bin").to_string_lossy().into_owned()
}

/// The command lines op was started with, one each.
fn said() -> Vec<String> {
    std::fs::read_to_string(programs().join("bin/argv")).unwrap_or_default().lines().map(str::to_owned).collect()
}

/// How many times op was started for `reference`.
fn started(reference: &str) -> usize {
    said().iter().filter(|line| line.split_once(' ').map(|(_, r)| r) == Some(reference)).count()
}

/// Lets op through, and a connection only to this machine; remembers each program asked for.
#[derive(Debug, Default)]
struct Open {
    asked: Mutex<Vec<String>>,
    no_op: bool,
}

impl Outside for Open {
    fn open(&self, _path: &Path) -> Result<(), Tripped> {
        Ok(())
    }

    fn reach(&self, host: &str) -> Result<(), Tripped> {
        if host == "127.0.0.1" { Ok(()) } else { Err(Tripped(host.to_owned())) }
    }

    fn run(&self, program: &str) -> Result<(), Tripped> {
        self.asked.lock().unwrap().push(program.to_owned());
        if program == "op" && !self.no_op { Ok(()) } else { Err(Tripped(program.to_owned())) }
    }
}

/// An environment whose PATH is `path`, or that has none.
fn with_path(path: Option<&str>) -> Environment {
    Environment::from_pairs(path.map(|path| ("PATH", path)))
}

fn op_through(path: Option<&str>, outside: Arc<dyn Outside>) -> OpCli {
    OpCli::through(&with_path(path), outside, Duration::from_secs(20), Duration::from_millis(50))
}

fn op() -> OpCli {
    op_through(Some(&bin()), Arc::new(Open::default()))
}

const UNREAD: Fault = Fault::new("OpReadFailed");

#[test]
fn a_read_is_what_op_printed_and_only_the_reference_goes_on_its_command_line() {
    let read = op().read("op://Tests/Read/password").unwrap();
    assert_eq!(read.expose(), "  a pass \"word\" with 'quotes' $HOME \n");
    assert!(said().contains(&"read op://Tests/Read/password".to_owned()));
    assert_eq!(started("op://Tests/Read/password"), 1);
}

#[test]
fn a_refused_read_is_tried_once_and_says_nothing_op_said() {
    let read = op().read("op://Tests/Gone/password");
    assert_eq!(read.as_ref().unwrap_err(), &UNREAD);
    assert!(!format!("{read:?}").contains("secret-shaped-words"));
    assert_eq!(started("op://Tests/Gone/password"), 1);
}

#[test]
fn a_timeout_is_tried_again_after_a_pause() {
    let began = Instant::now();
    assert_eq!(op().read("op://Tests/Late/password").unwrap().expose(), "late-password\n");
    assert_eq!(started("op://Tests/Late/password"), 3);
    assert!(began.elapsed() >= Duration::from_millis(100), "two pauses");
}

#[test]
fn an_unanswered_prompt_is_tried_three_times_at_most() {
    assert_eq!(op().read("op://Tests/Prompt/password").unwrap_err(), UNREAD);
    assert_eq!(started("op://Tests/Prompt/password"), 3);
}

#[test]
fn op_past_its_time_is_stopped() {
    let op =
        OpCli::through(&with_path(Some(&bin())), Arc::new(Open::default()), Duration::from_secs(1), Duration::ZERO);
    let began = Instant::now();
    assert_eq!(op.read("op://Tests/Hang/password").unwrap_err(), UNREAD);
    assert!(began.elapsed() < Duration::from_secs(10));
    // Not tried again: its own time was spent. A busy machine may stop it before it says a word.
    assert!(started("op://Tests/Hang/password") <= 1);
}

#[test]
fn what_isnt_text_is_no_secret() {
    assert_eq!(op().read("op://Tests/Binary/password").unwrap_err(), UNREAD);
}

#[test]
fn op_asks_nobody_on_its_standard_input() {
    assert_eq!(op().read("op://Tests/Stdin/password").unwrap().expose(), "nothing on stdin\n");
}

#[test]
fn op_starts_in_the_servers_environment_but_for_its_own_secrets() {
    let (path, key) = (bin(), "5e".repeat(32));
    let environment = Environment::from_pairs([
        ("PATH", path.as_str()),
        ("MAILMIND_DB_KEY", key.as_str()),
        ("ANTHROPIC_API_KEY", "sk-ant-of-the-server"),
        ("OP_ACCOUNT", "my.1password.example"),
    ]);
    let op = OpCli::through(&environment, Arc::new(Open::default()), Duration::from_secs(20), Duration::ZERO);
    let read = op.read("op://Tests/Environment/password").unwrap();
    assert_eq!(read.expose(), "db key withheld, api key withheld, account my.1password.example\n");
    let shown = format!("{op:?}");
    assert!(!shown.contains("sk-ant") && !shown.contains(&key), "{shown}");
}

#[test]
fn op_is_the_first_runnable_one_in_an_absolute_folder_path_names() {
    let plain = programs().join("plain").to_string_lossy().into_owned();
    let path = format!("/nonexistent:bin:.:{plain}:{}", bin());
    assert_eq!(
        op_through(Some(&path), Arc::new(Open::default())).read("op://Tests/Path/password").unwrap().expose(),
        "found\n"
    );
    for path in [None, Some(""), Some("bin"), Some(plain.as_str())] {
        let read = op_through(path, Arc::new(Open::default())).read("op://Tests/Nowhere/password");
        assert_eq!(read.unwrap_err(), Fault::new("OpNotFound"), "{path:?}");
    }
    assert_eq!(started("op://Tests/Nowhere/password"), 0);
}

#[test]
fn a_program_the_tripwire_refuses_never_starts() {
    let outside = Arc::new(Open { no_op: true, ..Open::default() });
    assert_eq!(op_through(Some(&bin()), outside.clone()).read("op://Tests/Tripped/password").unwrap_err(), UNREAD);
    assert_eq!(*outside.asked.lock().unwrap(), ["op"]);
    assert_eq!(started("op://Tests/Tripped/password"), 0);
}

#[test]
fn a_reference_that_isnt_one_never_starts_op() {
    let outside = Arc::new(Open::default());
    let op = op_through(Some(&bin()), outside.clone());
    for reference in ["-h", "--out-file=/tmp/x", "op://Tests/Two\nLines", "", "vault/item/field"] {
        assert_eq!(op.read(reference).unwrap_err(), UNREAD, "{reference:?}");
    }
    assert!(outside.asked.lock().unwrap().is_empty());
}

// -- the app over it -----------------------------------------------------------------------------------------------

const ADA: &str = "ada@example.test";

/// An IMAP server on loopback, over TLS from the first byte, that lets every LOGIN in and keeps each LOGIN's line;
/// every other command but LOGOUT it answers NO.
struct Imap {
    port: u16,
    logins: Arc<Mutex<Vec<String>>>,
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
        let imap = Imap { port: listener.local_addr().unwrap().port(), logins: Arc::default() };
        let logins = imap.logins.clone();
        std::thread::spawn(move || {
            for tcp in listener.incoming() {
                let Ok(tcp) = tcp else { return };
                let tls = StreamOwned::new(ServerConnection::new(config.clone()).unwrap(), tcp);
                let logins = logins.clone();
                std::thread::spawn(move || {
                    let _ = serve(BufReader::new(tls), &logins);
                });
            }
        });
        imap
    }

    fn logins(&self) -> Vec<String> {
        self.logins.lock().unwrap().clone()
    }
}

fn serve(
    mut stream: BufReader<StreamOwned<ServerConnection, std::net::TcpStream>>,
    logins: &Mutex<Vec<String>>,
) -> std::io::Result<()> {
    stream.get_mut().write_all(b"* OK [CAPABILITY IMAP4rev1] ready\r\n")?;
    stream.get_mut().flush()?;
    let mut line = String::new();
    while stream.read_line(&mut line)? > 0 {
        let mut words = line.split_whitespace();
        let (tag, command) = (words.next().unwrap_or("*").to_owned(), words.next().unwrap_or("").to_uppercase());
        let said = match command.as_str() {
            "LOGIN" => {
                logins.lock().unwrap().push(line.trim_end().to_owned());
                format!("{tag} OK [CAPABILITY IMAP4rev1] logged in\r\n")
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

#[derive(Debug)]
struct At;

impl Clock for At {
    fn now(&self) -> Moment {
        Moment::from_micros(1_793_620_800_000_000)
    }
}

/// A Keychain that holds nothing.
#[derive(Debug)]
struct Empty;

impl Keychain for Empty {
    fn find(&self, _item: &str) -> Result<Option<Secret>, KeychainProblem> {
        Ok(None)
    }
}

struct World {
    app: Arc<App>,
    _home: tempfile::TempDir,
}

/// The app over a data directory whose config is `config`, with op on the PATH and `more` in its environment.
fn world(config: Value, more: &[(&str, &str)]) -> World {
    let home = tempfile::tempdir().unwrap();
    std::fs::write(home.path().join("mailmind.config.json"), config.to_string()).unwrap();
    let key = mm_store::DbKey::from_bytes([0x5e; 32]);
    mm_store::Store::open(&home.path().join("mailmind.db"), &key)
        .unwrap()
        .set_version(mm_store::schema::VERSION)
        .unwrap();
    let (db_key, path) = ("5e".repeat(32), bin());
    let pairs = [("MAILMIND_DB_KEY", db_key.as_str()), ("PATH", path.as_str())];
    let environment = Environment::from_pairs(pairs.iter().copied().chain(more.iter().copied()));
    let settings = Settings { home: home.path().to_path_buf() };
    let seams = Seams {
        clock: Arc::new(At),
        outside: Arc::new(Open::default()),
        keychain: Arc::new(Empty),
        sanitizer: Arc::new(mm_server::real::Sanitizing),
    };
    let app = App::assemble(&environment, &settings, home.path().join("dist"), 1, &seams).unwrap();
    World { app: Arc::new(app), _home: home }
}

fn ada_at(port: u16, credential: Value) -> Value {
    json!({"email": ADA, "type": "imap", "host": "127.0.0.1", "port": port, "starttls": false,
           "credential": credential})
}

fn object(value: Value) -> Map<String, Value> {
    match value {
        Value::Object(map) => map,
        _ => unreachable!(),
    }
}

#[test]
fn an_account_whose_password_is_in_1password_logs_in_with_it_read_once_until_its_credential_changes() {
    let imap = Imap::start();
    let w = world(json!({"accounts": [ada_at(imap.port, json!({"op": "op://Tests/Ada/password"}))]}), &[]);
    let test = || w.app.connections.test(&object(json!({"email": ADA}))).unwrap();
    assert_eq!(test()["connect"], json!(true));
    assert_eq!(test()["connect"], json!(true));
    assert_eq!(started("op://Tests/Ada/password"), 1);
    let logins = imap.logins();
    assert_eq!(logins.len(), 2);
    assert!(logins.iter().all(|line| line.ends_with(r#"LOGIN "ada@example.test" "ada-op-password""#)), "{logins:?}");
    // A new credential is read afresh.
    let edited = json!({"email": ADA, "credential": {"style": "op", "ref": "op://Tests/Ada/new-password"}});
    w.app.connections.edit(&object(edited)).unwrap();
    assert_eq!(test()["connect"], json!(true));
    assert!(imap.logins()[2].ends_with(r#""ada-new-password""#));
    assert_eq!(started("op://Tests/Ada/new-password"), 1);
}

#[test]
fn a_1password_read_that_fails_is_a_refused_login_to_the_person() {
    let imap = Imap::start();
    let w = world(json!({"accounts": [ada_at(imap.port, json!({"op": "op://Tests/Missing/password"}))]}), &[]);
    let tested = w.app.connections.test(&object(json!({"email": ADA}))).unwrap();
    assert_eq!(tested, json!({"ok": true, "connect": false, "email": ADA, "error": "AUTHENTICATIONFAILED"}));
    assert!(imap.logins().is_empty());
}

#[test]
fn the_claude_api_key_can_be_a_1password_reference_and_wins_over_the_environments() {
    let key = json!({"accounts": [], "anthropic_api_key": {"op": "op://Tests/Claude/credential"}});
    let w = world(key, &[("ANTHROPIC_API_KEY", "sk-ant-from-the-environment")]);
    for _ in 0..2 {
        let report = w.app.assistant.status.report().unwrap();
        assert_eq!((&report["engine"], &report["key_from"]), (&json!("api"), &json!("mailmind.config.json")));
    }
    assert_eq!(started("op://Tests/Claude/credential"), 1);
    // One that can't be read is no key, never a quiet switch to the environment's.
    let unread = json!({"accounts": [], "anthropic_api_key": {"op": "op://Tests/Unread/credential"}});
    let w = world(unread, &[("ANTHROPIC_API_KEY", "sk-ant-from-the-environment")]);
    assert_eq!(w.app.assistant.status.report().unwrap()["engine"], json!("none"));
}

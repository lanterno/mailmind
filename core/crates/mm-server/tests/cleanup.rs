//! A sender's cleanup and a sweep over a socket, as far as the served suites (tests/api) don't reach:
//! what each refuses before it looks anything up, and a sweep in a read-only process.

#![allow(clippy::unwrap_used)]

use std::io::{Read, Write};
use std::net::TcpStream;
use std::path::Path;
use std::sync::Arc;
use std::time::Duration;

use mm_kernel::seams::{Clock, Keychain, KeychainProblem, Moment, Outside, Secret, Tripped};
use mm_server::environment::{Environment, Settings};
use mm_server::{App, Seams, Server};
use serde_json::{Value, json};

#[derive(Debug)]
struct Frozen;

impl Clock for Frozen {
    fn now(&self) -> Moment {
        Moment::from_micros(1_793_620_800_000_000)
    }
}

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

#[derive(Debug)]
struct Empty;

impl Keychain for Empty {
    fn find(&self, _item: &str) -> Result<Option<Secret>, KeychainProblem> {
        Ok(None)
    }
}

struct Running {
    port: u16,
    csrf: String,
    _home: tempfile::TempDir,
    _runtime: tokio::runtime::Runtime,
}

/// The server over an empty data directory, run with `profile`.
fn running(profile: &str) -> Running {
    let home = tempfile::tempdir().unwrap();
    std::fs::write(home.path().join("mailmind.config.json"), r#"{"accounts": []}"#).unwrap();
    let runtime = tokio::runtime::Builder::new_multi_thread().enable_all().build().unwrap();
    let server = runtime.block_on(Server::bind(0)).unwrap();
    let port = server.port().unwrap();
    let key = "5e".repeat(32);
    let environment = Environment::from_pairs([("MAILMIND_DB_KEY", key.as_str()), ("MAILMIND_PROFILE", profile)]);
    let settings = Settings { home: home.path().to_path_buf() };
    let seams = Seams { clock: Arc::new(Frozen), outside: Arc::new(Open), keychain: Arc::new(Empty) };
    let dist = home.path().join("dist");
    let app = Arc::new(App::assemble(&environment, &settings, dist, port, &seams).unwrap());
    let csrf = app.csrf.clone();
    let (stop, stopped) = tokio::sync::watch::channel(false);
    runtime.spawn(server.run(app, stopped, Duration::from_secs(1)));
    std::mem::forget(stop);
    Running { port, csrf, _home: home, _runtime: runtime }
}

impl Running {
    /// A POST that gets past every check: its status and its body.
    fn post(&self, path: &str, body: &Value) -> (u16, Value) {
        let body = body.to_string();
        let own = format!("127.0.0.1:{}", self.port);
        let mut stream = TcpStream::connect(("127.0.0.1", self.port)).unwrap();
        stream.set_read_timeout(Some(Duration::from_secs(10))).unwrap();
        let sent = format!(
            "POST {path} HTTP/1.1\r\nHost: {own}\r\nOrigin: http://{own}\r\nSec-Fetch-Site: same-origin\r\n\
             X-CSRF-Token: {}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
            self.csrf,
            body.len()
        );
        stream.write_all(sent.as_bytes()).unwrap();
        let mut got = Vec::new();
        let _ = stream.read_to_end(&mut got);
        let end = got.windows(4).position(|w| w == b"\r\n\r\n").unwrap();
        let head = String::from_utf8(got[..end].to_vec()).unwrap();
        let status = head.split(' ').nth(1).unwrap().parse().unwrap();
        (status, serde_json::from_slice(&got[end + 4..]).unwrap())
    }
}

fn bad_request() -> Value {
    json!({"type": "/problems/refused", "title": "Refused", "status": 400, "detail": "bad request"})
}

#[test]
fn a_sweep_that_would_move_mail_in_a_read_only_process_is_the_read_only_409() {
    let server = running("read-only");
    assert_eq!(
        server.post("/api/sweep", &json!({"account": "all"})),
        (
            409,
            json!({"type": "/problems/read-only", "title": "Read-only", "status": 409,
                   "detail": "MailMind is running read-only", "read_only": true})
        )
    );
}

#[test]
fn a_field_its_schema_refuses_is_the_bad_request_400_before_anything_is_looked_up() {
    let server = running("full");
    for (path, body) in [
        ("/api/cleanup/execute", json!({"from_addr": "news@loom.example.test", "block_scope": "everything"})),
        ("/api/cleanup/execute", json!({"from_addr": "news@loom.example.test", "do_unsubscribe": 1})),
        ("/api/cleanup/execute", json!({"from_addr": 7})),
        ("/api/cleanup/batch/preview", json!({"senders": [1]})),
        ("/api/cleanup/batch/preview", json!({"senders": "news@loom.example.test"})),
        ("/api/cleanup/batch", json!({"senders": ["news@loom.example.test"], "undo_group": 3})),
        ("/api/sweep", json!({"dry_run": "yes"})),
    ] {
        assert_eq!(server.post(path, &body), (400, bad_request()), "{path} {body}");
    }
}

#[test]
fn what_names_no_sender_is_refused_in_words() {
    let server = running("full");
    let refused =
        |detail: &str| json!({"type": "/problems/refused", "title": "Refused", "status": 400, "detail": detail});
    assert_eq!(server.post("/api/cleanup/execute", &json!({})), (400, refused("bad address")));
    assert_eq!(server.post("/api/cleanup/execute", &json!({"from_addr": "loom"})), (400, refused("bad address")));
    assert_eq!(server.post("/api/cleanup/batch/preview", &json!({"senders": []})), (400, refused("bad senders")));
    assert_eq!(server.post("/api/cleanup/batch", &json!({"senders": ["loom"]})), (400, refused("bad senders")));
}

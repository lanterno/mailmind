//! The calendar's POSTs over a socket, as far as the served suites (tests/api) don't reach: what an answer to an
//! invite and an event file refuse before they look anything up, and the words a send route keeps to when what it
//! looks up fails.

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
fn a_field_its_schema_refuses_is_the_bad_request_400_before_anything_is_looked_up() {
    let server = running("full");
    for (path, body) in [
        ("/api/invite/rsvp", json!({})),
        ("/api/invite/rsvp", json!({"decision": "accept"})),
        ("/api/invite/rsvp", json!({"key": "seven", "decision": "accept"})),
        ("/api/invite/rsvp", json!({"key": 7, "decision": "maybe"})),
        ("/api/invite/rsvp", json!({"key": 7, "decision": "accept", "folder": 3})),
        ("/api/invite/rsvp", json!({"key": 7, "decision": "accept", "uid": "eleven"})),
        ("/api/calendar/event", json!({"key": "seven"})),
        ("/api/calendar/event", json!({"key": 7, "title": 7})),
        ("/api/calendar/event", json!({"key": 7, "start": ["2026-11-12"]})),
    ] {
        assert_eq!(server.post(path, &body), (400, bad_request()), "{path} {body}");
    }
}

#[test]
fn an_event_file_that_names_no_message_is_refused_in_words() {
    let server = running("full");
    assert_eq!(
        server.post("/api/calendar/event", &json!({"title": "Lunch", "start": "2026-11-12T12:30"})),
        (400, json!({"type": "/problems/refused", "title": "Refused", "status": 400, "detail": "missing message"}))
    );
}

#[test]
fn an_answer_whose_lookup_fails_says_only_that_it_failed() {
    // No table to look the message up in: the route's own 500, with nothing of what went wrong.
    let server = running("full");
    assert_eq!(
        server.post("/api/invite/rsvp", &json!({"key": 7, "decision": "accept"})),
        (500, json!({"type": "about:blank", "title": "Internal Server Error", "status": 500, "detail": "RSVP failed"}))
    );
}

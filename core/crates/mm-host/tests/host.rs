//! The host as a shell runs it: a route answered in its process is what the served app answers over HTTP, a refusal
//! is the same problem document, events reach a subscriber, and close waits for the work a request set going.
//!
//! Every home is a temporary folder with fictional data, its database keyed by a key made for the test and its
//! Keychain a file (`stand_in`): nothing here reads the login Keychain or reaches a host off this machine, and the
//! Claude API is a stand-in on loopback.

use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::Path;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::mpsc::{Receiver, Sender, channel};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use mm_host::{Answer, Event, Home, Host, Subscriber};
use mm_kernel::events::{Events, Topic};
use mm_server::environment::{Environment, Settings};
use mm_server::{App, Seams, Server};
use serde_json::{Value, json};

/// A key made for the tests: it guards only fictional mail.
const KEY: &str = "0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0";
const ADA: &str = "ada@example.test";

fn home(folder: &Path) -> Home {
    Home {
        folder: folder.to_path_buf(),
        variables: vec![("MAILMIND_DB_KEY".into(), KEY.into())],
        stand_in: Some(folder.join("keychain.json")),
    }
}

fn opened(folder: &Path) -> Host {
    Host::open(home(folder)).expect("the host opens a fictional home")
}

/// An answer's JSON without the moment it was made, which two apps never share.
fn timeless(body: &[u8]) -> Value {
    let mut value: Value = serde_json::from_slice(body).expect("a JSON body");
    if let Some(fields) = value.as_object_mut() {
        fields.remove("generated");
    }
    value
}

fn json_of(answer: &Answer) -> Value {
    serde_json::from_slice(&answer.body).expect("a JSON body")
}

/// The same home served over HTTP on loopback, as `mailmind-server` serves it, on the stand-in's seams.
struct Served {
    port: u16,
    /// Its app's own CSRF token, which a POST to it carries as the page's does.
    csrf: String,
    stop: tokio::sync::watch::Sender<bool>,
    runtime: tokio::runtime::Runtime,
}

impl Served {
    fn over(folder: &Path) -> Served {
        let runtime = tokio::runtime::Builder::new_multi_thread().enable_all().build().expect("a runtime");
        let environment = Environment::from_pairs([("MAILMIND_DB_KEY", KEY)]);
        let settings = Settings { home: folder.to_path_buf() };
        let seams = Seams {
            clock: Arc::new(mm_server::real::SystemClock),
            outside: Arc::new(mm_host::stand_in::LoopbackOnly),
            keychain: Arc::new(mm_host::stand_in::FileKeychain::new(folder.join("keychain.json"))),
            sanitizer: Arc::new(mm_server::real::Sanitizing),
            mail_hosts: Arc::new(mm_server::mail_hosts::NoLookups),
        };
        let server = runtime.block_on(Server::bind(0)).expect("a loopback port");
        let port = server.port().expect("its port");
        let app = Arc::new(
            App::assemble(&environment, &settings, folder.join(".page-not-served"), port, &seams).expect("assembled"),
        );
        let csrf = app.csrf.clone();
        let (stop, stopped) = tokio::sync::watch::channel(false);
        runtime.spawn(server.run(app, stopped, Duration::from_secs(1)));
        Served { port, csrf, stop, runtime }
    }

    /// What the page gets for `method` on `target`, carrying `csrf` as its token: the status, the Content-Type and the
    /// body.
    fn ask(&self, method: &str, target: &str, body: &[u8], csrf: Option<&str>) -> (u16, String, Vec<u8>) {
        let mut stream = TcpStream::connect(("127.0.0.1", self.port)).expect("connected");
        let mut head = format!(
            "{method} {target} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nSec-Fetch-Site: same-origin\r\nConnection: close\r\n",
            port = self.port
        );
        if method != "GET" {
            head += &format!(
                "Origin: http://127.0.0.1:{port}\r\nContent-Type: application/json\r\nContent-Length: {}\r\n",
                body.len(),
                port = self.port
            );
        }
        if let Some(token) = csrf {
            head += &format!("X-CSRF-Token: {token}\r\n");
        }
        stream.write_all(head.as_bytes()).and_then(|()| stream.write_all(b"\r\n")).expect("sent");
        stream.write_all(body).expect("sent");
        let mut read = Vec::new();
        stream.read_to_end(&mut read).expect("an answer");
        let end = read.windows(4).position(|w| w == b"\r\n\r\n").expect("a head");
        let head = String::from_utf8_lossy(&read[..end]).to_string();
        let status = head.split(' ').nth(1).and_then(|s| s.parse().ok()).expect("a status");
        let content_type = head
            .lines()
            .find_map(|line| line.to_ascii_lowercase().strip_prefix("content-type: ").map(str::to_owned))
            .unwrap_or_default();
        (status, content_type, read[end + 4..].to_vec())
    }
}

impl Drop for Served {
    fn drop(&mut self) {
        let _ = self.stop.send(true);
        std::thread::sleep(Duration::from_millis(50));
        let _ = &self.runtime;
    }
}

#[test]
fn a_route_answered_in_process_is_what_the_served_app_answers() {
    let folder = tempfile::tempdir().expect("a home");
    let host = opened(folder.path());
    let served = Served::over(folder.path());
    for target in ["/api/accounts", "/api/folders?account=ada%40example.test", "/api/inboxes?limit=5", "/api/health"] {
        let answer = host.request("GET", target, &[]);
        let (status, content_type, body) = served.ask("GET", target, &[], None);
        assert_eq!(answer.status, status, "{target}");
        assert_eq!(answer.content_type, content_type, "{target}");
        assert_eq!(timeless(&answer.body), timeless(&body), "{target}: the same answer");
    }
    let accounts = host.request("GET", "/api/accounts", &[]);
    assert_eq!(accounts.status, 200);
    assert_eq!(json_of(&accounts)["accounts"], json!([]), "a new home has no accounts");
    let named = |name: &str| accounts.headers.iter().any(|(n, _)| n == name);
    assert!(
        named("x-content-type-options") && named("cache-control"),
        "the perimeter's headers: {:?}",
        accounts.headers
    );
}

#[test]
fn a_refusal_is_the_problem_document_the_page_gets() {
    let folder = tempfile::tempdir().expect("a home");
    let host = opened(folder.path());
    let served = Served::over(folder.path());
    // No route: 404 and 501, as `_no_route` answers them.
    for (method, target) in [("GET", "/api/nowhere"), ("PUT", "/api/accounts")] {
        let answer = host.request(method, target, &[]);
        let (status, content_type, body) = served.ask(method, target, &[], Some(&served.csrf));
        assert_eq!((answer.status, &answer.content_type, &answer.body), (status, &content_type, &body), "{target}");
        assert_eq!(answer.content_type, "application/problem+json");
    }
    // A body that isn't a JSON object is the bad request.
    let refused = host.request("POST", "/api/snooze", b"[1, 2]");
    assert_eq!(refused.status, 400);
    assert_eq!(refused.content_type, "application/problem+json");
    assert_eq!(json_of(&refused)["type"], "/problems/refused");
    assert_eq!(json_of(&refused)["status"], 400);
    // A message no one has is the use case's not found.
    let missing = host.request("GET", "/api/message/body?key=4242&folder=INBOX", &[]);
    assert_eq!(missing.status, 404);
    assert_eq!(json_of(&missing)["type"], "/problems/not-found");
    let (status, _, body) = served.ask("POST", "/api/snooze", b"[1, 2]", Some(&served.csrf));
    assert_eq!((status, body), (400, refused.body.clone()), "the served app's bad request, byte for byte");
    // The host carries the token every POST must: the served app, asked without it, refuses at the perimeter.
    let (status, _, body) = served.ask("POST", "/api/snooze", b"[1, 2]", None);
    assert_eq!(status, 403, "{}", String::from_utf8_lossy(&body));
    // A target no request line could carry is the host's own bad request.
    let bad = host.request("GET", "api/accounts", &[]);
    assert_eq!((bad.status, json_of(&bad)["title"].clone()), (400, json!("Bad Request")));
}

/// A subscriber that hands what it hears to the test.
struct Heard(Mutex<Sender<Option<Event>>>);

impl Subscriber for Heard {
    fn event(&self, event: Event) {
        let _ = self.0.lock().expect("held").send(Some(event));
    }

    fn ended(&self) {
        let _ = self.0.lock().expect("held").send(None);
    }
}

fn hearing() -> (Arc<Heard>, Receiver<Option<Event>>) {
    let (send, heard) = channel();
    (Arc::new(Heard(Mutex::new(send))), heard)
}

fn next(heard: &Receiver<Option<Event>>) -> Option<Event> {
    heard.recv_timeout(Duration::from_secs(10)).expect("something heard within ten seconds")
}

#[test]
fn events_reach_a_subscriber_until_close_ends_them() {
    let folder = tempfile::tempdir().expect("a home");
    let host = opened(folder.path());
    let (subscriber, heard) = hearing();
    let _subscription = host.subscribe(subscriber).expect("the stream opens");
    let hello = next(&heard).expect("the stream's hello");
    assert_eq!(hello.name, "hello");
    assert!(json_of_text(&hello.data)["build"].is_string(), "{hello:?}");
    host.app().events.publish(Topic::Mute, json!({"account": ADA}));
    let muted = next(&heard).expect("the event published");
    assert_eq!(muted.name, "mute");
    assert_eq!(json_of_text(&muted.data), json!({"account": ADA}));
    assert!(muted.id.ends_with("-1"), "numbered as the page's stream numbers it: {}", muted.id);
    host.close();
    assert_eq!(next(&heard), None, "close ends the stream, and the subscriber hears so");
}

fn json_of_text(text: &str) -> Value {
    serde_json::from_str(text).expect("JSON data")
}

#[test]
fn a_cancelled_subscription_hears_nothing_more_and_frees_its_place() {
    let folder = tempfile::tempdir().expect("a home");
    let host = opened(folder.path());
    let mut held = Vec::new();
    for _ in 0..mm_server::events::MAX_LISTENERS {
        let (subscriber, _heard) = hearing();
        held.push(host.subscribe(subscriber).expect("a stream"));
    }
    let (subscriber, _heard) = hearing();
    let refused = host.subscribe(subscriber).expect_err("one stream too many");
    assert_eq!(refused.status, 503, "the route's own refusal");
    assert_eq!(json_of(&refused)["detail"], "too many listeners");
    held.pop().expect("one").cancel();
    let wait = Instant::now();
    loop {
        let (subscriber, heard) = hearing();
        if let Ok(subscription) = host.subscribe(subscriber) {
            assert_eq!(next(&heard).map(|e| e.name), Some("hello".into()));
            drop(subscription);
            break;
        }
        assert!(wait.elapsed() < Duration::from_secs(10), "a cancelled stream lets its place go");
        std::thread::sleep(Duration::from_millis(20));
    }
}

#[test]
fn close_waits_for_the_work_a_request_set_going() {
    let folder = tempfile::tempdir().expect("a home");
    // An IMAP server on loopback that takes the connection and says nothing for a while: a sync waits on it.
    let listener = TcpListener::bind(("127.0.0.1", 0)).expect("a loopback port");
    let port = listener.local_addr().expect("its port").port();
    let config = json!({"accounts": [{"email": ADA, "type": "imap", "host": "127.0.0.1", "port": port, "starttls": true,
                                      "credential": {"keychain": format!("account:{ADA}")}}]});
    std::fs::write(folder.path().join("mailmind.config.json"), config.to_string()).expect("a config");
    std::fs::write(folder.path().join("keychain.json"), json!({format!("account:{ADA}"): "fictional"}).to_string())
        .expect("a Keychain file");
    let hold = Duration::from_millis(1500);
    let (accepted, met) = channel();
    std::thread::spawn(move || {
        while let Ok((stream, _)) = listener.accept() {
            let _ = accepted.send(Instant::now());
            std::thread::sleep(hold);
            drop(stream);
        }
    });
    // The read-only profile: no background sync of its own, so the only sync is the one the request sets going.
    let mut quiet = home(folder.path());
    quiet.variables.push(("MAILMIND_PROFILE".into(), "read-only".into()));
    let host = Host::open(quiet).expect("opened");
    let refreshed = host.request("POST", "/api/refresh", b"{}");
    assert_eq!(refreshed.status, 200, "{}", String::from_utf8_lossy(&refreshed.body));
    let reached = met.recv_timeout(Duration::from_secs(10)).expect("the sync reached the server");
    host.close();
    assert!(reached.elapsed() >= hold, "close returned before the sync it waits for: {:?}", reached.elapsed());
    let after = host.request("GET", "/api/accounts", &[]);
    assert_eq!(after.status, 503, "a request after close is refused");
}

/// A stand-in for the Claude API on loopback: every request read whole and answered with a rewrite that cost more
/// than a cent, whatever the model, and counted.
fn claude_stand_in() -> (String, Arc<AtomicUsize>) {
    let listener = TcpListener::bind(("127.0.0.1", 0)).expect("a loopback port");
    let url = format!("http://127.0.0.1:{}", listener.local_addr().expect("its address").port());
    let heard = Arc::new(AtomicUsize::new(0));
    let counted = heard.clone();
    let reply = json!({"type": "message", "model": "claude-haiku-5-5", "stop_reason": "end_turn",
                       "content": [{"type": "text", "text": json!({"text": "Shorter."}).to_string()}],
                       "usage": {"input_tokens": 100_000, "output_tokens": 10_000}})
    .to_string();
    std::thread::spawn(move || {
        while let Ok((mut stream, _)) = listener.accept() {
            let mut read = Vec::new();
            let mut chunk = [0u8; 16 * 1024];
            let whole = loop {
                let n = stream.read(&mut chunk).unwrap_or(0);
                if n == 0 {
                    break false;
                }
                read.extend_from_slice(&chunk[..n]);
                let Some(end) = read.windows(4).position(|w| w == b"\r\n\r\n") else { continue };
                let head = String::from_utf8_lossy(&read[..end]).to_lowercase();
                let length = head
                    .lines()
                    .find_map(|l| l.strip_prefix("content-length:").and_then(|v| v.trim().parse::<usize>().ok()))
                    .unwrap_or(0);
                if read.len() >= end + 4 + length {
                    break true;
                }
            };
            if !whole {
                continue;
            }
            counted.fetch_add(1, Ordering::SeqCst);
            let answer = format!(
                "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{reply}",
                reply.len()
            );
            let _ = stream.write_all(answer.as_bytes());
        }
    });
    (url, heard)
}

#[test]
fn an_ai_call_at_the_daily_limit_goes_only_with_the_persons_leave() {
    let folder = tempfile::tempdir().expect("a home");
    let (url, heard) = claude_stand_in();
    // A cent a day, which one rewrite spends: the next is over the limit.
    std::fs::write(folder.path().join("mailmind.config.json"), json!({"ai_daily_limit_usd": 0.01}).to_string())
        .expect("a config");
    let mut given = home(folder.path());
    given.variables.push(("ANTHROPIC_API_KEY".into(), "sk-ant-test-fictional".into()));
    given.variables.push(("ANTHROPIC_BASE_URL".into(), url));
    let host = Host::open(given).expect("opened");
    let rewrite = json!({"text": "A long and winding note.", "op": "shorten"}).to_string();

    let first = host.request("POST", "/api/ai/rewrite", rewrite.as_bytes());
    assert_eq!(first.status, 200, "{}", String::from_utf8_lossy(&first.body));
    assert_eq!(json_of(&first)["text"], "Shorter.");
    assert_eq!(heard.load(Ordering::SeqCst), 1);

    let refused = host.request("POST", "/api/ai/rewrite", rewrite.as_bytes());
    assert_eq!(refused.status, 402, "{}", String::from_utf8_lossy(&refused.body));
    let problem = json_of(&refused);
    assert_eq!(problem["over_limit"], true);
    assert_eq!(problem["limit_micros"], 10_000);
    assert!(problem["spent_micros"].as_i64().is_some_and(|spent| spent >= 10_000), "{problem}");
    assert_eq!(heard.load(Ordering::SeqCst), 1, "a call over the limit is never sent");

    let allowed = host.request_over_limit("POST", "/api/ai/rewrite", rewrite.as_bytes());
    assert_eq!(allowed.status, 200, "{}", String::from_utf8_lossy(&allowed.body));
    assert_eq!(json_of(&allowed)["text"], "Shorter.");
    assert_eq!(heard.load(Ordering::SeqCst), 2, "the person's leave sends it");

    let again = host.request("POST", "/api/ai/rewrite", rewrite.as_bytes());
    assert_eq!(again.status, 402, "the leave was for that one call");
    assert_eq!(heard.load(Ordering::SeqCst), 2);
}

#[test]
fn a_request_with_the_leave_is_otherwise_the_request() {
    let folder = tempfile::tempdir().expect("a home");
    let host = opened(folder.path());
    for target in ["/api/accounts", "/api/health"] {
        let plain = host.request("GET", target, &[]);
        let with_leave = host.request_over_limit("GET", target, &[]);
        assert_eq!((plain.status, &plain.content_type), (with_leave.status, &with_leave.content_type), "{target}");
        assert_eq!(timeless(&plain.body), timeless(&with_leave.body), "{target}");
    }
    let refused = host.request_over_limit("POST", "/api/snooze", b"[1, 2]");
    assert_eq!(refused.body, host.request("POST", "/api/snooze", b"[1, 2]").body, "the same bad request");
    let bad = host.request_over_limit("GET", "api/accounts", &[]);
    assert_eq!(bad.status, 400, "a target no request line could carry");
}

#[test]
fn a_home_that_isnt_there_says_so() {
    let folder = tempfile::tempdir().expect("a home");
    let gone = folder.path().join("not-made");
    let said = Host::open(home(&gone)).expect_err("no folder, no app");
    assert!(!said.contains(KEY), "an error names no secret");
}

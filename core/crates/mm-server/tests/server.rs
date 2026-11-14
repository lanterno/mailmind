//! The server over a socket, as a browser and a script meet it: the perimeter ahead of every route, the headers on
//! every answer, the page and its files, and what no route answers. The served suites (tests/api) hold its answers
//! to the Python app's; these hold what they don't reach.

#![allow(clippy::unwrap_used)]

use std::io::{Read, Write};
use std::net::TcpStream;
use std::path::Path;
use std::sync::Arc;
use std::time::Duration;

use mm_kernel::seams::{Clock, Keychain, KeychainProblem, Moment, Outside, Secret, Tripped};
use mm_server::environment::{Environment, Settings};
use mm_server::real::Sanitizing;
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

const PAGE: &str = "<meta name=\"csrf\" content=\"__CSRF_TOKEN__\"><meta name=\"build\" content=\"__BUILD__\">\
                    <meta name=\"mailmind-place\" content=\"__PLACE__\">";

/// The server over a data directory holding `config`, serving a build with a page and a script when `built`.
fn running(config: &str, built: bool) -> Running {
    let home = tempfile::tempdir().unwrap();
    std::fs::write(home.path().join("mailmind.config.json"), config).unwrap();
    // A database the Python app's migrations brought to the version the server reads.
    let key = mm_store::DbKey::from_bytes([0x5e; 32]);
    let store = mm_store::Store::open(&home.path().join("mailmind.db"), &key).unwrap();
    store.set_version(mm_store::schema::VERSION).unwrap();
    drop(store);
    let dist = home.path().join("dist");
    if built {
        std::fs::create_dir_all(dist.join("assets")).unwrap();
        std::fs::write(dist.join("index.html"), PAGE).unwrap();
        std::fs::write(dist.join("assets/app-abc123.js"), "console.log('app');".repeat(20)).unwrap();
    }
    let runtime = tokio::runtime::Builder::new_multi_thread().enable_all().build().unwrap();
    let server = runtime.block_on(Server::bind(0)).unwrap();
    let port = server.port().unwrap();
    let environment = Environment::from_pairs([("MAILMIND_DB_KEY", "5e".repeat(32).as_str())]);
    let settings = Settings { home: home.path().to_path_buf() };
    let seams = Seams {
        clock: Arc::new(Frozen),
        outside: Arc::new(Open),
        keychain: Arc::new(Empty),
        sanitizer: Arc::new(Sanitizing),
    };
    let app = Arc::new(App::assemble(&environment, &settings, dist, port, &seams).unwrap());
    let csrf = app.csrf.clone();
    let (stop, stopped) = tokio::sync::watch::channel(false);
    runtime.spawn(server.run(app, stopped, Duration::from_secs(1)));
    std::mem::forget(stop);
    Running { port, csrf, _home: home, _runtime: runtime }
}

struct Answer {
    status: u16,
    headers: Vec<(String, String)>,
    body: Vec<u8>,
}

impl Answer {
    fn header(&self, name: &str) -> Option<&str> {
        self.headers.iter().find(|(n, _)| n == name).map(|(_, v)| v.as_str())
    }

    fn json(&self) -> Value {
        serde_json::from_slice(&self.body).unwrap()
    }
}

impl Running {
    /// One request, its request line and headers as given, a body after them when there is one.
    fn ask(&self, line: &str, headers: &[(&str, &str)], body: &[u8]) -> Answer {
        let mut stream = TcpStream::connect(("127.0.0.1", self.port)).unwrap();
        stream.set_read_timeout(Some(Duration::from_secs(10))).unwrap();
        let mut sent = format!("{line} HTTP/1.1\r\n");
        for (name, value) in headers {
            sent.push_str(&format!("{name}: {value}\r\n"));
        }
        sent.push_str("Connection: close\r\n\r\n");
        stream.write_all(sent.as_bytes()).unwrap();
        stream.write_all(body).unwrap();
        let mut got = Vec::new();
        let _ = stream.read_to_end(&mut got);
        let end = got.windows(4).position(|w| w == b"\r\n\r\n").unwrap();
        let head = String::from_utf8(got[..end].to_vec()).unwrap();
        let mut lines = head.split("\r\n");
        let status = lines.next().unwrap().split(' ').nth(1).unwrap().parse().unwrap();
        let headers =
            lines.map(|l| l.split_once(": ").unwrap()).map(|(n, v)| (n.to_lowercase(), v.to_owned())).collect();
        Answer { status, headers, body: got[end + 4..].to_vec() }
    }

    fn own(&self) -> String {
        format!("127.0.0.1:{}", self.port)
    }

    /// A GET as the page's own fetch sends it.
    fn get(&self, path: &str) -> Answer {
        self.ask(&format!("GET {path}"), &[("Host", &self.own()), ("Sec-Fetch-Site", "same-origin")], b"")
    }

    /// A request of `method` that gets past every check.
    fn guarded(&self, method: &str, path: &str, body: &[u8]) -> Answer {
        let (own, origin, length) = (self.own(), format!("http://{}", self.own()), body.len().to_string());
        let headers = [
            ("Host", own.as_str()),
            ("Origin", origin.as_str()),
            ("Sec-Fetch-Site", "same-origin"),
            ("X-CSRF-Token", self.csrf.as_str()),
            ("Content-Length", length.as_str()),
        ];
        self.ask(&format!("{method} {path}"), &headers, body)
    }
}

const ADA: &str = r#"{"accounts": [{"email": "ada@example.test", "type": "imap", "host": "127.0.0.1", "port": 1,
    "credential": {"keychain": "account:ada@example.test"}}]}"#;

fn problem(status: u16, detail: &str) -> Value {
    let title = match status {
        403 => "Forbidden",
        404 => "Not Found",
        413 => "Content Too Large",
        500 => "Internal Server Error",
        501 => "Not Implemented",
        _ => unreachable!(),
    };
    json!({"type": "about:blank", "title": title, "status": status, "detail": detail})
}

#[test]
fn the_page_carries_this_runs_token_and_its_policy() {
    let server = running(ADA, true);
    let page = server.get("/");
    assert_eq!(page.status, 200);
    let text = String::from_utf8(page.body.clone()).unwrap();
    assert!(text.starts_with(&format!("<meta name=\"csrf\" content=\"{}\">", server.csrf)));
    assert!(!text.contains("__BUILD__") && text.ends_with("<meta name=\"mailmind-place\" content=\"\">"));
    assert_eq!(page.header("content-type"), Some("text/html; charset=utf-8"));
    assert_eq!(page.header("cache-control"), Some("no-store"));
    assert!(page.header("content-security-policy").unwrap().starts_with("default-src 'none'; script-src 'self'"));
    assert_eq!(page.header("x-frame-options"), Some("DENY"));
    assert_eq!(page.header("date"), None, "no Date header, as uvicorn is told to send none");
    assert_eq!(page.header("server"), None);
    for place in ["/all", "/all/", "/m/", "/m/key/INBOX/7", "/brief/today", "/search", "/documents"] {
        assert_eq!(server.get(place).status, 200, "{place}");
    }
    assert_eq!(server.get("/m").status, 404);
}

#[test]
fn with_nothing_built_the_page_says_how_to_build_it() {
    let server = running(ADA, false);
    let page = server.get("/");
    assert_eq!((page.status, page.body.as_slice()), (404, &b"The app isn't built. Run: just build-web\n"[..]));
    assert_eq!(page.header("content-type"), Some("text/plain; charset=utf-8"));
}

#[test]
fn a_file_of_the_page_is_served_by_its_etag_and_compressed_when_asked() {
    let server = running(ADA, true);
    let script = server.get("/assets/app-abc123.js");
    assert_eq!(script.status, 200);
    assert_eq!(script.header("content-type"), Some("text/javascript; charset=utf-8"));
    assert_eq!(script.header("cache-control"), Some("public, max-age=31536000, immutable"));
    assert_eq!(script.header("vary"), Some("Accept-Encoding"));
    let etag = script.header("etag").unwrap().to_owned();
    assert!(etag.starts_with("W/\"") && etag.len() == 3 + 24 + 1);

    let own = server.own();
    let base = [("Host", own.as_str()), ("Sec-Fetch-Site", "same-origin")];
    let zipped = server.ask("GET /assets/app-abc123.js", &[base[0], base[1], ("Accept-Encoding", "br, gzip")], b"");
    assert_eq!(zipped.header("content-encoding"), Some("gzip"));
    assert!(zipped.body.len() < script.body.len() && zipped.body.starts_with(&[0x1f, 0x8b]));
    let same = server.ask("GET /assets/app-abc123.js", &[base[0], base[1], ("If-None-Match", &etag)], b"");
    assert_eq!((same.status, same.body.len()), (304, 0));
    assert_eq!(same.header("etag"), Some(etag.as_str()));

    for missing in ["/assets/nope.js", "/assets/", "/assets/.hidden.js", "/assets/app-abc123.txt", "/assets/a\\b.js"] {
        let answer = server.get(missing);
        assert_eq!((answer.status, answer.body.as_slice()), (404, &b"not found"[..]), "{missing}");
    }
}

#[test]
fn a_refusal_is_a_problem_document_with_every_answers_headers() {
    let server = running(ADA, true);
    let rebound =
        server.ask("GET /api/accounts", &[("Host", "evil.example.test"), ("Sec-Fetch-Site", "same-origin")], b"");
    assert_eq!((rebound.status, rebound.json()), (403, problem(403, "bad host/origin")));
    assert_eq!(rebound.header("content-type"), Some("application/problem+json"));
    assert_eq!(rebound.header("cache-control"), Some("no-store"));
    assert_eq!(rebound.header("x-content-type-options"), Some("nosniff"));
    let page = server.ask("GET /", &[("Host", &server.own()), ("Sec-Fetch-Site", "cross-site")], b"");
    assert_eq!((page.status, page.json()), (403, problem(403, "cross-site request")));
    assert_eq!(page.header("cache-control"), None, "the page's refusal is no part of the API");
    let tokenless = server.ask("POST /api/seen", &[("Host", &server.own()), ("Sec-Fetch-Site", "same-origin")], b"");
    assert_eq!(tokenless.json(), problem(403, "bad or missing CSRF token"));
}

#[test]
fn a_body_over_its_cap_is_refused_before_it_is_read() {
    let server = running(ADA, true);
    let own = server.own();
    let over = (1u64 << 20) + 1;
    let length = over.to_string();
    let headers = [("Host", own.as_str()), ("Sec-Fetch-Site", "same-origin"), ("Content-Length", length.as_str())];
    let answer = server.ask("POST /api/seen", &headers, b"");
    assert_eq!((answer.status, answer.json()), (413, problem(413, "payload too large")));
    let sent = (38u64 << 20).to_string();
    let send = [("Host", own.as_str()), ("Sec-Fetch-Site", "same-origin"), ("Content-Length", sent.as_str())];
    assert_eq!(server.ask("POST /api/send", &send, b"").json(), problem(403, "bad or missing CSRF token"));
    assert_eq!(
        server.guarded("PUT", "/api/folders", b"{}").status,
        413,
        "any method but GET and HEAD may carry nothing"
    );
}

#[test]
fn only_get_and_post_are_answered_and_only_on_a_path_as_sent() {
    let server = running(ADA, true);
    let head = server.ask("HEAD /", &[("Host", &server.own()), ("Sec-Fetch-Site", "same-origin")], b"");
    assert_eq!((head.status, head.header("content-type"), head.body.len()), (501, Some("text/plain"), 0));
    assert_eq!(head.header("x-frame-options"), Some("DENY"));
    assert_eq!(server.guarded("PUT", "/api/folders", b"").json(), problem(501, "not implemented"));
    let delete = server.guarded("DELETE", "/", b"");
    assert_eq!((delete.status, delete.body.as_slice()), (501, &b"not implemented"[..]));
    assert_eq!(server.guarded("POST", "/api/folders", b"{}").json(), problem(404, "not found"));
    assert_eq!(server.guarded("POST", "/api/no-such-route", b"{}").json(), problem(404, "not found"));
    assert_eq!(server.get("/api").json(), problem(404, "not found"));
    let nope = server.get("/nope");
    assert_eq!(
        (nope.status, nope.header("content-type"), nope.body.as_slice()),
        (404, Some("text/plain"), &b"not found"[..])
    );
    assert_eq!(server.get("/%61pi/folders?account=ada@example.test").status, 404, "a path is judged as sent");
    let absolute = server.get(&format!("http://{}/api/accounts", server.own()));
    assert_eq!((absolute.status, absolute.body.as_slice()), (404, &b"not found"[..]));
}

#[test]
fn the_accounts_are_listed_as_the_api_may_show_them() {
    let server = running(ADA, true);
    let listed = server.get("/api/accounts");
    assert_eq!(listed.status, 200);
    assert_eq!(listed.header("content-type"), Some("application/json"));
    let body = listed.json();
    assert_eq!(body["generated"], "2026-11-02T12:00:00+00:00");
    assert_eq!(
        body["accounts"][0]["credential"],
        json!({"style": "keychain", "set": true, "item": "account:ada@example.test"})
    );
    assert!(String::from_utf8(listed.body).unwrap().starts_with(r#"{"ok": true, "accounts": [{"email": "#));
}

#[test]
fn the_folders_need_an_account_the_app_knows() {
    let server = running(ADA, true);
    let none = server.get("/api/folders?account=");
    assert_eq!(
        (none.status, none.json()),
        (
            400,
            json!({"type": "/problems/refused", "title": "Refused", "status": 400, "detail": "account required", "folders": []})
        )
    );
    let unknown = server.get("/api/folders?account=nobody@example.test");
    assert_eq!(unknown.json()["detail"], "unknown account");
    let bad = server.get("/api/messages?account=ada@example.test&limit=lots");
    assert_eq!(
        bad.json(),
        json!({"type": "/problems/refused", "title": "Refused", "status": 400, "detail": "bad request"})
    );
    let unoffered = server.get("/api/export/archive?format=pst");
    assert_eq!(unoffered.json(), bad.json());
}

#[test]
fn a_failure_answers_the_fixed_500_and_says_nothing_of_why() {
    let server = running("[]", true);
    let failed = server.get("/api/accounts");
    assert_eq!((failed.status, failed.json()), (500, problem(500, "request failed")));
    assert_eq!(failed.header("cache-control"), Some("no-store"));
}

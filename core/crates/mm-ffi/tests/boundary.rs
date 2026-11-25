//! The FFI boundary holds (test-strategy.md section 6): what a shell hands `CoreHost` and `Session` that doesn't read,
//! is too big, comes after close or from many threads at once is answered, never a panic across the boundary or a
//! 500. A panic here would cross into Swift as an abort of the person's app.
//!
//! The homes are temporary folders with no mail, keyed by a key made for the test, their Keychain a file, and in a test
//! world so no worker starts.

#![cfg(feature = "host")]

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Arc, Barrier};
use std::thread;

use mailmind_core::{CoreHome, CoreHost, HostProblem, Session};

/// A key made for the tests: it guards only fictional mail.
const KEY: &str = "1f2e3d4c5b6a79889786a5b4c3d2e1f01f2e3d4c5b6a79889786a5b4c3d2e1f0";

/// A temporary folder of the test's own, removed as it drops.
struct Folder(PathBuf);

impl Folder {
    fn new(name: &str) -> Folder {
        let path = std::env::temp_dir().join(format!("mm-ffi-boundary-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&path);
        std::fs::create_dir_all(&path).expect("a home");
        Folder(path)
    }
}

impl Drop for Folder {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn home(folder: &Folder, key: &str) -> CoreHome {
    CoreHome {
        folder: folder.0.to_string_lossy().into_owned(),
        variables: HashMap::from([
            ("MAILMIND_DB_KEY".to_owned(), key.to_owned()),
            ("MAILMIND_TEST_CLOCK".to_owned(), "paused".to_owned()),
            ("MAILMIND_TEST_NOW".to_owned(), "2026-11-02T12:00:00Z".to_owned()),
        ]),
        stand_in: Some(folder.0.join("keychain.json").to_string_lossy().into_owned()),
    }
}

fn opened(folder: &Folder) -> Arc<CoreHost> {
    CoreHost::open(home(folder, KEY)).expect("the host opens a fictional home")
}

/// Its status, which must be an answer to the request and not the app failing on it.
fn status(host: &CoreHost, method: &str, target: &str, body: &[u8]) -> u16 {
    let answer = host.request(method.to_owned(), target.to_owned(), body.to_vec());
    // 501 is HTTP's answer to a method no server knows, and 503 a closed host's: neither is the app failing.
    assert!(
        answer.status < 500 || [501, 503].contains(&answer.status),
        "{method} {}: {}",
        shown(target),
        answer.status
    );
    answer.status
}

/// A target as an assertion may print it: cut, since some are a megabyte long.
fn shown(target: &str) -> String {
    target.chars().take(60).collect()
}

#[test]
fn a_request_that_doesnt_read_is_refused_with_a_4xx() {
    let folder = Folder::new("unread");
    let host = opened(&folder);
    assert_eq!(status(&host, "GET", "/api/preferences", b""), 200, "the host answers a request that reads");

    let long_path = format!("/api/{}", "a".repeat(1 << 20));
    let cases: [(&str, &str); 8] = [
        ("", "/api/preferences"),
        ("G E T", "/api/preferences"),
        ("BREW", "/api/preferences"),
        ("GET", ""),
        ("GET", "api/preferences"),
        ("GET", "/api/\u{0}preferences"),
        ("GET", "http://elsewhere.example.test/api/preferences"),
        ("GET", &long_path),
    ];
    for (method, target) in cases {
        let got = status(&host, method, target, b"");
        let refused = (400..500).contains(&got) || (method == "BREW" && got == 501);
        assert!(refused, "{method:?} {}: {got}", shown(target));
    }
    // Bytes that aren't UTF-8 in a query name an account no one has, which lists nothing: answered, not failed on.
    status(&host, "GET", "/api/messages?account=%ff%fe&folder=%c0", b"");
    host.close();
}

#[test]
fn a_body_that_isnt_what_the_route_reads_is_refused_with_a_4xx() {
    let folder = Folder::new("bodies");
    let host = opened(&folder);
    let deep = "[".repeat(100_000);
    let huge = vec![b' '; 40 << 20];
    let bodies: [&[u8]; 6] =
        [b"\xff\xfe\xfd not UTF-8", b"{\"theme\": \"\xc0\xaf\"}", b"{\"theme\":", deep.as_bytes(), b"[1, 2, 3]", &huge];
    for body in bodies {
        let got = status(&host, "POST", "/api/preferences", body);
        assert!((400..500).contains(&got), "a body of {} bytes: {got}", body.len());
    }
    host.close();
}

#[test]
fn after_close_every_call_is_answered_and_closing_again_does_nothing() {
    let folder = Folder::new("closed");
    let host = opened(&folder);
    host.close();
    assert_eq!(status(&host, "GET", "/api/preferences", b""), 503);
    assert_eq!(
        host.request_over_limit("POST".to_owned(), "/api/ai/draft-reply".to_owned(), b"{}".to_vec()).status,
        503
    );
    host.woke();
    assert!(matches!(host.subscribe(Arc::new(Deaf)), Err(HostProblem::Refused { .. })), "no stream after close");
    host.close();
    assert_eq!(status(&host, "GET", "/api/messages", b""), 503);
}

#[test]
fn many_threads_at_once_and_a_close_among_them_get_answers_not_a_panic() {
    let folder = Folder::new("threads");
    let host = opened(&folder);
    const THREADS: usize = 16;
    let start = Arc::new(Barrier::new(THREADS + 1));
    let workers: Vec<_> = (0..THREADS)
        .map(|n| {
            let (host, start) = (Arc::clone(&host), Arc::clone(&start));
            thread::spawn(move || {
                start.wait();
                let mut seen = Vec::new();
                for i in 0..40 {
                    let got = match (n + i) % 4 {
                        0 => status(&host, "GET", "/api/preferences", b""),
                        1 => status(&host, "GET", "/api/messages?account=ada%40example.test&folder=INBOX", b""),
                        2 => status(&host, "POST", "/api/preferences", b"{\"nothing\": "),
                        _ => status(&host, "GET", "/api/outbox", b""),
                    };
                    seen.push(got);
                    if n == 0 && i == 20 {
                        host.close();
                    }
                }
                seen
            })
        })
        .collect();
    start.wait();
    let answers: Vec<u16> = workers.into_iter().flat_map(|w| w.join().expect("no thread panicked")).collect();
    assert_eq!(answers.len(), THREADS * 40);
    assert!(answers.contains(&503), "the close was heard");
    assert_eq!(status(&host, "GET", "/api/preferences", b""), 503);
}

#[test]
fn a_home_or_a_database_that_cant_open_is_a_refusal() {
    let folder = Folder::new("refused");
    assert!(matches!(CoreHost::open(home(&folder, "not a key")), Err(HostProblem::NotOpened { .. })));
    assert!(matches!(CoreHost::open(home(&folder, &"ab".repeat(16))), Err(HostProblem::NotOpened { .. })));
    let mut nowhere = home(&folder, KEY);
    nowhere.folder = folder.0.join("a-file").to_string_lossy().into_owned();
    std::fs::write(folder.0.join("a-file"), b"not a folder").expect("a file where the home would be");
    assert!(matches!(CoreHost::open(nowhere), Err(HostProblem::NotOpened { .. })));

    let database = folder.0.join("session.db").to_string_lossy().into_owned();
    assert!(Session::open(database, vec![1, 2, 3], 1).is_err(), "a key of three bytes");
    let missing = folder.0.join("no-such-folder").join("session.db").to_string_lossy().into_owned();
    assert!(Session::open(missing, vec![7; 32], 1).is_err());
}

/// A listener that hears nothing: the stream it would get is refused first.
struct Deaf;

impl mailmind_core::EventListener for Deaf {
    fn on_event(&self, _event: mailmind_core::CoreEvent) {}
    fn on_end(&self) {}
}

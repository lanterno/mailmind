//! A panic, as `mailmind-server` and the Mac app log it (`logging::log_panics`): by the thread it happened on and where
//! in the code, never by what it said, which can quote mail. A test binary of its own, since the hook and the log are
//! the process's. Every address here is fictional.

#![allow(clippy::unwrap_used)]

use mm_server::logging::{self, FILE};
use serde_json::{Value, json};

#[test]
fn a_dependencys_path_is_named_from_its_crate_on_and_the_cores_as_it_is() {
    let registry = "/Users/someone/.cargo/registry/src/index.crates.io-1949cf8c6b5b557f/mail-parser-1.0.0/src/lib.rs";
    assert_eq!(logging::place(registry, 12), "mail-parser-1.0.0/src/lib.rs:12");
    assert_eq!(logging::place("crates/mm-content/src/mime.rs", 604), "crates/mm-content/src/mime.rs:604");
    assert_eq!(
        logging::place("/rustc/abc123/library/core/src/option.rs", 9),
        "/rustc/abc123/library/core/src/option.rs:9"
    );
    assert_eq!(logging::place("/Users/someone/elsewhere/src/main.rs", 3), "main.rs:3");
}

#[test]
fn a_panic_is_logged_by_its_thread_and_place_never_by_its_message() {
    let home = tempfile::tempdir().unwrap();
    logging::keep_in(home.path(), false);
    logging::log_panics();
    // Asked again, nothing more: one line a panic.
    logging::log_panics();
    let line = line!() + 3;
    let named = std::thread::Builder::new().name("job:sync".into());
    let ended = named.spawn(|| -> () {
        panic!("Subject: Your invoice from ada@example.test");
    });
    assert!(ended.unwrap().join().is_err());
    let unnamed = std::thread::Builder::new().name("ada@example.test".into());
    assert!(unnamed.spawn(|| -> () { panic!("a secret") }).unwrap().join().is_err());

    let text = std::fs::read_to_string(home.path().join(FILE)).unwrap();
    assert!(!text.contains("invoice") && !text.contains("example.test") && !text.contains("secret"), "{text}");
    let written: Vec<Value> = text.lines().map(|l| serde_json::from_str(l).unwrap()).collect();
    assert_eq!(written.len(), 2, "{text}");
    let at = format!("crates/mm-server/tests/panics.rs:{line}");
    assert_eq!(
        (&written[0]["level"], &written[0]["logger"], &written[0]["error"]),
        (&json!("ERROR"), &json!("panic"), &json!("Panic"))
    );
    assert_eq!(written[0]["msg"], json!(format!("a thread panicked: job:sync at {at}")));
    assert_eq!((&written[0]["what"], &written[0]["path"]), (&json!("job:sync"), &json!(at)));
    // A thread named by an address is withheld, as any value holding one is.
    assert_eq!(written[1]["what"], json!("<withheld>"));
}

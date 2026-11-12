//! The contract as JSON lines: what the browser worker, the desktop channel and the daemon all speak.

#![allow(clippy::unwrap_used)]

use std::sync::{Arc, Mutex};

use mm_app::wire::{Dispatcher, Outcome, Outgoing, Sink};
use mm_app::{DbKey, Refusal, Session};
use mm_fixtures::FIXTURE_KEY_HEX;
use mm_store::Store;

fn dispatcher() -> (Dispatcher, Arc<Mutex<Vec<Outgoing>>>) {
    let key = DbKey::from_hex(FIXTURE_KEY_HEX).unwrap();
    let mut store = Store::open_in_memory(&key).unwrap();
    store.create_list_tables().unwrap();
    store.add_messages(mm_fixtures::messages(120, 7)).unwrap();
    let session = Session::with_store(store, 0).unwrap();
    let sent = Arc::new(Mutex::new(Vec::new()));
    let into = sent.clone();
    let sink: Sink = Arc::new(move |m| into.lock().unwrap().push(m));
    (Dispatcher::new(session, sink), sent)
}

const OPEN: &str = r#"{"id":1,"method":"open_list","params":{"query":{"accounts":["work@example.test","home@example.test","club@example.test"],"folder":"INBOX"}}}"#;

#[test]
fn opening_a_list_replies_with_its_view_and_size() {
    let (d, sent) = dispatcher();
    d.handle_line(OPEN);
    assert_eq!(
        sent.lock().unwrap().as_slice(),
        [Outgoing::Reply { id: 1, outcome: Outcome::Opened { view: 1, total: 120 } }]
    );
}

#[test]
fn a_window_is_answered_then_its_batch_follows() {
    let (d, sent) = dispatcher();
    d.handle_line(OPEN);
    d.handle_line(r#"{"id":2,"method":"set_visible","params":{"view":1,"first":0,"count":20}}"#);
    let sent = sent.lock().unwrap();
    assert_eq!(sent.len(), 3);
    assert!(matches!(&sent[1], Outgoing::Reply { id: 2, outcome: Outcome::Done } | Outgoing::Batch { .. }));
    let batch = sent.iter().find_map(|m| match m {
        Outgoing::Batch { view: 1, batch } => Some(batch),
        _ => None,
    });
    assert_eq!(batch.map(|b| b.held_count), Some(100));
}

#[test]
fn a_closed_view_is_not_there() {
    let (d, sent) = dispatcher();
    d.handle_line(OPEN);
    d.handle_line(r#"{"id":2,"method":"close_view","params":{"view":1}}"#);
    d.handle_line(r#"{"id":3,"method":"set_visible","params":{"view":1,"first":0,"count":20}}"#);
    assert_eq!(
        sent.lock().unwrap().last(),
        Some(&Outgoing::Refused { id: 3, refusal: Refusal::not_found("this view") })
    );
}

#[test]
fn a_line_that_is_no_request_is_refused_under_its_id_without_echoing_it() {
    let (d, sent) = dispatcher();
    d.handle_line(r#"{"id":9,"method":"read_everything","params":{"secret":"hunter2"}}"#);
    let sent = sent.lock().unwrap();
    let Some(Outgoing::Refused { id: 9, refusal: Refusal::Invalid { detail } }) = sent.first() else {
        panic!("not refused under its id: {sent:?}");
    };
    assert!(!detail.contains("hunter2") && !detail.contains("read_everything"));
}

#[test]
fn what_is_not_json_is_refused_under_id_0() {
    let (d, sent) = dispatcher();
    d.handle_line("not json");
    assert!(matches!(sent.lock().unwrap().first(), Some(Outgoing::Refused { id: 0, .. })));
}

#[test]
fn messages_have_the_shape_the_typescript_client_reads() {
    let (d, sent) = dispatcher();
    d.handle_line(OPEN);
    let json = serde_json::to_value(&sent.lock().unwrap()[0]).unwrap();
    assert_eq!(
        json,
        serde_json::json!({"kind": "reply", "id": 1, "outcome": {"kind": "opened", "view": 1, "total": 120}})
    );
}

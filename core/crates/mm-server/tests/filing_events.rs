//! What a filing request tells the open tabs, against an IMAP server on loopback: moves that land within the request
//! are told once, as `commands-changed`, however many messages it moved, since no worker will tell of them; one the
//! worker has is the tray's, and the worker tells of it once it lands (the Python app's
//! tests/unit/mailbox/test_thread_filing.py).

#![allow(clippy::unwrap_used)]

mod stand_in;

use std::pin::Pin;
use std::task::{Context, Poll, Waker};

use futures_core::Stream;
use mm_server::events::TabStream;
use serde_json::json;
use stand_in::{ADA, World, note, world};

/// A reply to Note 1, in its conversation.
fn reply(uid: u32) -> Vec<u8> {
    format!(
        "From: Lin <lin@example.test>\r\nTo: Ada <{ADA}>\r\nSubject: Re: Note 1\r\n\
         Date: Mon, 02 Nov 2026 1{uid}:00:00 +0000\r\nMessage-ID: <reply-{uid}@example.test>\r\n\
         In-Reply-To: <note-1@example.test>\r\nReferences: <note-1@example.test>\r\n\r\nYes, {uid}.\r\n"
    )
    .into_bytes()
}

/// Ada's Inbox of Note 1 and two replies to it, and her Archive, synced.
fn synced() -> World {
    let imap = stand_in::Imap::start();
    imap.add(ADA, "INBOX", [(1, note(1)), (2, reply(2)), (3, reply(3))]);
    imap.add(ADA, "Archive", []);
    let w = world(imap, &[ADA], &[]);
    w.app.syncing.sync_once();
    w.app.folders.of_account(ADA).unwrap();
    w
}

/// The message key of Ada's INBOX copy `uid`, as a marked row names it.
fn key(w: &World, uid: u32) -> i64 {
    stand_in::database(w.home.path())
        .read(|c| {
            c.query_row(
                "SELECT message_key FROM messages WHERE account=? AND folder='INBOX' AND uid=?",
                (ADA, uid),
                |r| r.get(0),
            )
        })
        .unwrap()
}

/// The topics a tab heard since it opened, in order.
fn heard(tab: &mut TabStream) -> Vec<String> {
    let mut frames = String::new();
    while let Poll::Ready(Some(Ok(frame))) = Pin::new(&mut *tab).poll_next(&mut Context::from_waker(Waker::noop())) {
        frames.push_str(&frame);
    }
    frames.lines().filter_map(|l| l.strip_prefix("event: ")).filter(|t| *t != "hello").map(str::to_owned).collect()
}

fn runtime() -> tokio::runtime::Runtime {
    tokio::runtime::Builder::new_current_thread().enable_time().build().unwrap()
}

#[test]
fn a_conversation_filed_within_the_request_tells_the_open_tabs_once() {
    let w = synced();
    let runtime = runtime();
    let _entered = runtime.enter();
    let mut tab = TabStream::new(w.app.events.listen(None).unwrap(), "b1");
    let said = w.app.filing.file_thread("archive", w.pk(ADA, "INBOX", 1), None, None, None).unwrap();
    assert_eq!((&said["msg"], &said["count"], &said["queued"]), (&json!("filed"), &json!(3), &json!(0)));
    assert_eq!(w.imap.ids_in(ADA, "Archive").len(), 3);
    assert_eq!(heard(&mut tab), ["commands-changed"]);
}

#[test]
fn a_message_filed_within_the_request_tells_the_open_tabs() {
    let w = synced();
    let runtime = runtime();
    let _entered = runtime.enter();
    let mut tab = TabStream::new(w.app.events.listen(None).unwrap(), "b1");
    let said = w.app.filing.file("archive", w.pk(ADA, "INBOX", 2), None, None, None).unwrap();
    assert_eq!(said["msg"], "archived");
    assert_eq!(heard(&mut tab), ["commands-changed"]);
}

#[test]
fn rows_filed_together_tell_the_open_tabs_once() {
    let w = synced();
    let runtime = runtime();
    let _entered = runtime.enter();
    let mut tab = TabStream::new(w.app.events.listen(None).unwrap(), "b1");
    let rows: Vec<_> = [2, 3].map(|uid| ((key(&w, uid), "INBOX".to_owned(), Some(i64::from(uid))), false)).to_vec();
    let said = w.app.filing.file_many("archive", &rows, None, None).unwrap();
    assert_eq!((&said["count"], &said["queued"]), (&json!(2), &json!(0)));
    assert_eq!(heard(&mut tab), ["commands-changed"]);
}

#[test]
fn a_move_the_worker_has_is_the_trays_and_tells_of_no_landing() {
    let w = synced();
    let runtime = runtime();
    let _entered = runtime.enter();
    let mut tab = TabStream::new(w.app.events.listen(None).unwrap(), "b1");
    w.imap.away(true);
    let said = w.app.filing.file_thread("archive", w.pk(ADA, "INBOX", 1), None, None, None).unwrap();
    assert_eq!((&said["msg"], &said["queued"]), (&json!("queued"), &json!(3)));
    let topics = heard(&mut tab);
    assert_eq!(topics.iter().filter(|t| *t == "outbox").count(), 3, "{topics:?}");
    assert!(!topics.iter().any(|t| t == "commands-changed"), "{topics:?}");
}

//! Changes a person makes while their server is away go as soon as it is back, against an IMAP server on loopback:
//! a sync that works after one that failed, a wake between them or not, and a person's Try again each let go of the
//! waits the changes were backing off, rather than leave them for minutes more.

#![allow(clippy::unwrap_used)]

mod stand_in;

use stand_in::{ADA, World, note, world};

/// Ada's Inbox of two notes and her Archive, synced, then the server gone, and a note archived meanwhile, tried by the
/// worker and backing off.
fn archived_while_away() -> World {
    let imap = stand_in::Imap::start();
    imap.add(ADA, "INBOX", (1..=2).map(|uid| (uid, note(uid))));
    imap.add(ADA, "Archive", []);
    let w = world(imap, &[ADA], &[]);
    w.app.syncing.sync_once();
    w.app.folders.of_account(ADA).unwrap();
    w.imap.away(true);
    let filed = w.app.filing.file("archive", w.pk(ADA, "INBOX", 1), None, Some("m-1"), None).unwrap();
    assert_eq!(filed["status"], "queued");
    w.app.commands.drain().unwrap();
    assert!(archived(&w).is_empty());
    w
}

fn archived(w: &World) -> Vec<String> {
    w.imap.ids_in(ADA, "Archive")
}

/// The server is back, and the worker passes by at once: the change still waits out its back-off.
fn back_but_still_waiting(w: &World) {
    w.imap.away(false);
    w.app.commands.drain().unwrap();
    assert!(archived(w).is_empty(), "it waits out its back-off, with nothing to say the server is back");
}

#[test]
fn a_sync_that_works_once_the_server_is_back_sends_what_waited_at_once() {
    let w = archived_while_away();
    w.app.syncing.sync_once();
    back_but_still_waiting(&w);
    w.app.syncing.sync_once();
    w.app.commands.drain().unwrap();
    assert_eq!(archived(&w), ["<note-1@example.test>"], "no minutes on: the clock hasn't moved");
}

#[test]
fn try_again_sends_what_waited_with_the_sync_it_sets_going() {
    let w = archived_while_away();
    back_but_still_waiting(&w);
    // No sync has failed in this process, as after a restart: the banner speaks from the record.
    assert_eq!(w.app.syncing.retry(Some(ADA)).unwrap(), ADA);
    w.app.commands.drain().unwrap();
    assert_eq!(archived(&w), ["<note-1@example.test>"]);
}

#[test]
fn a_wake_between_the_sync_that_failed_and_the_one_that_works_still_sends_what_waited() {
    let w = archived_while_away();
    w.app.syncing.sync_once();
    w.app.syncing.woke();
    back_but_still_waiting(&w);
    w.app.syncing.sync_once();
    w.app.commands.drain().unwrap();
    assert_eq!(archived(&w), ["<note-1@example.test>"]);
}

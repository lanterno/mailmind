//! Changes a person makes while their server is away, against an IMAP server on loopback that keeps what it is
//! asked: each waits in the queue, the lists show it made, and once the server is back each goes once, in the order
//! it was made. A message another client expunged meanwhile, or a folder it deleted, leaves nothing stuck and nothing
//! moved twice.

#![allow(clippy::unwrap_used)]

mod stand_in;

use mm_mailbox::lists::Paging;
use serde_json::Value;
use stand_in::{ADA, Imap, World, note, world};

/// Ada's Inbox of four notes, her Archive, Trash and Plans empty, synced, and her folders read once.
fn synced() -> World {
    let imap = Imap::start();
    imap.add(ADA, "INBOX", (1..=4).map(|uid| (uid, note(uid))));
    for folder in ["Archive", "Trash", "Plans"] {
        imap.add(ADA, folder, []);
    }
    let w = world(imap, &[ADA], &[]);
    w.app.syncing.sync_once();
    w.app.folders.of_account(ADA).unwrap();
    w
}

fn id(uid: u32) -> String {
    format!("<note-{uid}@example.test>")
}

/// The UIDs Ada's Inbox lists.
fn listed(w: &World) -> Vec<i64> {
    let page = Paging { offset: 0, limit: 50, grouped: false };
    let answer = w.app.reading.lists.messages_in(Some(ADA), "INBOX", page).unwrap();
    let mut uids: Vec<i64> =
        answer["items"].as_array().unwrap().iter().map(|row| row["uid"].as_i64().unwrap()).collect();
    uids.sort_unstable();
    uids
}

fn status(answer: &Value) -> &str {
    answer["status"].as_str().unwrap_or_default()
}

/// The commands that changed mail on the server, as it heard them, in order.
fn changes_heard(w: &World, mark: usize) -> Vec<String> {
    let changing = ["UID STORE", "UID COPY", "UID MOVE", "UID EXPUNGE"];
    w.imap.since(mark, ADA).into_iter().filter(|c| changing.iter().any(|verb| c.starts_with(verb))).collect()
}

/// A while on, then every change that is due tried.
fn later(w: &World) {
    w.clock.shift(3600);
    w.app.commands.drain().unwrap();
}

#[test]
fn changes_made_while_the_server_is_away_go_once_in_order_when_it_is_back() {
    let w = synced();
    w.imap.away(true);
    let pk = |uid| w.pk(ADA, "INBOX", uid);
    let filing = &w.app.filing;
    assert_eq!(status(&filing.set_flags(pk(1), &[("seen", true)], Some("m-1")).unwrap()), "queued");
    assert_eq!(status(&filing.file("archive", pk(1), None, Some("m-2"), None).unwrap()), "queued");
    assert_eq!(status(&filing.set_flags(pk(2), &[("flagged", true)], Some("m-3")).unwrap()), "queued");
    assert_eq!(status(&filing.file("trash", pk(2), None, Some("m-4"), None).unwrap()), "queued");
    assert_eq!(status(&filing.move_to(pk(3), "Plans", None, Some("m-5")).unwrap()), "queued");
    assert_eq!(listed(&w), [4], "what was filed has left the list while it waits");
    assert_eq!(w.imap.ids_in(ADA, "INBOX").len(), 4, "and nothing reached the server");

    // Away still: each try fails, and backs off.
    later(&w);
    assert_eq!(w.imap.ids_in(ADA, "INBOX").len(), 4);
    assert_eq!(listed(&w), [4], "nothing given back while the server is only away");

    w.imap.away(false);
    let mark = w.imap.mark();
    later(&w);
    assert_eq!(w.imap.ids_in(ADA, "INBOX"), [id(4)]);
    assert_eq!(w.imap.ids_in(ADA, "Archive"), [id(1)]);
    assert_eq!(w.imap.ids_in(ADA, "Trash"), [id(2)]);
    assert_eq!(w.imap.ids_in(ADA, "Plans"), [id(3)]);
    assert!(w.imap.flags_of(ADA, "Archive", &id(1)).contains("\\Seen"), "marked read before it moved");
    assert!(w.imap.flags_of(ADA, "Trash", &id(2)).contains("\\Flagged"), "starred before it moved");
    let heard = changes_heard(&w, mark);
    let at =
        |start: &str| heard.iter().position(|c| c.starts_with(start)).unwrap_or_else(|| panic!("{start}: {heard:?}"));
    assert!(at("UID STORE 1 +FLAGS (\\Seen)") < at("UID COPY 1 "), "{heard:?}");
    assert!(at("UID STORE 2 +FLAGS (\\Flagged)") < at("UID COPY 2 "), "{heard:?}");
    assert!(at("UID COPY 1 ") < at("UID COPY 2 ") && at("UID COPY 2 ") < at("UID COPY 3 "), "{heard:?}");

    // Done: another while on asks the server nothing more.
    let mark = w.imap.mark();
    later(&w);
    assert!(changes_heard(&w, mark).is_empty(), "{:?}", changes_heard(&w, mark));
}

#[test]
fn a_message_another_client_expunged_while_its_move_waited_is_done_with_and_moved_nowhere() {
    let w = synced();
    w.imap.away(true);
    assert_eq!(
        status(&w.app.filing.file("archive", w.pk(ADA, "INBOX", 2), None, Some("m-1"), None).unwrap()),
        "queued"
    );
    w.imap.expunge(ADA, "INBOX", 2);
    w.imap.away(false);
    let mark = w.imap.mark();
    later(&w);
    assert!(w.imap.ids_in(ADA, "Archive").is_empty(), "nothing appeared in the Archive");
    assert_eq!(w.imap.ids_in(ADA, "INBOX"), [id(1), id(3), id(4)]);
    let mark_again = w.imap.mark();
    later(&w);
    assert!(changes_heard(&w, mark_again).is_empty(), "it isn't tried again: {:?}", changes_heard(&w, mark));
    assert_eq!(listed(&w), [1, 3, 4]);
}

#[test]
fn a_move_into_a_folder_another_client_deleted_gives_the_message_back() {
    let w = synced();
    w.imap.away(true);
    assert_eq!(status(&w.app.filing.move_to(w.pk(ADA, "INBOX", 3), "Plans", None, Some("m-1")).unwrap()), "queued");
    assert_eq!(listed(&w), [1, 2, 4]);
    w.imap.delete_folder(ADA, "Plans");
    w.imap.away(false);
    later(&w);
    assert_eq!(w.imap.ids_in(ADA, "INBOX"), [id(1), id(2), id(3), id(4)], "the message stayed where it was");
    assert_eq!(listed(&w), [1, 2, 3, 4], "and is listed there again");
    let mark = w.imap.mark();
    later(&w);
    assert!(changes_heard(&w, mark).is_empty(), "a refusal isn't tried again on its own");
}

#[test]
fn a_message_another_client_expunged_while_open_still_reads_and_filing_it_moves_nothing() {
    let w = synced();
    // Opened before: its body is kept, and it still reads.
    let pk = w.pk(ADA, "INBOX", 1);
    assert_eq!(w.app.reading.bodies.message_body(pk, false).unwrap()["ok"], true);
    w.imap.expunge(ADA, "INBOX", 1);
    assert_eq!(w.app.reading.bodies.message_body(pk, false).unwrap()["ok"], true, "what was kept still reads");
    // Filed now, it is done with: nothing moves, nothing waits.
    let filed = w.app.filing.file("archive", pk, None, Some("m-1"), None);
    assert!(w.imap.ids_in(ADA, "Archive").is_empty(), "{filed:?}");
    let mark = w.imap.mark();
    later(&w);
    assert!(changes_heard(&w, mark).is_empty());
}

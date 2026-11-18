//! Changes a person makes while their server is away, against an IMAP server on loopback that keeps what it is
//! asked: each waits in the queue, the lists show it made, and once the server is back each goes once, in the order
//! it was made. A message another client expunged meanwhile, or a folder it deleted, leaves nothing stuck and nothing
//! moved twice.

#![allow(clippy::unwrap_used)]

mod stand_in;

use mm_kernel::seams::Keychain;
use mm_mailbox::lists::Paging;
use serde_json::{Value, json};
use stand_in::{ADA, BEA, Imap, World, note, world};

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

/// A known bug: a folder another client deleted while the older-mail lane read its first scan is asked for at every
/// round of the lane, a login each time, for good; its list still shows the mail it held, saying nothing; and a message
/// of it fails to open as a fault. Once the server's folders say it is gone, it should be forgotten as a folder MailMind
/// deleted is (`FolderStore::forget_folder`). Fixing it fails this test until the marker goes.
#[test]
#[should_panic(expected = "the lane stopped asking for a folder the server no longer has")]
fn a_folder_another_client_deleted_is_no_longer_read() {
    let imap = Imap::start();
    imap.add(ADA, "INBOX", (1..=2).map(|uid| (uid, note(uid))));
    imap.add(ADA, "Plans", (1..=1200).map(|uid| (uid, note(uid))));
    let w = world(imap, &[ADA], &[]);
    w.app.syncing.sync_once();
    let page = Paging { offset: 0, limit: 50, grouped: false };
    assert_eq!(w.app.reading.lists.messages_in(Some(ADA), "Plans", page).unwrap()["total"], 500);
    w.imap.delete_folder(ADA, "Plans");
    w.app.folders.invalidate(None).unwrap();
    let rail = w.app.folders.of_account(ADA).unwrap();
    assert!(rail.folders.iter().all(|(folder, _)| folder.name != "Plans"), "the rail has heard it is gone");
    w.app.syncing.read_older().unwrap();
    let mark = w.imap.mark();
    w.app.syncing.read_older().unwrap();
    assert!(
        !w.imap.since(mark, ADA).iter().any(|c| c.contains("Plans")),
        "the lane stopped asking for a folder the server no longer has"
    );
}

/// Ada's and Bea's Inboxes of three notes each, Bea's password gone from the Keychain, and a sync: what each account's
/// health says of its last sync, and whether Bea's server was asked anything.
fn without_beas_password() -> (World, Value, Vec<String>) {
    let imap = Imap::start();
    imap.add(ADA, "INBOX", (1..=3).map(|uid| (uid, note(uid))));
    imap.add(BEA, "INBOX", (1..=3).map(|uid| (uid, note(uid))));
    let w = world(imap, &[ADA, BEA], &[]);
    assert!(w.keychain.delete(&format!("account:{BEA}")).unwrap());
    let mark = w.imap.mark();
    w.app.syncing.sync_once();
    w.app.syncing.sync_once();
    let heard = w.imap.since(mark, BEA);
    let health = w.app.health.answer().unwrap();
    (w, health, heard)
}

fn health_of<'h>(health: &'h Value, email: &str) -> &'h Value {
    health["accounts"].as_array().unwrap().iter().find(|row| row["account"] == email).unwrap()
}

#[test]
fn an_account_whose_password_the_keychain_lost_sends_no_login_and_holds_up_no_other() {
    let (w, health, heard) = without_beas_password();
    assert!(heard.is_empty(), "no login without a password: {heard:?}");
    assert_eq!(health_of(&health, ADA)["healthy"], true);
    assert!(w.stored(ADA, "INBOX", 3).kept, "Ada's mail came in");
    let bea = health_of(&health, BEA);
    assert_eq!((bea["healthy"].clone(), bea["last_error"].clone()), (Value::Bool(false), Value::from("NoCredential")));
    let page = Paging { offset: 0, limit: 50, grouped: false };
    let listed = w.app.reading.lists.messages_in(Some(BEA), "INBOX", page).unwrap();
    assert_eq!((listed["total"].clone(), listed["sync_failed"].clone()), (Value::from(0), Value::Bool(true)));
}

/// An account whose password the Keychain doesn't hold fails its sync as a kind of its own, so the Mac app's card says
/// the password is missing and offers Change Password, rather than a Retry that never helps; and a Test says so too,
/// not that a server refused a password none was sent.
#[test]
fn an_account_whose_password_the_keychain_lost_is_told_so() {
    let (w, health, _) = without_beas_password();
    assert_eq!(health_of(&health, BEA)["last_error_kind"], "NO_PASSWORD", "the trouble's kind says what is wrong");
    let mark = w.imap.mark();
    let tested = w.app.connections.test(json!({"email": BEA}).as_object().unwrap()).unwrap();
    assert_eq!((&tested["connect"], &tested["error"]), (&json!(false), &json!("no password")), "{tested}");
    assert!(tested["says"].as_str().unwrap().contains("Keychain doesn't hold one"), "{tested}");
    assert!(!w.imap.since(mark, BEA).iter().any(|c| c == "LOGIN"), "no login without a password");
}

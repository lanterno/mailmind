//! Changes a person makes while their server is away, against an IMAP server on loopback that keeps what it is
//! asked: each waits in the queue, the lists show it made, and once the server is back each goes once, in the order
//! it was made. A message another client expunged meanwhile, or a folder it deleted, leaves nothing stuck and nothing
//! moved twice.

#![allow(clippy::unwrap_used)]

mod stand_in;

use mm_kernel::errors::Problem;
use mm_kernel::seams::Keychain;
use mm_mailbox::bodies::FOLDER_GONE;
use mm_mailbox::ingest::SyncError;
use mm_mailbox::lists::Paging;
use mm_mailbox::session::{NO_SUCH_FOLDER, ServerError};
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

/// What Ada's Plans lists now: how many it holds.
fn plans_total(w: &World) -> Value {
    let page = Paging { offset: 0, limit: 50, grouped: false };
    w.app.reading.lists.messages_in(Some(ADA), "Plans", page).unwrap()["total"].clone()
}

/// Ada's Inbox of two notes and her Plans of 1200, synced, and Plans opened: its first scan stores the newest 500 and
/// leaves the rest to the older-mail lane.
fn plans_half_read() -> World {
    let imap = Imap::start();
    imap.add(ADA, "INBOX", (1..=2).map(|uid| (uid, note(uid))));
    imap.add(ADA, "Plans", (1..=1200).map(|uid| (uid, note(uid))));
    let w = world(imap, &[ADA], &[]);
    w.app.syncing.sync_once();
    assert_eq!(plans_total(&w), 500);
    w
}

/// Whether the lane's next round asks Ada's server anything of Plans.
fn lane_asks_for_plans(w: &World) -> bool {
    let mark = w.imap.mark();
    w.app.syncing.read_older().unwrap();
    w.imap.since(mark, ADA).iter().any(|c| c.contains("Plans"))
}

/// A folder another client deleted while the older-mail lane read its first scan: once the rail's LIST leaves it out,
/// it is forgotten, as a folder MailMind deleted is, and the lane asks for it no more.
#[test]
fn a_folder_another_client_deleted_is_no_longer_read() {
    let w = plans_half_read();
    w.imap.delete_folder(ADA, "Plans");
    w.app.folders.invalidate(None).unwrap();
    let rail = w.app.folders.of_account(ADA).unwrap();
    assert!(rail.folders.iter().all(|(folder, _)| folder.name != "Plans"), "the rail has heard it is gone");
    assert!(!lane_asks_for_plans(&w), "the lane stopped asking for a folder the server no longer has");
    assert_eq!(plans_total(&w), 0, "and its list holds nothing");
}

/// With no LIST asked meanwhile, the lane finds the folder gone itself: its EXAMINE is refused, and a LIST that works
/// leaves the folder out.
#[test]
fn the_older_mail_lane_forgets_a_folder_its_server_no_longer_lists() {
    let w = plans_half_read();
    w.imap.delete_folder(ADA, "Plans");
    let mark = w.imap.mark();
    w.app.syncing.read_older().unwrap();
    let heard = w.imap.since(mark, ADA);
    assert!(heard.iter().any(|c| c == "EXAMINE \"Plans\"") && heard.iter().any(|c| c.starts_with("LIST")), "{heard:?}");
    assert!(!lane_asks_for_plans(&w), "forgotten once");
    assert_eq!(plans_total(&w), 0);
    let rail = w.app.folders.of_account(ADA).unwrap();
    assert!(rail.folders.iter().all(|(folder, _)| folder.name != "Plans"));
}

/// A server whose NO says `[NONEXISTENT]` is taken at its word, with no LIST asked.
#[test]
fn a_folder_its_server_calls_nonexistent_is_forgotten_without_a_list() {
    let w = plans_half_read();
    w.imap.delete_folder(ADA, "Plans");
    w.imap.held().says_nonexistent = true;
    let mark = w.imap.mark();
    w.app.syncing.read_older().unwrap();
    let heard = w.imap.since(mark, ADA);
    assert!(!heard.iter().any(|c| c.starts_with("LIST")), "{heard:?}");
    assert!(!lane_asks_for_plans(&w));
    assert_eq!(plans_total(&w), 0);
}

/// A folder its server can't open for now, a server away, and a folder gone where LIST fails too: each is kept, its
/// mail listed still. Only a server that says the folder is gone forgets it.
#[test]
fn a_folder_is_never_forgotten_on_a_refusal_that_says_nothing_of_it_or_a_server_away() {
    let w = plans_half_read();
    w.imap.held().selects_refused = 1;
    assert!(lane_asks_for_plans(&w));
    assert_eq!(plans_total(&w), 500, "a NO for now, and LIST names it");

    w.imap.away(true);
    w.app.syncing.read_older().unwrap();
    w.app.folders.invalidate(None).unwrap();
    let rail = w.app.folders.of_account(ADA).unwrap();
    assert!(rail.folders.iter().any(|(folder, _)| folder.name == "Plans"), "the stored folders serve");
    w.imap.away(false);
    assert_eq!(plans_total(&w), 500, "a server away");

    w.imap.held().lists_refused = true;
    w.imap.delete_folder(ADA, "Plans");
    assert!(lane_asks_for_plans(&w));
    assert!(lane_asks_for_plans(&w), "asked again: nothing said it is gone");
    assert_eq!(plans_total(&w), 500, "a NO that says nothing, and a LIST that failed");
}

/// A message of a folder another client deleted, opened: the open says so in words, and the folder is forgotten, so
/// its list is empty and the rail leaves it out.
#[test]
fn a_message_opened_from_a_folder_another_client_deleted_says_so_and_leaves_the_lists() {
    let imap = Imap::start();
    imap.add(ADA, "INBOX", (1..=2).map(|uid| (uid, note(uid))));
    imap.add(ADA, "Plans", (1..=3).map(|uid| (uid, note(uid))));
    let w = world(imap, &[ADA], &[]);
    w.app.syncing.sync_once();
    assert_eq!(plans_total(&w), 3);
    assert!(!w.stored(ADA, "Plans", 2).fetched, "its body waits to be opened");
    let pk = w.pk(ADA, "Plans", 2);
    w.imap.delete_folder(ADA, "Plans");
    let opened = w.app.reading.bodies.message_body(pk, false);
    assert_eq!(opened, Err(Problem::not_found(FOLDER_GONE).into()));
    assert_eq!(plans_total(&w), 0);
    let rail = w.app.folders.of_account(ADA).unwrap();
    assert!(rail.folders.iter().all(|(folder, _)| folder.name != "Plans"));
    assert_eq!(w.app.reading.bodies.message_body(pk, false), Err(Problem::not_found("not found").into()));
}

/// Undo's read back into a folder another client deleted (`Ingest::ingest_messages`): its SELECT is refused and a LIST
/// leaves it out, so the folder is forgotten as a sync forgets it: its list is empty and the rail leaves it out.
#[test]
fn undos_read_back_into_a_folder_another_client_deleted_forgets_the_folder() {
    let w = plans_of_three();
    w.imap.delete_folder(ADA, "Plans");
    let read = w.app.ingest.ingest_messages(ADA, "Plans", &[id(2), id(3)]);
    assert_eq!(read, Err(SyncError::Server(ServerError::Refused(NO_SUCH_FOLDER))));
    assert_eq!(plans_total(&w), 0);
    let rail = w.app.folders.of_account(ADA).unwrap();
    assert!(rail.folders.iter().all(|(folder, _)| folder.name != "Plans"));
}

/// The same read back into a folder its server won't open for now, with LIST still naming it, keeps the folder and its
/// mail.
#[test]
fn undos_read_back_into_a_folder_its_server_wont_open_for_now_keeps_the_folder() {
    let w = plans_of_three();
    w.imap.held().selects_refused = 1;
    assert!(w.app.ingest.ingest_messages(ADA, "Plans", &[id(2)]).is_err());
    assert_eq!(plans_total(&w), 3);
}

/// Ada's Inbox of two notes and her Plans of three, synced.
fn plans_of_three() -> World {
    let imap = Imap::start();
    imap.add(ADA, "INBOX", (1..=2).map(|uid| (uid, note(uid))));
    imap.add(ADA, "Plans", (1..=3).map(|uid| (uid, note(uid))));
    let w = world(imap, &[ADA], &[]);
    w.app.syncing.sync_once();
    assert_eq!(plans_total(&w), 3);
    w
}

/// A move into a folder another client deleted, queued while the server was away: once the folder is found gone, the
/// move is given back, the tray saying its folder went, not that the server refused it; its message is listed where
/// it was, and nothing is tried.
#[test]
fn a_change_queued_for_a_folder_another_client_deleted_is_given_back_saying_its_folder_went() {
    let w = synced();
    assert_eq!(plans_total(&w), 0, "opened once, so its sync state is stored");
    w.imap.away(true);
    assert_eq!(status(&w.app.filing.move_to(w.pk(ADA, "INBOX", 3), "Plans", None, Some("m-1")).unwrap()), "queued");
    assert_eq!(listed(&w), [1, 2, 4]);
    w.imap.delete_folder(ADA, "Plans");
    w.imap.away(false);
    w.app.folders.invalidate(None).unwrap();
    let rail = w.app.folders.of_account(ADA).unwrap();
    assert!(rail.folders.iter().all(|(folder, _)| folder.name != "Plans"));
    assert_eq!(listed(&w), [1, 2, 3, 4], "the message is listed where it was");
    let tray = w.app.commands.list_open(50).unwrap();
    assert_eq!((&tray["items"][0]["state"], &tray["items"][0]["error"]), (&json!("error"), &json!("folder_gone")));
    assert_eq!((&tray["waiting"], &tray["stuck"]), (&json!(0), &json!(1)));
    let mark = w.imap.mark();
    later(&w);
    assert!(changes_heard(&w, mark).is_empty(), "nothing is tried");
    assert_eq!(w.imap.ids_in(ADA, "INBOX"), [id(1), id(2), id(3), id(4)]);
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

//! The body backfill on the Rust server (#5), against an IMAP server on loopback: a folder's dozen in one FETCH of
//! their openings, on the connection the account's jobs share; none older than twelve months; a message too large to
//! keep whole keeping its opening's text, its attachment fetched when it is opened; and an opening a sync read that held
//! its whole message kept as its body, so the backfill fetches only the Inbox's mail a sync read cut off.

#![allow(clippy::unwrap_used)]

mod stand_in;

use mm_mailbox::gates::{Priority, Wait, on_gate};
use mm_mailbox::session::MailServers;
use stand_in::{ADA, Imap, NOW, StoredBody, World, note, note_on, with_pdf, world};

const HOUR: i64 = 3600;
const DAY: i64 = 86_400;

/// Ada's `folder` as a sync of it left it: each message's headers stored, `(uid, seconds old)`, and no body.
fn stored_without_bodies(w: &World, folder: &str, aged: &[(u32, i64)]) {
    for (uid, seconds) in aged {
        w.write(&format!(
            "INSERT INTO messages (account, kind, folder, uid, subject, ts, message_key) \
             VALUES ('{ADA}', 'imap', '{folder}', {uid}, 'Note {uid}', {}, {})",
            NOW - seconds,
            1000 * i64::from(*uid) + i64::try_from(folder.len()).unwrap(),
        ));
    }
}

/// The UID set of each FETCH of openings Ada's connections were asked since `mark`.
fn openings_asked(w: &World, mark: usize) -> Vec<String> {
    w.imap
        .since(mark, ADA)
        .iter()
        .filter(|c| c.starts_with("UID FETCH") && c.ends_with("(UID RFC822.SIZE BODY.PEEK[]<0.65536>)"))
        .map(|c| c["UID FETCH ".len()..].split(' ').next().unwrap().to_owned())
        .collect()
}

/// Each FETCH of whole messages Ada's connections were asked since `mark`.
fn wholes_asked(w: &World, mark: usize) -> Vec<String> {
    w.imap.since(mark, ADA).into_iter().filter(|c| c.ends_with("(UID BODY.PEEK[])")).collect()
}

fn fetched(w: &World, folder: &str, uid: u32) -> bool {
    w.stored(ADA, folder, uid).fetched
}

#[test]
fn old_mail_comes_a_folders_dozen_a_fetch_on_the_connection_the_accounts_jobs_share() {
    let imap = Imap::start();
    imap.add(ADA, "INBOX", []);
    imap.add(ADA, "Archive", (1..=30).map(|uid| (uid, note(uid))));
    imap.add(ADA, "Sent", (1..=3).map(|uid| (uid, note(uid))));
    let w = world(imap, &[ADA], &[]);
    // The Archive's newest is 30, an hour old; Sent's are older than any of it.
    let archive: Vec<(u32, i64)> = (1..=30).map(|uid| (uid, (31 - i64::from(uid)) * HOUR)).collect();
    stored_without_bodies(&w, "Archive", &archive);
    stored_without_bodies(&w, "Sent", &[(1, 50 * HOUR), (2, 49 * HOUR), (3, 48 * HOUR)]);
    let mark = w.imap.mark();
    w.app.backfill.round().unwrap();
    let newest: Vec<String> = [(19..=30), (7..=18)]
        .into_iter()
        .map(|uids| uids.rev().map(|uid: u32| uid.to_string()).collect::<Vec<_>>().join(","))
        .collect();
    // About four dozen a round: the Archive's 24 newest in two FETCHes, then the rest of the round's 48.
    let sets = openings_asked(&w, mark);
    assert_eq!(sets[..2], newest, "{sets:?}");
    assert_eq!(sets.len(), 4, "{sets:?}");
    assert!((1..=30).all(|uid| fetched(&w, "Archive", uid)) && (1..=3).all(|uid| fetched(&w, "Sent", uid)));
    // Each opening held all of its message: none was asked for whole.
    assert!(wholes_asked(&w, mark).is_empty());
    // One login for the round, and the next job on Ada's gate has it too.
    assert_eq!(w.imap.sent(ADA, "LOGIN").len(), 1);
    on_gate(&*w.app.gates, ADA, Priority::Interactive, Wait::Ever, || {
        let mut s = w.app.kept.open(ADA, 60).unwrap();
        s.select("INBOX", true).unwrap();
        s.logout();
    })
    .unwrap();
    assert_eq!(w.imap.sent(ADA, "LOGIN").len(), 1);
    // Caught up: the next round asks nothing.
    let mark = w.imap.mark();
    w.app.backfill.round().unwrap();
    assert!(w.imap.since(mark, ADA).is_empty(), "{:?}", w.imap.since(mark, ADA));
}

#[test]
fn mail_older_than_twelve_months_waits_to_be_opened() {
    let imap = Imap::start();
    imap.add(ADA, "INBOX", []);
    let old = note_on(2, "Sun, 21 Sep 2025 09:00:00 +0000", "From long ago.");
    imap.add(ADA, "Archive", [(1, note(1)), (2, old)]);
    let w = world(imap, &[ADA], &[]);
    stored_without_bodies(&w, "Archive", &[(1, 10 * DAY), (2, 400 * DAY)]);
    let mark = w.imap.mark();
    w.app.backfill.round().unwrap();
    assert_eq!(openings_asked(&w, mark), ["1"]);
    assert!(fetched(&w, "Archive", 1));
    assert_eq!(w.stored(ADA, "Archive", 2), StoredBody { fetched: false, text: None, kept: false });
    let mark = w.imap.mark();
    w.app.backfill.round().unwrap();
    assert!(w.imap.since(mark, ADA).is_empty(), "nothing left within the twelve months");
    // Opened, it is fetched as any message is.
    let opened = w.app.reading.bodies.message_body(w.pk(ADA, "Archive", 2), false).unwrap();
    assert_eq!(opened["text"].as_str().unwrap().trim(), "From long ago.");
    assert!(fetched(&w, "Archive", 2));
}

#[test]
fn a_message_too_large_to_keep_whole_keeps_its_text_and_its_attachment_comes_when_it_is_opened() {
    let imap = Imap::start();
    imap.add(ADA, "INBOX", []);
    let (small, large) = (with_pdf(1, "The menu, attached.", 100), with_pdf(2, "The plans, attached.", 400));
    imap.add(ADA, "Archive", [(1, small), (2, large), (3, note(3))]);
    let w = world(imap, &[ADA], &[]);
    stored_without_bodies(&w, "Archive", &[(1, 3 * HOUR), (2, 2 * HOUR), (3, HOUR)]);
    let mark = w.imap.mark();
    w.app.backfill.round().unwrap();
    assert_eq!(openings_asked(&w, mark), ["3,2,1"]);
    // The one cut off at 64 KiB and under a quarter of a megabyte is fetched whole; the larger one isn't.
    assert_eq!(wholes_asked(&w, mark), ["UID FETCH 1 (UID BODY.PEEK[])"]);
    assert!(fetched(&w, "Archive", 1) && fetched(&w, "Archive", 3));
    let large = w.stored(ADA, "Archive", 2);
    assert_eq!((large.fetched, large.kept), (false, false));
    assert_eq!(large.text.unwrap().trim(), "The plans, attached.");
    // Its text found, the backfill asks for it no more.
    let mark = w.imap.mark();
    w.app.backfill.round().unwrap();
    assert!(w.imap.since(mark, ADA).is_empty(), "{:?}", w.imap.since(mark, ADA));
    // Opened, it is fetched whole, attachment and all.
    let opened = w.app.reading.bodies.message_body(w.pk(ADA, "Archive", 2), false).unwrap();
    assert_eq!(opened["attachments"].as_array().unwrap().len(), 1, "{}", opened["attachments"]);
    assert!(w.stored(ADA, "Archive", 2).fetched);
}

#[test]
fn a_sync_keeps_each_opening_that_held_its_whole_message_and_the_backfill_fetches_the_rest_whole() {
    let imap = Imap::start();
    imap.add(ADA, "INBOX", [(1, note(1)), (2, with_pdf(2, "The plans, attached.", 100))]);
    let w = world(imap, &[ADA], &[]);
    w.app.syncing.sync_once();
    // The note's opening held all of it: kept whole, its text for search.
    let note = w.stored(ADA, "INBOX", 1);
    assert_eq!((note.fetched, note.kept), (true, true));
    assert_eq!(note.text.unwrap().trim(), "Note 1 says hello.");
    // The plans' was cut off at 64 KiB: it waits for the backfill.
    assert_eq!(w.stored(ADA, "INBOX", 2), StoredBody { fetched: false, text: None, kept: false });
    let mark = w.imap.mark();
    w.app.backfill.round().unwrap();
    assert_eq!(openings_asked(&w, mark), ["2"]);
    assert_eq!(wholes_asked(&w, mark), ["UID FETCH 2 (UID BODY.PEEK[])"]);
    let plans = w.stored(ADA, "INBOX", 2);
    assert_eq!((plans.fetched, plans.kept), (true, true));
    // Opened, the note asks its server nothing.
    let mark = w.imap.mark();
    let opened = w.app.reading.bodies.message_body(w.pk(ADA, "INBOX", 1), false).unwrap();
    assert_eq!(opened["text"].as_str().unwrap().trim(), "Note 1 says hello.");
    assert!(w.imap.since(mark, ADA).iter().all(|c| !c.starts_with("UID FETCH")), "{:?}", w.imap.since(mark, ADA));
}

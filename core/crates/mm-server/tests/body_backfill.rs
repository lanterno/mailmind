//! The body backfill on the Rust server (#5), against an IMAP server on loopback: the text of the openings a sync read
//! is kept, so the backfill never fetches that mail again.

#![allow(clippy::unwrap_used)]

mod stand_in;

use stand_in::{ADA, Imap, StoredBody, note, with_pdf, world};

#[test]
fn the_text_of_each_opening_a_sync_read_is_kept_and_the_backfill_fetches_none_of_that_mail() {
    let imap = Imap::start();
    imap.add(ADA, "INBOX", [(1, note(1)), (2, with_pdf(2, "The plans, attached.", 100))]);
    let w = world(imap, &[ADA], &[]);
    w.app.syncing.sync_once();
    let text = |uid: u32| {
        let StoredBody { fetched, text, kept } = w.stored(ADA, "INBOX", uid);
        (fetched, text.unwrap().trim().to_owned(), kept)
    };
    assert_eq!(text(1), (false, "Note 1 says hello.".to_owned(), false));
    // Only its opening was read: its attachment waits to be opened.
    assert_eq!(text(2), (false, "The plans, attached.".to_owned(), false));
    let mark = w.imap.mark();
    w.app.backfill.round().unwrap();
    assert!(w.imap.since(mark, ADA).iter().all(|c| !c.starts_with("UID FETCH")), "{:?}", w.imap.since(mark, ADA));
}

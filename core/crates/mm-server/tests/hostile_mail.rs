//! Mail as large as a provider lets it be, through the whole app against an IMAP server on loopback: a message whose
//! attachment is 25 MB opens, and its file comes whole.

#![allow(clippy::unwrap_used)]

mod stand_in;

use stand_in::{ADA, world};

#[test]
fn a_message_with_a_25_mb_attachment_opens_and_its_file_comes_whole() {
    // 25 MB attached, about 34 MB as the server sends it: more than one IMAP response line may run to.
    let imap = stand_in::Imap::start();
    imap.add(ADA, "INBOX", [(1, stand_in::with_pdf(1, "The plans, attached.", 33 << 10))]);
    let w = world(imap, &[ADA], &[]);
    w.app.syncing.sync_once();
    let pk = w.pk(ADA, "INBOX", 1);
    let opened = w.app.reading.bodies.message_body(pk, false).unwrap();
    assert_eq!(opened["ok"], true, "{}", opened["error"]);
    assert_eq!(opened["text"].as_str().map(str::trim), Some("The plans, attached."));
    let files = opened["attachments"].as_array().unwrap();
    assert_eq!(files.len(), 1, "{files:?}");
    let part = files[0]["part_id"].as_str().unwrap().to_owned();
    let file = w.app.reading.bodies.attachment(pk, &part).unwrap().unwrap();
    assert!(file.data.len() >= 24 << 20, "the file came whole: {} bytes", file.data.len());
    assert!(w.stored(ADA, "INBOX", 1).fetched, "and is kept, so it isn't fetched again");
}

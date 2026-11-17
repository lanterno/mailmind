//! A sync's step as the store keeps it: its messages and where the sync got to in one transaction, so a first scan
//! that stops goes on from the last step stored; a step that holds the folder to a place a fresh read has left stores
//! nothing; and what a first scan has begun counts for opening the folder.

#![allow(clippy::unwrap_used)]

use std::path::Path;
use std::sync::Arc;

use mm_content::mime;
use mm_kernel::seams::{Clock, Moment, Outside, Tripped};
use mm_mailbox::fetch_ports::KeptBody;
use mm_mailbox::ports::StoredLists;
use mm_mailbox::sync_plan::Scan;
use mm_mailbox::sync_ports::{Incoming, Place, SyncAccount, SyncStore, Verdict};
use mm_store::{Database, DbKey};

const ADA: &str = "ada@example.test";
const WHEN: &str = "2026-11-02T12:00:00+00:00";

#[derive(Debug)]
struct Open;

impl Outside for Open {
    fn open(&self, _path: &Path) -> Result<(), Tripped> {
        Ok(())
    }

    fn reach(&self, _host: &str) -> Result<(), Tripped> {
        Ok(())
    }

    fn run(&self, _program: &str) -> Result<(), Tripped> {
        Ok(())
    }
}

#[derive(Debug)]
struct Frozen;

impl Clock for Frozen {
    fn now(&self) -> Moment {
        Moment::from_micros(1_793_620_800_000_000)
    }
}

/// A database the core makes, at the newest schema.
fn database(dir: &Path) -> Database {
    let database = Database::new(dir.join("mailmind.db"), DbKey::from_bytes([0x5e; 32]), Arc::new(Open));
    database.ready(&Frozen).unwrap();
    database
}

fn ada() -> SyncAccount {
    SyncAccount { email: ADA.to_owned(), kind: "imap".to_owned(), gmail_threads: false }
}

/// Message `uid` as a sync stores it.
fn message(uid: u32) -> Incoming {
    let raw = format!(
        "From: Lin <lin@example.test>\r\nTo: Ada <{ADA}>\r\nSubject: Note {uid}\r\n\
         Date: Mon, 02 Nov 2026 09:00:00 +0000\r\nMessage-ID: <note-{uid}@example.test>\r\n\r\n"
    );
    Incoming {
        uid,
        flags: [0; 5],
        fields: mime::fields(raw.as_bytes()),
        gmail_thread: None,
        gmail_labels: None,
        verdict: Verdict {
            bucket: "personal".to_owned(),
            confidence: "high".to_owned(),
            reason: String::new(),
            is_junk: false,
        },
        snippet: format!("Note {uid}"),
        body: None,
    }
}

fn messages(from: u32, to: u32) -> Vec<Incoming> {
    (from..=to).map(message).collect()
}

fn place(watermark: u32, leaves: Option<Scan>, holds: Option<Scan>) -> Place {
    Place { watermark, leaves, holds, when: WHEN.to_owned() }
}

/// The UIDs stored of Ada's INBOX, in order.
fn stored(database: &Database) -> Vec<u32> {
    database
        .read(|c| {
            let mut rows = c.prepare("SELECT uid FROM messages WHERE account=? AND folder='INBOX' ORDER BY uid")?;
            rows.query_map([ADA], |r| r.get(0))?.collect()
        })
        .unwrap()
}

#[test]
fn a_step_is_stored_with_where_it_leaves_the_folder() {
    let dir = tempfile::tempdir().unwrap();
    let database = database(dir.path());
    let pks = database.store_step(&ada(), "INBOX", &messages(11, 13), &place(13, Some(Scan::Down(11)), None)).unwrap();
    assert_eq!(pks.map(|p| p.len()), Some(3));
    assert_eq!(stored(&database), [11, 12, 13]);
    let state = database.folder_state(ADA, "INBOX").unwrap();
    assert_eq!((state.watermark, state.scan), (13, Scan::Down(11)));
    assert_eq!(database.reading_older().unwrap(), [(ADA.to_owned(), "INBOX".to_owned(), 11)]);
    // The next step down, held to where this one left it, reads the folder whole; its watermark stays.
    let pks =
        database.store_step(&ada(), "INBOX", &messages(1, 10), &place(0, Some(Scan::Whole), Some(Scan::Down(11))));
    assert_eq!(pks.unwrap().map(|p| p.len()), Some(10));
    let state = database.folder_state(ADA, "INBOX").unwrap();
    assert_eq!((state.watermark, state.scan), (13, Scan::Whole));
    assert!(database.reading_older().unwrap().is_empty());
}

#[test]
fn a_step_held_to_a_place_the_folder_has_left_stores_nothing() {
    let dir = tempfile::tempdir().unwrap();
    let database = database(dir.path());
    database.store_step(&ada(), "INBOX", &messages(11, 13), &place(13, Some(Scan::Down(11)), None)).unwrap();
    // The server renumbered the folder meanwhile, and a sync read it afresh.
    database.reset_folder(ADA, "INBOX", 1_793_620_800).unwrap();
    let late =
        database.store_step(&ada(), "INBOX", &messages(1, 10), &place(0, Some(Scan::Whole), Some(Scan::Down(11))));
    assert_eq!(late.unwrap(), None);
    assert!(stored(&database).is_empty());
    assert_eq!(database.folder_state(ADA, "INBOX").unwrap().scan, Scan::Never);
}

#[test]
fn a_step_that_fails_part_way_leaves_nothing_of_it_and_the_folder_where_it_was() {
    let dir = tempfile::tempdir().unwrap();
    let database = database(dir.path());
    database.store_step(&ada(), "INBOX", &messages(21, 30), &place(30, Some(Scan::Down(21)), None)).unwrap();
    // A message the store can't take: its recipients' table is gone.
    database.read(|c| c.execute_batch("DROP TABLE message_recipients")).unwrap();
    let failed =
        database.store_step(&ada(), "INBOX", &messages(11, 20), &place(0, Some(Scan::Down(11)), Some(Scan::Down(21))));
    assert!(failed.is_err());
    assert_eq!(stored(&database), (21..=30).collect::<Vec<u32>>());
    assert_eq!(database.folder_state(ADA, "INBOX").unwrap().scan, Scan::Down(21));
}

#[test]
fn a_folder_opens_as_one_read_before_once_its_first_scan_has_begun() {
    let dir = tempfile::tempdir().unwrap();
    let database = database(dir.path());
    assert!(!database.scan_begun(ADA, "INBOX").unwrap());
    database.store_step(&ada(), "Archive", &messages(1, 2), &place(2, Some(Scan::Cut), None)).unwrap();
    assert!(!database.scan_begun(ADA, "Archive").unwrap(), "warming read only its newest");
    database.store_step(&ada(), "INBOX", &messages(11, 13), &place(13, Some(Scan::Down(11)), None)).unwrap();
    assert!(database.scan_begun(ADA, "INBOX").unwrap());
    database.store_step(&ada(), "Archive", &[], &place(0, Some(Scan::Whole), None)).unwrap();
    assert!(database.scan_begun(ADA, "Archive").unwrap());
}

/// A message's UID, its text, and whether its whole body is kept.
type Text = (u32, Option<String>, bool);

/// Each of Ada's Inbox messages' text, whether its whole body is kept, and the messages search finds for `word`.
fn texts(database: &Database, word: &str) -> (Vec<Text>, Vec<u32>) {
    database
        .read(|c| {
            let mut rows = c.prepare(
                "SELECT uid, body_text, body_fetched AND EXISTS(SELECT 1 FROM bodies b WHERE b.message_pk=m.pk) \
                 FROM messages m WHERE account=? AND folder='INBOX' ORDER BY uid",
            )?;
            let texts = rows.query_map([ADA], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?.collect::<Result<_, _>>()?;
            let mut found = c.prepare(
                "SELECT m.uid FROM msg_fts JOIN messages m ON m.pk=msg_fts.rowid WHERE msg_fts MATCH ? ORDER BY m.uid",
            )?;
            let found = found.query_map([word], |r| r.get(0))?.collect::<Result<_, _>>()?;
            Ok((texts, found))
        })
        .unwrap()
}

#[test]
fn a_body_a_sync_read_whole_is_kept_with_its_row_and_a_later_sync_that_read_none_leaves_it() {
    let dir = tempfile::tempdir().unwrap();
    let database = database(dir.path());
    let mut read = message(11);
    let raw = format!(
        "From: Lin <lin@example.test>\r\nTo: Ada <{ADA}>\r\nSubject: Note 11\r\n\r\nA log cabin by the lake\r\n"
    );
    read.body = Some(KeptBody::of(raw.into_bytes(), WHEN.to_owned()));
    database.store_step(&ada(), "INBOX", &[read, message(12)], &place(12, None, None)).unwrap();
    let cabin = Some("A log cabin by the lake\r\n".to_owned());
    assert_eq!(texts(&database, "cabin"), (vec![(11, cabin.clone(), true), (12, None, false)], vec![11]));
    // Synced again with no body read, it keeps the one it has.
    database.store_step(&ada(), "INBOX", &[message(11), message(12)], &place(12, None, None)).unwrap();
    assert_eq!(texts(&database, "cabin"), (vec![(11, cabin, true), (12, None, false)], vec![11]));
}

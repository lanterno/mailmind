//! What the calendar reads of the database, as the Python app read it (mailmind.calendar.adapters.sqlite): the
//! calendar parts of stored mail, newest first, and only of mail whose body is stored, that came in since then and
//! isn't on its way out; the outbox's keys under a prefix; and the zone a TZID names among the system's zone files.

#![allow(clippy::unwrap_used)]

use std::path::Path;
use std::sync::{Arc, Mutex};

use mm_calendar::ports::{StoredInvites, ZoneNames};
use mm_calendar::publish::EventMessages;
use mm_kernel::seams::{Outside, Tripped};
use mm_store::calendar::SystemZones;
use mm_store::{Database, DbKey, Store};

/// Lets everything through, and keeps the paths it was asked to open.
#[derive(Debug, Default)]
struct Open {
    opened: Mutex<Vec<String>>,
}

impl Outside for Open {
    fn open(&self, path: &Path) -> Result<(), Tripped> {
        self.opened.lock().unwrap().push(path.to_string_lossy().into_owned());
        Ok(())
    }

    fn reach(&self, _host: &str) -> Result<(), Tripped> {
        Ok(())
    }

    fn run(&self, _program: &str) -> Result<(), Tripped> {
        Ok(())
    }
}

const TABLES: &str = "
CREATE TABLE messages(pk INTEGER PRIMARY KEY, message_key INTEGER, account TEXT, folder TEXT, uid INTEGER,
  thread_id TEXT, ts INTEGER, body_fetched INTEGER DEFAULT 0, pending_removal INTEGER, message_id TEXT, refs TEXT,
  in_reply_to TEXT);
CREATE TABLE bodies(message_pk INTEGER PRIMARY KEY, raw BLOB, fetched_at TEXT);
CREATE TABLE attachments(id INTEGER PRIMARY KEY AUTOINCREMENT, message_pk INTEGER, part_id TEXT, filename TEXT,
  content_type TEXT, size INTEGER, sha256 TEXT, is_inline INTEGER DEFAULT 0, content_id TEXT);
CREATE TABLE outbox(id INTEGER PRIMARY KEY AUTOINCREMENT, idempotency_key TEXT NOT NULL UNIQUE);
";

/// A message holding `ics` as its second part.
fn raw(ics: &str) -> Vec<u8> {
    [
        "From: ren@example.test",
        "To: ada@example.test",
        "Subject: Invitation",
        "MIME-Version: 1.0",
        "Content-Type: multipart/mixed; boundary=\"b\"",
        "",
        "--b",
        "Content-Type: text/plain; charset=utf-8",
        "",
        "You're invited.",
        "--b",
        "Content-Type: text/calendar; charset=utf-8",
        "Content-Disposition: attachment; filename=\"invite.ics\"",
        "",
        ics,
        "--b--",
        "",
    ]
    .join("\r\n")
    .into_bytes()
}

fn database() -> (tempfile::TempDir, Arc<Database>) {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("mailmind.db");
    let key = DbKey::from_bytes([0x5e; 32]);
    Store::open(&path, &key).unwrap();
    let database = Arc::new(Database::new(path, key, Arc::new(Open::default())));
    database.write(|c| c.execute_batch(TABLES)).unwrap();
    (dir, database)
}

/// Message `pk`, in `folder`, which came in at `ts`, with `ics` stored as its body's second part, and the part
/// recorded as `content_type` and `filename`.
struct Row<'a> {
    pk: i64,
    folder: &'a str,
    ts: i64,
    fetched: bool,
    removing: bool,
    content_type: &'a str,
    filename: &'a str,
    part_id: &'a str,
}

fn row(pk: i64, ts: i64) -> Row<'static> {
    Row {
        pk,
        folder: "INBOX",
        ts,
        fetched: true,
        removing: false,
        content_type: "text/calendar",
        filename: "invite.ics",
        part_id: "2",
    }
}

fn add(database: &Database, row: &Row<'_>) {
    database
        .write(|c| {
            c.execute(
                "INSERT INTO messages(pk, message_key, account, folder, uid, thread_id, ts, body_fetched, pending_removal)
                 VALUES (?1, ?1 + 100, 'ada@example.test', ?2, ?1, 't' || ?1, ?3, ?4, ?5)",
                rusqlite::params![row.pk, row.folder, row.ts, i64::from(row.fetched), row.removing.then_some(1)],
            )?;
            c.execute(
                "INSERT INTO bodies(message_pk, raw, fetched_at) VALUES (?, ?, '2026-11-02T12:00:00')",
                rusqlite::params![row.pk, raw(&format!("BEGIN:VCALENDAR\r\nUID:m{}\r\nEND:VCALENDAR", row.pk))],
            )?;
            c.execute(
                "INSERT INTO attachments(message_pk, part_id, filename, content_type, size) VALUES (?, ?, ?, ?, 10)",
                rusqlite::params![row.pk, row.part_id, row.filename, row.content_type],
            )?;
            Ok(())
        })
        .unwrap();
}

fn pks(database: &Database, since: i64, skipped: &dyn Fn(&str) -> bool) -> Vec<i64> {
    database.invite_parts(since, skipped).unwrap().into_iter().map(|(row, _)| row.pk).collect()
}

#[test]
fn every_calendar_part_of_stored_mail_comes_newest_first_with_its_text() {
    let (_dir, database) = database();
    add(&database, &row(1, 100));
    add(&database, &row(2, 300));
    add(&database, &row(3, 300));
    add(&database, &Row { content_type: "application/octet-stream", filename: "INVITE.ICS", ..row(4, 200) });
    let found = database.invite_parts(0, &|_| false).unwrap();
    let said: Vec<String> =
        found.iter().map(|(r, text)| format!("{} {:?} {:?} {} {text:?}", r.pk, r.key, r.uid, r.folder)).collect();
    assert_eq!(
        said,
        [
            r#"3 Some(103) Some(3) INBOX "BEGIN:VCALENDAR\r\nUID:m3\r\nEND:VCALENDAR""#,
            r#"2 Some(102) Some(2) INBOX "BEGIN:VCALENDAR\r\nUID:m2\r\nEND:VCALENDAR""#,
            r#"4 Some(104) Some(4) INBOX "BEGIN:VCALENDAR\r\nUID:m4\r\nEND:VCALENDAR""#,
            r#"1 Some(101) Some(1) INBOX "BEGIN:VCALENDAR\r\nUID:m1\r\nEND:VCALENDAR""#,
        ]
    );
    assert_eq!((found[0].0.account.as_str(), found[0].0.thread_id.as_deref()), ("ada@example.test", Some("t3")));
}

#[test]
fn unfetched_old_leaving_and_skipped_mail_and_other_parts_are_not_read() {
    let (_dir, database) = database();
    add(&database, &row(1, 500));
    add(&database, &Row { fetched: false, ..row(2, 500) });
    add(&database, &row(3, 99));
    add(&database, &Row { removing: true, ..row(4, 500) });
    add(&database, &Row { folder: "Trash", ..row(5, 500) });
    add(&database, &Row { content_type: "text/plain", filename: "notes.txt", ..row(6, 500) });
    // A part the body hasn't, and an id that is none.
    add(&database, &Row { part_id: "9", ..row(7, 500) });
    add(&database, &Row { part_id: "x", ..row(8, 500) });
    assert_eq!(pks(&database, 100, &|folder| folder == "Trash"), [1]);
}

#[test]
fn the_outboxs_keys_under_a_prefix_come_newest_first() {
    let (_dir, database) = database();
    database
        .write(|c| {
            c.execute_batch(
                "INSERT INTO outbox(idempotency_key) VALUES ('rsvp:ada@example.test:u1:ACCEPTED:0'),
                   ('rsvp:ada@example.test:u1x:DECLINED:0'), ('reply:1'), ('rsvp:ada@example.test:u1:DECLINED:1'),
                   ('rsvp:ada@example.test:%:TENTATIVE:0');",
            )
        })
        .unwrap();
    assert_eq!(
        database.keys_sent("rsvp:ada@example.test:u1:").unwrap(),
        ["rsvp:ada@example.test:u1:DECLINED:1", "rsvp:ada@example.test:u1:ACCEPTED:0"]
    );
    // A prefix is no pattern.
    assert_eq!(database.keys_sent("rsvp:ada@example.test:%:").unwrap(), ["rsvp:ada@example.test:%:TENTATIVE:0"]);
    assert!(database.keys_sent("rsvp:ada@example.test:u2:").unwrap().is_empty());
}

#[test]
fn a_message_is_its_pk_key_and_account() {
    let (_dir, database) = database();
    add(&database, &row(7, 100));
    let message = EventMessages::message(&*database, 7).unwrap().unwrap();
    assert_eq!((message.pk, message.key, message.account.as_str()), (7, Some(107), "ada@example.test"));
    assert!(EventMessages::message(&*database, 8).unwrap().is_none());
}

#[test]
fn an_answer_is_threaded_by_the_messages_id_references_and_in_reply_to() {
    let (_dir, database) = database();
    add(&database, &row(7, 100));
    add(&database, &row(8, 100));
    database
        .write(|c| {
            c.execute(
                "UPDATE messages SET message_id='<i@example.test>', refs='<a@example.test> <b@example.test>',
                   in_reply_to='<b@example.test>' WHERE pk=7",
                [],
            )
        })
        .unwrap();
    let threading = |pk| database.read(|c| mm_store::calendar::threading(c, pk)).unwrap();
    assert_eq!(
        threading(7),
        Some([
            Some("<i@example.test>".to_owned()),
            Some("<a@example.test> <b@example.test>".to_owned()),
            Some("<b@example.test>".to_owned())
        ])
    );
    assert_eq!(threading(8), Some([None, None, None]));
    assert_eq!(threading(9), None);
}

#[test]
fn a_tzid_is_the_system_zone_of_that_name_and_nothing_outside_them() {
    let open = Arc::new(Open::default());
    let zones = SystemZones::new(open.clone());
    // 2026-07-01 and 2026-12-01, at noon UTC.
    let (summer, winter) = (1_782_907_200, 1_796_126_400);
    let berlin = zones.zone("Europe/Berlin").unwrap();
    assert_eq!((berlin.offset_at(summer), berlin.offset_at(winter)), (7200, 3600));
    for name in
        ["", "/etc/passwd", "../../etc/passwd", "Europe/../../etc/passwd", "Europe//Berlin", "Nowhere/Land", "a\0b"]
    {
        assert!(zones.zone(name).is_none(), "{name:?}");
    }
    assert!(open.opened.lock().unwrap().iter().all(|p| p.starts_with("/usr/share/zoneinfo/")));
    // A zone read once is kept.
    let opened = open.opened.lock().unwrap().len();
    zones.zone("Europe/Berlin").unwrap();
    assert_eq!(open.opened.lock().unwrap().len(), opened);
}

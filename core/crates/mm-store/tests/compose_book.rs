//! The outbox, the drafts and their journal as the store keeps them: a send submitted twice is one row, a claim is
//! had once, a try that fails backs off or gives up, a crash's sends are sorted out at start, and a draft's saves come
//! due and settle as the Python app's tables have them.

#![allow(clippy::unwrap_used)]

use std::path::Path;
use std::sync::Arc;

use mm_compose::ports::Tracked;
use mm_compose::sending_ports::{self, DraftRow, Mark, NewSend, Record, Save};
use mm_kernel::errors::Fault;
use mm_kernel::seams::{Outside, Tripped};
use mm_store::{Database, DbKey, Store};

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

/// The three tables, as the newest schema golden has them.
const TABLES: &str = "
CREATE TABLE outbox (id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE, message_id TEXT NOT NULL, envelope TEXT NOT NULL, recipients TEXT NOT NULL,
  subject TEXT, raw BLOB NOT NULL, state TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt_at INTEGER NOT NULL DEFAULT 0, delivered INTEGER NOT NULL DEFAULT 0,
  sent_appended INTEGER NOT NULL DEFAULT 0, answered_set INTEGER NOT NULL DEFAULT 0,
  followup_recorded INTEGER NOT NULL DEFAULT 0, last_error TEXT, draft_id INTEGER,
  draft_deleted INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, sent_at TEXT, orig_account TEXT,
  orig_message_id TEXT, orig_thread_id TEXT, wire_begun INTEGER NOT NULL DEFAULT 0,
  wait_kind TEXT NOT NULL DEFAULT 'grace', original_key INTEGER);
CREATE TABLE drafts (id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL, folder TEXT, uid INTEGER,
  message_id TEXT, uidvalidity INTEGER, mode TEXT, subject TEXT, created_at TEXT, updated_at TEXT, own_text TEXT,
  tail_text TEXT, tail_html TEXT, bcc TEXT, raw_sha TEXT, idempotency_key TEXT, version INTEGER NOT NULL DEFAULT 0,
  saved_by TEXT, pushed_at INTEGER);
CREATE TABLE draft_journal(draft_id INTEGER PRIMARY KEY REFERENCES drafts(id) ON DELETE CASCADE,
  version INTEGER NOT NULL, account TEXT NOT NULL, discard INTEGER NOT NULL DEFAULT 0, message_id TEXT, raw BLOB,
  raw_sha TEXT, own_text TEXT, tail_text TEXT, tail_html TEXT, bcc TEXT, tried TEXT, saved_at INTEGER NOT NULL,
  due_at INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0);
";

fn database(dir: &Path) -> Database {
    let key = DbKey::from_bytes([0x5e; 32]);
    let path = dir.join("mailmind.db");
    Store::open(&path, &key).unwrap().create_list_tables().unwrap();
    let database = Database::new(path, key, Arc::new(Open));
    database.read(|c| c.execute_batch(TABLES)).unwrap();
    database
}

fn send(key: &str, due: i64) -> NewSend {
    NewSend {
        account: "ada@example.test".to_owned(),
        idempotency_key: key.to_owned(),
        message_id: format!("<{key}@example.test>"),
        envelope: r#"["lin@example.test"]"#.to_owned(),
        recipients: r#"{"to": ["lin@example.test"], "cc": [], "bcc": []}"#.to_owned(),
        subject: "Linen".to_owned(),
        raw: b"Subject: Linen\r\n\r\nHi\r\n".to_vec(),
        next_attempt_at: due,
        wait_kind: "grace",
        created_at: "2026-11-13T12:00:00+00:00".to_owned(),
        ..NewSend::default()
    }
}

#[test]
fn a_send_submitted_twice_is_one_row_and_a_claim_is_had_once() {
    let dir = tempfile::tempdir().unwrap();
    let store = database(dir.path());
    let first = sending_ports::write(&store, |book| book.outbox_insert(&send("k-1", 110))).unwrap();
    let again = sending_ports::write(&store, |book| book.outbox_insert(&send("k-1", 999))).unwrap();
    assert_eq!((first.1, again), (true, (first.0, false)));
    let id = first.0.unwrap();
    assert!(sending_ports::read(&store, |book| book.outbox_due(109)).unwrap().is_empty());
    assert_eq!(
        sending_ports::read(&store, |book| book.outbox_due(110)).unwrap(),
        [(id, "ada@example.test".to_owned())]
    );
    let won = sending_ports::write(&store, |book| book.outbox_claim(id)).unwrap().unwrap();
    assert_eq!((won.state.as_str(), won.raw.as_slice()), ("sending", b"Subject: Linen\r\n\r\nHi\r\n".as_slice()));
    assert_eq!(sending_ports::write(&store, |book| book.outbox_claim(id)).unwrap(), None);
    // A claimed send is too late for Undo Send.
    assert!(!sending_ports::write(&store, |book| book.outbox_cancel_queued(id)).unwrap());
}

#[test]
fn a_failed_try_backs_off_gives_up_and_a_person_retries_it() {
    let dir = tempfile::tempdir().unwrap();
    let store = database(dir.path());
    let id = sending_ports::write(&store, |book| book.outbox_insert(&send("k-1", 0))).unwrap().0.unwrap();
    sending_ports::write(&store, |book| {
        book.outbox_claim(id)?;
        book.outbox_set(id, &[Mark::WireBegun(true), Mark::WaitKind("backoff"), Mark::WireBegun(false)])?;
        book.outbox_retry_later(id, 1, false, 140, "timeout")
    })
    .unwrap();
    let row = sending_ports::read(&store, |book| book.outbox_get(id)).unwrap().unwrap();
    assert_eq!(
        (row.state.as_str(), row.attempts, row.next_attempt_at, row.last_error.as_deref(), row.wire_begun),
        ("queued", 1, 140, Some("timeout"), false)
    );
    assert_eq!(row.wait_kind, "backoff");
    assert_eq!(sending_ports::read(&store, |book| book.outbox_next_due(100)).unwrap(), Some(140));
    sending_ports::write(&store, |book| book.outbox_give_up(id, 2, "auth failed")).unwrap();
    let row = sending_ports::read(&store, |book| book.outbox_get(id)).unwrap().unwrap();
    assert_eq!((row.state.as_str(), row.attempts, row.next_attempt_at), ("error", 2, 0));
    assert!(sending_ports::write(&store, |book| book.outbox_requeue(id)).unwrap());
    assert!(!sending_ports::write(&store, |book| book.outbox_requeue(id)).unwrap(), "only a send in error");
    let row = sending_ports::read(&store, |book| book.outbox_get(id)).unwrap().unwrap();
    assert_eq!((row.state.as_str(), row.attempts, row.last_error), ("queued", 0, None));
    assert!(sending_ports::write(&store, |book| book.outbox_cancel_queued(id)).unwrap());
}

#[test]
fn at_start_a_send_that_may_have_gone_out_waits_for_a_person() {
    let dir = tempfile::tempdir().unwrap();
    let store = database(dir.path());
    let ids: Vec<i64> = ["k-1", "k-2", "k-3"]
        .iter()
        .map(|k| sending_ports::write(&store, |book| book.outbox_insert(&send(k, 0))).unwrap().0.unwrap())
        .collect();
    sending_ports::write(&store, |book| {
        for id in &ids {
            book.outbox_claim(*id)?;
        }
        book.outbox_set(ids[0], &[Mark::WireBegun(true)])?;
        book.outbox_set(ids[1], &[Mark::WireBegun(true)])?;
        book.outbox_delivered(ids[1])?;
        book.outbox_reset_stuck("unsure")
    })
    .unwrap();
    let states: Vec<(String, Option<String>)> = ids
        .iter()
        .map(|id| {
            let row = sending_ports::read(&store, |book| book.outbox_get(*id)).unwrap().unwrap();
            (row.state, row.last_error)
        })
        .collect();
    assert_eq!(
        states,
        [("error".to_owned(), Some("unsure".to_owned())), ("queued".to_owned(), None), ("queued".to_owned(), None)]
    );
    sending_ports::write(&store, |book| book.outbox_finish(ids[1], "2026-11-13T12:01:00+00:00")).unwrap();
    let row = sending_ports::read(&store, |book| book.outbox_get(ids[1])).unwrap().unwrap();
    assert_eq!((row.state.as_str(), row.sent_at.as_deref()), ("sent", Some("2026-11-13T12:01:00+00:00")));
}

#[test]
fn an_invites_answers_are_found_by_their_keys_prefix_in_characters() {
    let dir = tempfile::tempdir().unwrap();
    let store = database(dir.path());
    for key in ["rsvp:\u{e9}:1", "rsvp:\u{e9}:2", "rsvp:e:3"] {
        sending_ports::write(&store, |book| book.outbox_insert(&send(key, 0))).unwrap();
    }
    let keys = sending_ports::read(&store, |book| book.outbox_keys("rsvp:\u{e9}:")).unwrap();
    assert_eq!(keys, ["rsvp:\u{e9}:2", "rsvp:\u{e9}:1"]);
}

fn tracked(subject: &str) -> Tracked {
    Tracked {
        account: "ada@example.test".to_owned(),
        folder: None,
        uid: None,
        message_id: Some("<d@example.test>".to_owned()),
        uidvalidity: None,
        mode: Some("new".to_owned()),
        subject: Some(subject.to_owned()),
        at: "2026-11-13T12:00:00+00:00".to_owned(),
    }
}

fn record() -> Record {
    Record { raw_sha: "ab".to_owned(), own_text: "Hi".to_owned(), ..Record::default() }
}

fn save(id: i64, version: i64, record: &Record, due_at: i64) -> Save<'_> {
    Save {
        draft_id: id,
        version,
        account: "ada@example.test",
        message_id: "<d@example.test>",
        raw: b"Subject: Linen\r\n\r\nHi\r\n",
        record,
        saved_at: 100,
        due_at,
        attempts: 0,
    }
}

#[test]
fn a_drafts_save_comes_due_unless_its_send_is_about_to_go_and_settles_once_pushed() {
    let dir = tempfile::tempdir().unwrap();
    let store = database(dir.path());
    let record = record();
    let id = sending_ports::write(&store, |book| {
        let id = book.draft_insert(&tracked("Linen"))?;
        book.draft_set_key(id, "k-1")?;
        book.journal_put(&save(id, 1, &record, 100))?;
        Ok(id)
    })
    .unwrap();
    assert_eq!(sending_ports::read(&store, |book| book.draft_by_key("k-1")).unwrap().unwrap().id, id);
    assert_eq!(sending_ports::read(&store, |book| book.journal_due(100, 160)).unwrap(), [id]);
    // A send of the draft due before the next round holds it: the outbox has its bytes.
    let send_id =
        sending_ports::write(&store, |book| book.outbox_insert(&NewSend { draft_id: Some(id), ..send("k-send", 150) }))
            .unwrap()
            .0
            .unwrap();
    assert!(sending_ports::read(&store, |book| book.journal_due(100, 160)).unwrap().is_empty());
    assert!(sending_ports::read(&store, |book| book.outbox_holds_draft(id)).unwrap());
    sending_ports::write(&store, |book| book.outbox_cancel_queued(send_id)).unwrap();
    // A try names the copy it may leave; a newer save since then stays to go, with no copy left to wonder about.
    assert!(sending_ports::write(&store, |book| book.journal_tried(id, 1, "<d@example.test>")).unwrap());
    sending_ports::write(&store, |book| book.journal_put(&save(id, 2, &record, 100))).unwrap();
    let waiting = sending_ports::read(&store, |book| book.journal_get(id)).unwrap().unwrap();
    assert_eq!((waiting.version, waiting.tried.as_deref()), (2, Some("<d@example.test>")));
    sending_ports::write(&store, |book| book.journal_settled(id, 1)).unwrap();
    let waiting = sending_ports::read(&store, |book| book.journal_get(id)).unwrap().unwrap();
    assert_eq!((waiting.version, waiting.tried), (2, None));
    sending_ports::write(&store, |book| book.journal_settled(id, 2)).unwrap();
    assert_eq!(sending_ports::read(&store, |book| book.journal_get(id)).unwrap(), None);
}

#[test]
fn a_discard_takes_the_place_of_a_waiting_save_and_a_try_at_it_is_refused() {
    let dir = tempfile::tempdir().unwrap();
    let store = database(dir.path());
    let record = Record { tail_text: Some("> quoted".to_owned()), ..record() };
    let id = sending_ports::write(&store, |book| {
        let id = book.draft_insert(&tracked("Linen"))?;
        book.journal_put(&save(id, 1, &record, 100))?;
        Ok(id)
    })
    .unwrap();
    assert_eq!(
        sending_ports::read(&store, |book| book.draft_quote(id)).unwrap(),
        Some((Some("> quoted".to_owned()), None))
    );
    sending_ports::write(&store, |book| book.journal_discard(id, 2, "ada@example.test", 120)).unwrap();
    let waiting = sending_ports::read(&store, |book| book.journal_get(id)).unwrap().unwrap();
    assert_eq!((waiting.discard, waiting.raw, waiting.due_at, waiting.version), (true, None, 120, 2));
    assert!(!sending_ports::write(&store, |book| book.journal_tried(id, 2, "<d@example.test>")).unwrap());
    // With no save in the journal, the quote is the copy's.
    assert_eq!(sending_ports::read(&store, |book| book.draft_quote(id)).unwrap(), Some((None, None)));
}

#[test]
fn a_draft_is_repointed_only_where_no_save_moved_it_on() {
    let dir = tempfile::tempdir().unwrap();
    let store = database(dir.path());
    let id = sending_ports::write(&store, |book| {
        let id = book.draft_insert(&tracked("Linen"))?;
        book.draft_set_resume(id, &record())?;
        Ok(id)
    })
    .unwrap();
    let seen: DraftRow = sending_ports::read(&store, |book| book.draft_get(id)).unwrap().unwrap();
    sending_ports::write(&store, |book| {
        book.draft_settle_push(id, "ada@example.test", "Drafts", Some(4), "<d@example.test>", Some(77), 130)
    })
    .unwrap();
    assert!(!sending_ports::write(&store, |book| book.draft_repoint(&seen, "Drafts", 9, Some(77), true)).unwrap());
    let now = sending_ports::read(&store, |book| book.draft_get(id)).unwrap().unwrap();
    assert!(sending_ports::write(&store, |book| book.draft_repoint(&now, "Drafts", 9, Some(77), true)).unwrap());
    let after = sending_ports::read(&store, |book| book.draft_get(id)).unwrap().unwrap();
    assert_eq!((after.uid, after.pushed_at, after.own_text, after.raw_sha), (Some(9), Some(130), None, None));
}

#[test]
fn a_block_that_fails_writes_nothing_and_says_its_own_fault() {
    let dir = tempfile::tempdir().unwrap();
    let store = database(dir.path());
    let failed = sending_ports::write(&store, |book| {
        book.outbox_insert(&send("k-1", 0))?;
        Err::<(), _>(Fault::new("Refused"))
    });
    assert_eq!(failed, Err(Fault::new("Refused")));
    assert!(sending_ports::read(&store, |book| book.outbox_due(i64::MAX)).unwrap().is_empty());
}

//! What the Assistant's background work reads and keeps, on a database the core makes (`tests/unit/assistant`'s
//! `test_assessment_record.py` and the Python adapters' `needs_summary`, `save_summary` and `draft_candidates`): one copy
//! of each message triage hasn't summarized, newest first, none a batch holds; what it says kept under the message's
//! key; and the mail a reply may be drafted ahead for.

#![allow(clippy::unwrap_used)]

use std::path::Path;
use std::sync::Arc;

use mm_assistant::background_ports::{Assessment, DraftCandidates, Outcome, Triage};
use mm_assistant::ports::Message;
use mm_kernel::seams::{Clock, Moment, Outside, Tripped};
use mm_store::{Database, DbKey};

#[derive(Debug)]
struct Frozen;

impl Clock for Frozen {
    fn now(&self) -> Moment {
        Moment::from_micros(1_793_620_800_000_000)
    }
}

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

const ADA: &str = "ada@example.test";

/// A database at the newest version, with `rows` in it.
fn database(dir: &Path, rows: &str) -> Database {
    let database = Database::new(dir.join("mailmind.db"), DbKey::from_bytes([0x5e; 32]), Arc::new(Open));
    database.ready(&Frozen).unwrap();
    database.write(|c| c.execute_batch(rows)).unwrap();
    database
}

/// Seven messages, by their keys: 1 in two folders, 2 junk, 3 summarized, 4 on its way out, 5 in a batch that is
/// out, 6 the newest, 7 with no time; and a copy with no key.
const PENDING: &str = "
INSERT INTO message_identity (key, account, identity) VALUES
  (1, 'ada@example.test', 'm1'), (2, 'ada@example.test', 'm2'), (3, 'ada@example.test', 'm3'),
  (4, 'ada@example.test', 'm4'), (5, 'ada@example.test', 'm5'), (6, 'ada@example.test', 'm6'),
  (7, 'ada@example.test', 'm7');
INSERT INTO messages (pk, account, kind, folder, uid, from_name, from_addr, subject, date_iso, ts, snippet, is_junk,
                      pending_removal, message_key) VALUES
  (1, 'ada@example.test', 'imap', 'INBOX', 1, 'Lin', 'lin@example.test', 'Lunch', '2026-11-02', 1000, 'Free?', 0, NULL, 1),
  (2, 'ada@example.test', 'imap', 'Archive', 1, 'Lin', 'lin@example.test', 'Lunch', '2026-11-02', 1000, 'Free?', 0, NULL, 1),
  (3, 'ada@example.test', 'imap', 'INBOX', 3, NULL, 'win@example.test', 'Prize', NULL, 2000, NULL, 1, NULL, 2),
  (4, 'ada@example.test', 'imap', 'INBOX', 4, NULL, 'ivo@example.test', 'Venue', NULL, 2000, NULL, 0, NULL, 3),
  (5, 'ada@example.test', 'imap', 'INBOX', 5, NULL, 'mae@example.test', 'Design', NULL, 2000, NULL, 0, 1, 4),
  (6, 'ada@example.test', 'imap', 'INBOX', 6, NULL, 'kai@example.test', 'Rent', NULL, 2000, NULL, 0, NULL, 5),
  (7, 'ada@example.test', 'imap', 'INBOX', 7, NULL, 'bo@example.test', 'Tea', NULL, 3000, NULL, 0, NULL, 6),
  (8, 'ada@example.test', 'imap', 'INBOX', 8, NULL, 'jo@example.test', 'Undated', NULL, NULL, NULL, 0, NULL, 7),
  (9, 'ada@example.test', 'imap', 'INBOX', 9, NULL, 'al@example.test', 'Keyless', NULL, 4000, NULL, 0, NULL, NULL);
INSERT INTO assessments (message_key, summary, importance) VALUES (3, 'Where to meet.', 3);
INSERT INTO ai_batches (id, feature, model, state, requests, estimate_micros, cutoff_ts, created_at) VALUES
  (1, 'triage', 'claude-opus-5-5', 'ended', 1, 0, 500, 0);
INSERT INTO ai_batch_items (message_key, batch_id) VALUES (5, 1);
";

fn keys(database: &Database, limit: i64, newer_than: Option<i64>) -> Vec<Option<i64>> {
    database.needs_summary(limit, newer_than).unwrap().into_iter().map(|p| p.message_key).collect()
}

#[test]
fn triage_reads_one_copy_of_each_message_it_hasnt_summarized_newest_first() {
    let dir = tempfile::tempdir().unwrap();
    let database = database(dir.path(), PENDING);
    assert_eq!(keys(&database, 10, None), [Some(6), Some(1), Some(7)]);
    assert_eq!(keys(&database, 1, None), [Some(6)]);
    assert_eq!(keys(&database, 10, Some(1000)), [Some(6)]);
    let [newest, lunch, _] = database.needs_summary(10, None).unwrap().try_into().unwrap();
    assert_eq!(
        (newest.pk, newest.from_name, newest.from_addr.as_deref(), newest.subject.as_deref(), newest.ts),
        (7, None, Some("bo@example.test"), Some("Tea"), Some(3000))
    );
    assert_eq!(
        (lunch.from_name.as_deref(), lunch.date_iso.as_deref(), lunch.snippet.as_deref()),
        (Some("Lin"), Some("2026-11-02"), Some("Free?"))
    );
}

#[test]
fn a_batch_that_is_out_says_where_it_cuts_off() {
    let dir = tempfile::tempdir().unwrap();
    let database = database(dir.path(), PENDING);
    assert_eq!(database.batch_cutoff(), Ok(None));
    database
        .write(|c| {
            c.execute_batch(
                "INSERT INTO ai_batches (id, feature, model, state, requests, estimate_micros, cutoff_ts, created_at)
                 VALUES (2, 'triage', 'claude-opus-5-5', 'running', 1, 0, 1500, 0),
                        (3, 'triage', 'claude-opus-5-5', 'ended', 1, 0, 2500, 0)",
            )
        })
        .unwrap();
    assert_eq!(database.batch_cutoff(), Ok(Some(1500)));
}

fn said(summary: &str, outcome: Outcome) -> Assessment {
    Assessment {
        summary: summary.into(),
        importance: 4,
        category: "personal".into(),
        action: "Reply".into(),
        model: "claude-opus-5-5".into(),
        served_by: Some("claude-opus-4-8".into()),
        prompt_version: Some(2),
        outcome: Some(outcome),
    }
}

type Kept = (String, i64, String, String, String, String, Option<String>, Option<i64>, Option<String>);

fn kept(database: &Database, key: i64) -> Option<Kept> {
    database
        .read(|c| {
            c.query_row(
                "SELECT summary, importance, category, action, model, created_at, served_by, prompt_version, outcome
                 FROM assessments WHERE message_key = ?",
                [key],
                |r| {
                    Ok((
                        r.get(0)?,
                        r.get(1)?,
                        r.get(2)?,
                        r.get(3)?,
                        r.get(4)?,
                        r.get(5)?,
                        r.get(6)?,
                        r.get(7)?,
                        r.get(8)?,
                    ))
                },
            )
            .map(Some)
            .or_else(|e| if e == rusqlite::Error::QueryReturnedNoRows { Ok(None) } else { Err(e) })
        })
        .unwrap()
}

#[test]
fn what_triage_said_is_kept_under_the_messages_key_and_said_again_replaces_it() {
    let dir = tempfile::tempdir().unwrap();
    let database = database(dir.path(), PENDING);
    database.save_summary(2, &said("Lunch on Thursday.", Outcome::Assessed), "2026-11-02T12:00:00+00:00").unwrap();
    assert_eq!(
        kept(&database, 1),
        Some((
            "Lunch on Thursday.".into(),
            4,
            "personal".into(),
            "Reply".into(),
            "claude-opus-5-5".into(),
            "2026-11-02T12:00:00+00:00".into(),
            Some("claude-opus-4-8".into()),
            Some(2),
            Some("assessed".into()),
        ))
    );
    assert_eq!(keys(&database, 10, None), [Some(6), Some(7)]);
    database.save_summary(1, &said("Lunch", Outcome::Refused), "2026-11-02T13:00:00+00:00").unwrap();
    let again = kept(&database, 1).unwrap();
    assert_eq!(
        (again.0.as_str(), again.5.as_str(), again.8.as_deref()),
        ("Lunch", "2026-11-02T13:00:00+00:00", Some("refused"))
    );
    // A copy with no key, or none at all, keeps nothing.
    database.save_summary(9, &said("Keyless", Outcome::Assessed), "2026-11-02T12:00:00+00:00").unwrap();
    database.save_summary(99, &said("Gone", Outcome::Assessed), "2026-11-02T12:00:00+00:00").unwrap();
    let count: i64 = database.read(|c| c.query_row("SELECT COUNT(*) FROM assessments", [], |r| r.get(0))).unwrap();
    assert_eq!(count, 2);
}

/// What a reply may be drafted ahead for, as of 4000: the newest of the lunch thread and a lone message that wants
/// action, and none of the rest.
const CANDIDATES: &str = "
INSERT INTO message_identity (key, account, identity)
  SELECT value, 'ada@example.test', 'd' || value FROM json_each('[10,11,12,13,14,15,16,17,18,19,20,21]');
INSERT INTO messages (pk, account, kind, folder, uid, thread_id, message_id, from_addr, ts, is_junk, answered,
                      pending_removal, message_key) VALUES
  (10, 'ada@example.test', 'imap', 'INBOX', 10, 't-lunch', '<d10@example.test>', 'lin@example.test', 5000, 0, 0, NULL, 10),
  (11, 'ada@example.test', 'imap', 'INBOX', 11, 't-lunch', '<d11@example.test>', 'lin@example.test', 4500, 0, 0, NULL, 11),
  (12, 'ada@example.test', 'imap', 'INBOX', 12, '', '<d12@example.test>', 'ivo@example.test', 4500, 0, 0, NULL, 12),
  (13, 'ada@example.test', 'imap', 'INBOX', 13, 't-info', '<d13@example.test>', 'news@example.test', 4500, 0, 0, NULL, 13),
  (14, 'ada@example.test', 'imap', 'INBOX', 14, 't-done', '<d14@example.test>', 'mae@example.test', 4500, 0, 1, NULL, 14),
  (15, 'ada@example.test', 'imap', 'INBOX', 15, 't-later', '<d15@example.test>', 'kai@example.test', 4500, 0, 0, NULL, 15),
  (16, 'ada@example.test', 'imap', 'INBOX', 16, 't-muted', '<d16@example.test>', 'bo@example.test', 4500, 0, 0, NULL, 16),
  (17, 'ada@example.test', 'imap', 'INBOX', 17, 't-old', '<d17@example.test>', 'jo@example.test', 3000, 0, 0, NULL, 17),
  (18, 'ada@example.test', 'imap', 'Archive', 18, 't-filed', '<d18@example.test>', 'al@example.test', 4500, 0, 0, NULL, 18),
  (19, 'ada@example.test', 'imap', 'INBOX', 19, NULL, NULL, 'cy@example.test', 4500, 0, 0, NULL, 19),
  (20, 'ada@example.test', 'imap', 'INBOX', 20, 't-going', '<d20@example.test>', 'di@example.test', 4500, 0, 0, 1, 20),
  (21, 'ada@example.test', 'imap', 'INBOX', 21, 't-junk', '<d21@example.test>', 'win@example.test', 4500, 1, 0, NULL, 21);
INSERT INTO assessments (message_key, importance, category)
  SELECT value, 5, 'personal' FROM json_each('[10,11,14,15,16,17,18,19,20,21]');
INSERT INTO assessments (message_key, importance, category) VALUES (12, 2, 'action-needed'), (13, 3, 'info');
INSERT INTO snoozes (message_key, folder, until) VALUES (15, 'INBOX', 9999999999);
INSERT INTO muted (account, thread_id, muted_at) VALUES ('ada@example.test', 't-muted', 'now');
";

#[test]
fn drafting_ahead_reads_each_threads_newest_that_wants_a_reply_most_important_first() {
    let dir = tempfile::tempdir().unwrap();
    let database = database(dir.path(), CANDIDATES);
    let lunch = Message {
        pk: 10,
        account: ADA.into(),
        thread_id: Some("t-lunch".into()),
        message_id: Some("<d10@example.test>".into()),
        message_key: Some(10),
        from_addr: Some("lin@example.test".into()),
        folder: Some("INBOX".into()),
        ..Message::default()
    };
    let lone = Message {
        pk: 12,
        thread_id: Some(String::new()),
        message_id: Some("<d12@example.test>".into()),
        message_key: Some(12),
        from_addr: Some("ivo@example.test".into()),
        ..lunch.clone()
    };
    assert_eq!(database.draft_candidates(4000), Ok(vec![lunch.clone(), lone]));
    assert_eq!(database.draft_candidates(4600), Ok(vec![lunch]));
}

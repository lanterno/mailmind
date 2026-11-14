//! The batches sent to the Claude API, on a database the core makes (`assistant.adapters.sqlite`'s `batch_*`): one out
//! at a time, the messages each holds by key, its answers kept for each message's copies and any that comes later, and
//! what a batch out books against the daily limit.

#![allow(clippy::unwrap_used)]

use std::path::Path;
use std::sync::Arc;

use mm_assistant::background_ports::{Assessment, BatchBook, Begun, Outcome, Pending, Triage};
use mm_assistant::ports::{Reply, Usage, UsageLedger};
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

/// Five messages waiting, by their keys: 1 in two folders, 2, 3 and 4 in the inbox, and 5 whose every copy has gone.
const MAIL: &str = "
INSERT INTO message_identity (key, account, identity) VALUES
  (1, 'ada@example.test', 'm1'), (2, 'ada@example.test', 'm2'), (3, 'ada@example.test', 'm3'),
  (4, 'ada@example.test', 'm4'), (5, 'ada@example.test', 'm5');
INSERT INTO messages (pk, account, kind, folder, uid, from_addr, subject, ts, is_junk, message_key) VALUES
  (1, 'ada@example.test', 'imap', 'INBOX', 1, 'lin@example.test', 'Lunch', 1000, 0, 1),
  (2, 'ada@example.test', 'imap', 'Archive', 1, 'lin@example.test', 'Lunch', 1000, 0, 1),
  (3, 'ada@example.test', 'imap', 'INBOX', 3, 'ivo@example.test', 'Venue', 2000, 0, 2),
  (4, 'ada@example.test', 'imap', 'INBOX', 4, 'kai@example.test', '', 3000, 0, 3),
  (5, 'ada@example.test', 'imap', 'INBOX', 5, 'bo@example.test', 'Tea', 4000, 0, 4);
";

fn database(dir: &Path) -> Database {
    let database = Database::new(dir.join("mailmind.db"), DbKey::from_bytes([0x5e; 32]), Arc::new(Open));
    database.ready(&Frozen).unwrap();
    database.write(|c| c.execute_batch(MAIL)).unwrap();
    database
}

fn begun(keys: &[i64]) -> Begun<'_> {
    Begun {
        feature: "triage-batch",
        model: "claude-opus-5-5",
        keys,
        estimate_micros: 9000,
        cutoff_ts: 2000,
        now: 1_793_620_800,
        prompt_version: 2,
    }
}

fn waiting(database: &Database) -> Vec<i64> {
    database.needs_summary(10, None).unwrap().into_iter().filter_map(|p| p.message_key).collect()
}

fn reply(text: &str) -> Reply {
    Reply {
        text: text.into(),
        model: "claude-opus-5-5".into(),
        usage: Usage::default(),
        stop_reason: "end_turn".into(),
    }
}

/// What a stored answer is made of: assessed when the reply says so, else what stands in, named by the copy's subject.
fn judge(copy: Option<&Pending>, reply: &Reply) -> Assessment {
    let assessed = reply.text != "unusable";
    Assessment {
        summary: if assessed { reply.text.clone() } else { copy.and_then(|c| c.subject.clone()).unwrap_or_default() },
        importance: 3,
        category: "info".into(),
        action: String::new(),
        model: if assessed { "claude-opus-5-5".into() } else { "parse-error".into() },
        served_by: Some(reply.model.clone()),
        prompt_version: Some(2),
        outcome: Some(if assessed { Outcome::Assessed } else { Outcome::Unusable }),
    }
}

fn kept(database: &Database) -> Vec<(i64, String, Option<String>, Option<String>)> {
    database
        .read(|c| {
            c.prepare("SELECT message_key, summary, outcome, created_at FROM assessments ORDER BY message_key")?
                .query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)))?
                .collect()
        })
        .unwrap()
}

#[test]
fn one_batch_at_a_time_holds_its_messages_until_its_answers_are_in() {
    let dir = tempfile::tempdir().unwrap();
    let database = database(dir.path());
    assert_eq!(database.open(), Ok(None));
    let id = database.begin(&begun(&[1, 2])).unwrap().expect("begun");
    assert_eq!(database.begin(&begun(&[3])), Ok(None), "another is out");
    let out = database.open().unwrap().expect("out");
    assert_eq!(
        (out.id, out.state.as_str(), out.remote_id, out.requests, out.estimate_micros, out.cutoff_ts),
        (id, "submitting", None, 2, 9000, 2000)
    );
    assert_eq!((out.created_at, out.polled_at, out.prompt_version), (1_793_620_800, 0, Some(2)));
    assert_eq!((waiting(&database), database.waiting(), database.batch_cutoff()), (vec![4, 3], Ok(2), Ok(Some(2000))));
    assert_eq!(database.reserved(), Ok(9000), "booked against the daily limit while it is out");

    database.sent(id, "msgbatch_01", 1_793_620_900).unwrap();
    let out = database.open().unwrap().expect("out");
    assert_eq!(
        (out.state.as_str(), out.remote_id.as_deref(), out.polled_at),
        ("running", Some("msgbatch_01"), 1_793_620_900)
    );
    database.polled(id, 1_793_621_200).unwrap();
    assert_eq!(database.in_state("running").unwrap()[0].polled_at, 1_793_621_200);
}

#[test]
fn a_batch_that_never_went_gives_its_messages_back() {
    let dir = tempfile::tempdir().unwrap();
    let database = database(dir.path());
    let id = database.begin(&begun(&[1, 2])).unwrap().expect("begun");
    database.drop_batch(id).unwrap();
    assert_eq!((database.open(), database.waiting(), database.reserved()), (Ok(None), Ok(0), Ok(0)));
    assert_eq!(waiting(&database), [4, 3, 2, 1]);
    assert!(database.begin(&begun(&[3])).unwrap().is_some(), "the next can go");
}

#[test]
fn its_answers_are_kept_for_every_copy_and_one_with_none_waits_again() {
    let dir = tempfile::tempdir().unwrap();
    let database = database(dir.path());
    let id = database.begin(&begun(&[1, 2, 3, 4])).unwrap().expect("begun");
    database.sent(id, "msgbatch_01", 0).unwrap();
    // Every copy of 4 has gone since; 2 got no answer; 9 is no message of the batch's.
    database.write(|c| c.execute("DELETE FROM messages WHERE message_key = 4", [])).unwrap();
    let answers =
        [(1, reply("Lunch is set.")), (3, reply("unusable")), (4, reply("Tea on Friday.")), (9, reply("No."))];
    let stored = database.collected(id, 1_793_621_100, "2026-11-02T12:05:00+00:00", &answers, &judge).unwrap();
    assert_eq!(stored, 3, "an answer for nothing of the batch's isn't one");
    assert_eq!(
        kept(&database),
        [
            (1, "Lunch is set.".into(), Some("assessed".into()), Some("2026-11-02T12:05:00+00:00".into())),
            (3, String::new(), Some("unusable".into()), Some("2026-11-02T12:05:00+00:00".into())),
            (4, "Tea on Friday.".into(), Some("assessed".into()), Some("2026-11-02T12:05:00+00:00".into())),
        ]
    );
    let [done] = database.in_state("collected").unwrap().try_into().unwrap();
    assert_eq!((done.id, done.ended_at), (id, Some(1_793_621_100)));
    assert_eq!((database.open(), database.waiting(), database.reserved()), (Ok(None), Ok(0), Ok(0)));
    assert_eq!(waiting(&database), [2], "the one with no answer waits again");
    database.discarded(id).unwrap();
    assert_eq!(database.in_state("done").unwrap().len(), 1);
}

#[test]
fn a_message_whose_copies_have_all_gone_keeps_only_an_answer_that_was_assessed() {
    let dir = tempfile::tempdir().unwrap();
    let database = database(dir.path());
    let id = database.begin(&begun(&[4])).unwrap().expect("begun");
    database.write(|c| c.execute("DELETE FROM messages WHERE message_key = 4", [])).unwrap();
    let stored = database.collected(id, 0, "2026-11-02T12:05:00+00:00", &[(4, reply("unusable"))], &judge).unwrap();
    assert_eq!((stored, kept(&database)), (1, vec![]));
}

#[test]
fn batches_are_read_by_state_oldest_first_and_the_one_out_is_the_newest() {
    let dir = tempfile::tempdir().unwrap();
    let database = database(dir.path());
    database
        .write(|c| {
            c.execute_batch(
                "INSERT INTO ai_batches (id, remote_id, feature, model, state, requests, estimate_micros, cutoff_ts,
                                         created_at, polled_at, ended_at, prompt_version) VALUES
                   (1, 'msgbatch_a', 'triage-batch', 'claude-sonnet-5-5', 'collected', 1, 10, 0, 5, 6, 7, NULL),
                   (2, 'msgbatch_b', 'triage-batch', 'claude-opus-5-5', 'collected', 1, 20, 0, 5, 6, 8, 2),
                   (3, NULL, 'triage-batch', 'claude-opus-5-5', 'submitting', 1, 30, 0, 9, 0, NULL, 2),
                   (4, 'msgbatch_d', 'triage-batch', 'claude-opus-5-5', 'running', 1, 40, 0, 9, 0, NULL, 2)",
            )
        })
        .unwrap();
    let collected: Vec<(i64, Option<String>, Option<i64>)> =
        database.in_state("collected").unwrap().into_iter().map(|b| (b.id, b.remote_id, b.prompt_version)).collect();
    assert_eq!(collected, [(1, Some("msgbatch_a".into()), None), (2, Some("msgbatch_b".into()), Some(2))]);
    assert_eq!(database.open().unwrap().map(|b| b.id), Some(4));
    assert_eq!(database.reserved(), Ok(70), "what the batches out may cost");
}

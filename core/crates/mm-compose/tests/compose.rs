//! What the outbox tray lists, and a draft as it opens, as the Python app's `outbox_item.listed`,
//! `draft_journal` and `drafts.info` give them, with the reply already saved to a message that isn't one.

#![allow(clippy::unwrap_used)]

use std::sync::{Arc, Mutex};

use mm_compose::drafts::Drafts;
use mm_compose::outbox::{SENT_COPY_FAILED, SENT_COPY_PENDING, WAITING_AFTER, listed, overdue_since, waiting};
use mm_compose::ports::{DraftFolders, Row, StoredDrafts, Tracked};
use mm_kernel::errors::Fault;
use mm_kernel::seams::{Clock, Moment};
use serde_json::{Value, json};

fn row(value: Value) -> Row {
    match value {
        Value::Object(map) => map,
        _ => Row::new(),
    }
}

fn send(more: Value) -> Row {
    let mut r = row(json!({
        "id": 4, "account": "ada@example.test", "subject": "Lunch", "state": "queued", "attempts": 0,
        "last_error": null, "created_at": "2026-11-02T11:59:00+00:00", "sent_at": null, "next_attempt_at": 1793620810,
        "delivered": 0, "wire_begun": 0, "sent_appended": 0, "wait_kind": "grace",
        "recipients": r#"{"to": ["Lin <lin@example.test>"], "cc": [], "bcc": ["a@example.test", "b@example.test"]}"#,
    }));
    r.extend(row(more));
    r
}

#[test]
fn a_queued_send_says_what_it_waits_for() {
    let item = listed(&send(json!({})));
    assert_eq!(item["to"], json!(["Lin <lin@example.test>"]));
    assert_eq!(item["bcc_count"], 2);
    assert_eq!((item["scheduled"].clone(), item["retrying"].clone()), (json!(false), json!(false)));
    assert_eq!(listed(&send(json!({"wait_kind": "scheduled"})))["scheduled"], true);
    assert_eq!(listed(&send(json!({"wait_kind": "backoff"})))["retrying"], true);
    assert_eq!(listed(&send(json!({"state": "sending", "wait_kind": "backoff"})))["retrying"], false);
}

#[test]
fn one_that_went_out_is_sent_until_its_copy_in_sent_is_owed_and_failed() {
    let pending = listed(&send(json!({"state": "sending", "delivered": 1})));
    assert_eq!((pending["state"].clone(), pending["error"].clone()), (json!("sent"), json!(SENT_COPY_PENDING)));
    let failed = listed(&send(json!({"state": "error", "delivered": 1, "last_error": "x"})));
    assert_eq!((failed["state"].clone(), failed["error"].clone()), (json!("error"), json!(SENT_COPY_FAILED)));
    let done = listed(&send(json!({"state": "error", "delivered": 1, "sent_appended": 1, "last_error": "x"})));
    assert_eq!((done["state"].clone(), done["error"].clone()), (json!("sent"), Value::Null));
}

#[test]
fn a_send_names_the_draft_it_goes_from() {
    assert_eq!(listed(&send(json!({"draft_id": 7})))["draft_id"], 7);
    assert_eq!(listed(&send(json!({})))["draft_id"], Value::Null);
}

#[test]
fn a_send_whose_answer_never_came_is_unsure() {
    assert_eq!(listed(&send(json!({"state": "error", "wire_begun": 1})))["unsure"], true);
    assert_eq!(listed(&send(json!({"state": "error", "wire_begun": 1, "delivered": 1})))["unsure"], false);
}

#[test]
fn recipients_that_dont_read_are_none_and_a_blank_subject_says_so() {
    let item = listed(&send(json!({"recipients": "not json", "subject": "", "next_attempt_at": null})));
    assert_eq!((item["to"].clone(), item["bcc_count"].clone()), (json!([]), json!(0)));
    assert_eq!(item["subject"], "(no subject)");
    assert_eq!(item["next_attempt_at"], 0);
}

#[test]
fn a_save_is_overdue_after_a_while_and_at_once_when_nothing_pushes() {
    assert_eq!(overdue_since(1000, true), 1000 - WAITING_AFTER);
    assert_eq!(overdue_since(1000, false), 1000);
    let save = waiting(&row(
        json!({"draft_id": 3, "account": "a", "subject": null, "saved_at": 9, "attempts": 1, "discard": 1, "due_at": 20}),
    ));
    assert_eq!(
        save,
        json!({"id": 3, "account": "a", "subject": "", "saved_at": 9, "attempts": 1, "discarding": true, "kept": false})
    );
    let kept = waiting(&row(json!({"draft_id": 4, "account": "a", "subject": "Notes", "saved_at": 9, "attempts": 0,
                                   "discard": 0, "due_at": mm_compose::draft_journal::KEPT_HERE})));
    assert_eq!(kept["kept"], json!(true));
}

#[derive(Debug)]
struct Now;

impl Clock for Now {
    fn now(&self) -> Moment {
        Moment::from_micros(1_793_620_800_000_000)
    }
}

#[derive(Debug, Default)]
struct Book {
    known: Option<Row>,
    tracked: Mutex<Vec<Tracked>>,
    /// MailMind's own drafts that answer the message, and the copies listed that name it.
    answering: Vec<Row>,
    naming: Vec<Row>,
    /// The tracked drafts by Message-ID, and those a send holds or a discard is taking.
    by_msgid: Vec<(&'static str, i64)>,
    held: Vec<i64>,
}

impl StoredDrafts for Book {
    fn by_message_id(&self, _account: &str, message_id: Option<&str>) -> Result<Option<Row>, Fault> {
        if let Some((_, id)) = self.by_msgid.iter().find(|(m, _)| Some(*m) == message_id) {
            return Ok(Some(row(json!({"id": id}))));
        }
        Ok(message_id.and(self.known.clone()))
    }

    fn track(&self, draft: &Tracked) -> Result<i64, Fault> {
        self.tracked.lock().unwrap().push(draft.clone());
        Ok(12)
    }

    fn uidvalidity(&self, _account: &str, _folder: &str) -> Result<Option<i64>, Fault> {
        Ok(Some(77))
    }

    fn answering(&self, _message_id: &str) -> Result<Vec<Row>, Fault> {
        Ok(self.answering.clone())
    }

    fn naming(&self, _message_id: &str) -> Result<Vec<Row>, Fault> {
        Ok(self.naming.clone())
    }

    fn held(&self, draft_id: i64) -> Result<bool, Fault> {
        Ok(self.held.contains(&draft_id))
    }
}

#[derive(Debug)]
struct DraftsIs(&'static str);

impl DraftFolders for DraftsIs {
    fn plays_drafts(&self, _account: &str, folder: &str) -> Result<bool, Fault> {
        Ok(folder == self.0)
    }
}

fn message(folder: &str, draft: i64) -> Row {
    row(json!({"account": "bea@example.test", "folder": folder, "uid": 5, "draft": draft,
               "message_id": "<d@example.test>", "subject": "Contract"}))
}

#[test]
fn another_message_is_no_draft() {
    let drafts = Drafts::new(Arc::new(Book::default()), Arc::new(DraftsIs("Drafts")), Arc::new(Now));
    assert!(drafts.info(&message("INBOX", 0)).unwrap().is_empty());
}

#[test]
fn a_tracked_draft_opens_with_its_id_and_mode() {
    let book = Book { known: Some(row(json!({"id": 3, "mode": "reply"}))), ..Book::default() };
    let drafts = Drafts::new(Arc::new(book), Arc::new(DraftsIs("Drafts")), Arc::new(Now));
    let info = drafts.info(&message("INBOX", 1)).unwrap();
    assert_eq!(Value::Object(info), json!({"draft_id": 3, "draft_mode": "reply", "is_draft": true}));
}

#[test]
fn another_apps_draft_is_tracked_as_it_opens() {
    let book = Arc::new(Book::default());
    let drafts = Drafts::new(book.clone(), Arc::new(DraftsIs("Drafts")), Arc::new(Now));
    let info = drafts.info(&message("Drafts", 0)).unwrap();
    assert_eq!(Value::Object(info), json!({"draft_id": 12, "draft_mode": "new", "is_draft": true}));
    let tracked = book.tracked.lock().unwrap();
    assert_eq!(tracked.len(), 1);
    assert_eq!(
        (tracked[0].folder.as_deref(), tracked[0].uid, tracked[0].uidvalidity),
        (Some("Drafts"), Some(5), Some(77))
    );
    assert_eq!(tracked[0].at, "2026-11-02T12:00:00+00:00");
}

fn reply_draft(book: Book) -> Value {
    let drafts = Drafts::new(Arc::new(book), Arc::new(DraftsIs("Drafts")), Arc::new(Now));
    let info = drafts.info(&message("INBOX", 0)).unwrap();
    info.get("reply_draft").cloned().unwrap_or(Value::Null)
}

/// A copy listed in `folder` naming the message, `<d@example.test>`, in its In-Reply-To (`irt`), saved at `ts`.
fn copy(folder: &str, message_id: &str, irt: &str, ts: i64, draft: i64) -> Row {
    row(json!({"pk": ts, "account": "bea@example.test", "folder": folder, "uid": ts, "message_key": ts * 10,
               "message_id": message_id, "in_reply_to": irt, "ts": ts, "draft": draft}))
}

#[test]
fn a_message_no_draft_answers_names_none() {
    assert_eq!(reply_draft(Book::default()), Value::Null);
}

#[test]
fn of_mailminds_own_replies_the_newest_save_opens_and_the_others_are_counted() {
    let book = Book {
        answering: vec![
            row(json!({"id": 3, "saved_at": 100})),
            row(json!({"id": 5, "saved_at": 300})),
            row(json!({"id": 4, "saved_at": 300})),
            row(json!({"id": 6, "saved_at": null})),
        ],
        ..Book::default()
    };
    assert_eq!(reply_draft(book), json!({"draft_id": 5, "in_drafts": null, "more": 3}));
}

#[test]
fn another_apps_reply_in_drafts_is_named_by_its_copy() {
    let book = Book {
        answering: vec![row(json!({"id": 3, "saved_at": 100}))],
        naming: vec![copy("Drafts", "<other@example.test>", "<d@example.test>", 200, 0)],
        ..Book::default()
    };
    assert_eq!(
        reply_draft(book),
        json!({"draft_id": null, "in_drafts": {"key": 2000, "folder": "Drafts", "uid": 200}, "more": 1})
    );
}

#[test]
fn a_copy_mailmind_tracks_is_named_by_its_id_once() {
    let book = Book {
        answering: vec![row(json!({"id": 3, "saved_at": 100}))],
        naming: vec![
            copy("Drafts", "<mine@example.test>", "<d@example.test>", 50, 0),
            copy("Drafts", "<older@example.test>", "<d@example.test>", 400, 0),
        ],
        by_msgid: vec![("<mine@example.test>", 3), ("<older@example.test>", 8)],
        ..Book::default()
    };
    assert_eq!(reply_draft(book), json!({"draft_id": 8, "in_drafts": null, "more": 1}));
}

#[test]
fn a_copy_that_isnt_a_draft_names_another_message_first_or_is_held_answers_nothing() {
    let book = Book {
        naming: vec![
            copy("Sent", "<sent@example.test>", "<d@example.test>", 10, 0),
            copy("Drafts", "<later@example.test>", "<other@example.test>", 20, 0),
            copy("Drafts", "<held@example.test>", "<d@example.test>", 30, 0),
            copy("Archive", "<flagged@example.test>", "<d@example.test>", 5, 1),
        ],
        by_msgid: vec![("<held@example.test>", 9)],
        held: vec![9],
        ..Book::default()
    };
    assert_eq!(
        reply_draft(book),
        json!({"draft_id": null, "in_drafts": {"key": 50, "folder": "Archive", "uid": 5}, "more": 0})
    );
}

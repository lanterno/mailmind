//! What the outbox tray lists, and a draft as it opens, as the Python app's `outbox_item.listed`,
//! `draft_journal` and `drafts.info` give them.

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
        json!({"draft_id": 3, "account": "a", "subject": null, "saved_at": 9, "attempts": 1, "discard": 1}),
    ));
    assert_eq!(save, json!({"id": 3, "account": "a", "subject": "", "saved_at": 9, "attempts": 1, "discarding": true}));
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
}

impl StoredDrafts for Book {
    fn by_message_id(&self, _account: &str, message_id: Option<&str>) -> Result<Option<Row>, Fault> {
        Ok(message_id.and(self.known.clone()))
    }

    fn track(&self, draft: &Tracked) -> Result<i64, Fault> {
        self.tracked.lock().unwrap().push(draft.clone());
        Ok(12)
    }

    fn uidvalidity(&self, _account: &str, _folder: &str) -> Result<Option<i64>, Fault> {
        Ok(Some(77))
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

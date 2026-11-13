//! A send's rules and a draft's, as values: the outbox row a send is written as, what a try leaves on it and what a
//! delivered one owes; a draft's saves and which copy is ours to remove; the journal's timing; the composer's files
//! and the time it asks a send to go at. Each as the Python app has it.

#![allow(clippy::unwrap_used)]

use mm_compose::attachments::{self, FURTHEST};
use mm_compose::draft;
use mm_compose::draft_journal::{self, PUSH_EVERY, PUSH_TICK};
use mm_compose::outbox_item::{self, OriginalRow, SendMeta, UNSURE};
use mm_compose::sending_ports::{DraftRow, Mark, OutboxRow, Waiting};
use mm_compose::spec::Built;
use serde_json::json;

fn built() -> Built {
    Built {
        raw: b"From: Ada <ada@example.test>\r\n\r\nHi\r\n".to_vec(),
        message_id: "<1.2.3@example.test>".to_owned(),
        envelope: vec!["lin@example.test".to_owned(), "bo@example.test".to_owned()],
        to_disp: vec!["Lin Mori <lin@example.test>".to_owned()],
        cc_disp: Vec::new(),
        bcc_disp: vec!["Zo\u{eb} \"B\" <bo@example.test>".to_owned()],
    }
}

fn meta(send_after: i64) -> SendMeta {
    SendMeta {
        account: "ada@example.test".to_owned(),
        idempotency_key: "k-1".to_owned(),
        subject: "Fabric order".to_owned(),
        send_after,
        ..SendMeta::default()
    }
}

#[test]
fn a_send_is_written_as_json_dumps_writes_its_lists() {
    let row = outbox_item::new_row(&built(), &meta(0), None, 110, "2026-11-13T12:00:00+00:00");
    assert_eq!(row.envelope, r#"["lin@example.test", "bo@example.test"]"#);
    assert_eq!(
        row.recipients,
        r#"{"to": ["Lin Mori <lin@example.test>"], "cc": [], "bcc": ["Zo\u00eb \"B\" <bo@example.test>"]}"#
    );
    assert_eq!((row.next_attempt_at, row.wait_kind), (110, outbox_item::GRACE));
    assert_eq!((row.orig_account, row.orig_message_id, row.original_key), (None, None, None));
    // What json.dumps escapes: past ASCII as UTF-16, DEL and controls as \u, the rest by their short names.
    assert_eq!(
        outbox_item::python_list(&["\u{1f600}\u{7f}\u{1}\t\\".to_owned()]),
        r#"["\ud83d\ude00\u007f\u0001\t\\"]"#
    );
}

#[test]
fn a_later_time_schedules_a_send_and_an_earlier_one_keeps_the_window() {
    let later = outbox_item::new_row(&built(), &meta(500), None, 110, "t");
    assert_eq!((later.next_attempt_at, later.wait_kind), (500, outbox_item::SCHEDULED));
    let within = outbox_item::new_row(&built(), &meta(100), None, 110, "t");
    assert_eq!((within.next_attempt_at, within.wait_kind), (110, outbox_item::GRACE));
}

#[test]
fn a_reply_names_its_original_as_its_row_has_it_and_else_as_the_request_did() {
    let orig = OriginalRow {
        account: "ada@example.test".to_owned(),
        message_id: Some("<o@example.test>".to_owned()),
        thread_id: Some("t-1".to_owned()),
        message_key: Some(7),
    };
    let asked = SendMeta { orig_message_id: Some("<asked@example.test>".to_owned()), ..meta(0) };
    let row = outbox_item::new_row(&built(), &asked, Some(&orig), 110, "t");
    assert_eq!(
        (row.orig_account.as_deref(), row.orig_message_id.as_deref(), row.orig_thread_id.as_deref(), row.original_key),
        (Some("ada@example.test"), Some("<o@example.test>"), Some("t-1"), Some(7))
    );
    let gone = outbox_item::new_row(&built(), &asked, None, 110, "t");
    assert_eq!(
        (gone.orig_account.as_deref(), gone.orig_message_id.as_deref(), gone.orig_thread_id),
        (Some("ada@example.test"), Some("<asked@example.test>"), None)
    );
}

#[test]
fn a_failed_try_backs_off_doubling_to_fifteen_minutes() {
    let waits: Vec<i64> = (1..9).map(outbox_item::backoff).collect();
    assert_eq!(waits, [20, 40, 80, 160, 320, 640, 900, 900]);
    assert_eq!(outbox_item::backoff(i64::MAX), 900);
    let again = outbox_item::failed_try(false, false, "timeout");
    assert_eq!(
        (again.give_up, again.error, again.unsure, again.marks),
        (false, "timeout", false, vec![Mark::WireBegun(false), Mark::WaitKind(outbox_item::BACKOFF)])
    );
    assert!(outbox_item::failed_try(false, true, "recipient refused").give_up);
    // Unanswered once it had the whole message: only a person sends it again, and it stays marked.
    let unsure = outbox_item::failed_try(true, false, "connection lost after the message went out");
    assert_eq!((unsure.give_up, unsure.error, unsure.unsure, unsure.marks), (true, UNSURE, true, Vec::new()));
}

#[test]
fn a_delivered_send_owes_what_its_kind_of_message_does() {
    let new = OutboxRow { delivered: true, ..OutboxRow::default() };
    let owed = outbox_item::owed(&new, false);
    assert_eq!(
        (owed.sent_copy, owed.sent_filed_for_us, owed.answered, owed.followup, owed.draft),
        (true, false, false, false, false)
    );
    let filed = outbox_item::owed(&new, true);
    assert_eq!((filed.sent_copy, filed.sent_filed_for_us), (false, true));
    let reply = OutboxRow {
        orig_message_id: Some("<o@example.test>".to_owned()),
        draft_id: Some(4),
        sent_appended: true,
        answered_set: true,
        ..OutboxRow::default()
    };
    let owed = outbox_item::owed(&reply, false);
    assert_eq!((owed.sent_copy, owed.answered, owed.followup, owed.draft), (false, false, true, true));
    assert_eq!(outbox_item::attempts_since_delivery(&OutboxRow { attempts: 3, ..OutboxRow::default() }), 0);
    let unsure = OutboxRow { state: "error".to_owned(), wire_begun: true, ..OutboxRow::default() };
    assert!(outbox_item::is_unsure(&unsure));
    assert!(!outbox_item::is_unsure(&OutboxRow { delivered: true, ..unsure }));
}

fn saved(version: i64, saved_by: Option<&str>) -> DraftRow {
    DraftRow {
        id: 1,
        account: "ada@example.test".to_owned(),
        version,
        saved_by: saved_by.map(str::to_owned),
        ..DraftRow::default()
    }
}

#[test]
fn a_save_that_never_saw_the_latest_version_may_not_overwrite_it() {
    assert_eq!(draft::composer_key(Some("  k-1 \n")), Some("k-1".to_owned()));
    assert_eq!(draft::composer_key(Some(" ")), None);
    assert_eq!(draft::composer_key(Some(&"\u{e9}".repeat(300))).unwrap().chars().count(), 200);
    let prev = saved(3, Some("k-1"));
    assert!(!draft::is_stale(&prev, None, None), "naming no version overwrites");
    assert!(!draft::is_stale(&prev, Some(3), None));
    assert!(draft::is_stale(&prev, Some(2), None));
    assert!(draft::is_stale(&prev, Some(2), Some("k-2")));
    assert!(!draft::is_stale(&prev, Some(2), Some("k-1")), "this composer's own save, whose answer was lost");
    assert_eq!((draft::next_version(None), draft::next_version(Some(&prev))), (1, 4));
    let empty: Vec<String> = Vec::new();
    assert!(draft::is_empty(" ", "\n", &empty, &empty, &empty, 0));
    assert!(!draft::is_empty("", "", &empty, &empty, &["bo@example.test".to_owned()], 0));
    assert!(!draft::is_empty("", "", &empty, &empty, &empty, 1));
}

#[test]
fn only_a_copy_mailmind_saved_or_one_that_is_plainly_a_draft_is_removed() {
    let copy = DraftRow { uid: Some(9), folder: Some("Archive".to_owned()), ..saved(1, None) };
    assert!(!draft::copy_is_ours(&DraftRow { uid: None, ..copy.clone() }, |_| true, || true));
    assert!(!draft::copy_is_ours(&copy, |_| false, || false));
    assert!(draft::copy_is_ours(&copy, |_| false, || true));
    assert!(draft::copy_is_ours(&copy, |f| f == "Archive", || false));
    assert!(draft::copy_is_ours(&DraftRow { raw_sha: Some("ab".to_owned()), ..copy.clone() }, |_| false, || false));
}

#[test]
fn a_push_replaces_its_own_mailboxs_copy_and_leaves_another_accounts_to_be_removed_there() {
    let prev = DraftRow {
        uid: Some(9),
        uidvalidity: Some(77),
        folder: Some("Drafts".to_owned()),
        message_id: Some("<d@example.test>".to_owned()),
        ..saved(1, None)
    };
    let here = draft::replaced_in_place(&prev, "ada@example.test");
    assert_eq!(
        (here.prev_uid, here.prev_uidvalidity, here.prev_folder.as_deref()),
        (Some(9), Some(77), Some("Drafts"))
    );
    assert_eq!(draft::replaced_in_place(&prev, "bo@example.test"), mm_compose::sending_ports::InPlace::default());
    assert!(!draft::left_elsewhere(&prev, "ada@example.test", "Drafts"));
    assert!(draft::left_elsewhere(&prev, "ada@example.test", "[Gmail]/Drafts"));
    assert!(draft::left_elsewhere(&prev, "bo@example.test", "Drafts"));
    assert!(!draft::left_elsewhere(&DraftRow { uidvalidity: None, ..prev.clone() }, "bo@example.test", "Drafts"));
    assert!(draft::spans_accounts(&prev, "bo@example.test"));
    assert!(!draft::spans_accounts(&DraftRow { uid: None, ..prev }, "bo@example.test"));
}

#[test]
fn typing_puts_no_push_off_and_a_failed_one_waits_longer_each_time() {
    let waiting = Waiting { due_at: 130, attempts: 2, ..Waiting::default() };
    assert_eq!(draft_journal::after_save(false, 100, Some(&waiting), Some(90)), (100, 0));
    assert_eq!(draft_journal::after_save(true, 100, Some(&waiting), Some(90)), (130, 2));
    assert_eq!(draft_journal::after_save(true, 100, None, Some(90)), (150, 0));
    assert_eq!(draft_journal::after_save(true, 100, None, None), (100 + PUSH_EVERY, 0));
    assert_eq!(draft_journal::after_save(true, 100, None, Some(10)), (100, 0));
    let waits: Vec<i64> = [1, 2, 3, 6, 7, 50].iter().map(|n| draft_journal::due_after_failing(0, *n)).collect();
    assert_eq!(waits, [20, 40, 80, 640, 900, 900]);
    assert_eq!(draft_journal::next_round_in(100, None), PUSH_TICK);
    assert_eq!(draft_journal::next_round_in(100, Some(103)), 3);
    assert_eq!(draft_journal::next_round_in(100, Some(500)), PUSH_TICK);
}

#[test]
fn a_discard_waits_on_a_copy_a_try_may_have_left() {
    let tried = Waiting {
        tried: Some("<d@example.test>".to_owned()),
        account: "ada@example.test".to_owned(),
        ..Waiting::default()
    };
    assert!(draft_journal::copy_may_be_there(None, Some(&tried)));
    assert!(draft_journal::copy_may_be_there(Some(4), None));
    assert!(!draft_journal::copy_may_be_there(Some(0), Some(&Waiting::default())));
    assert_eq!(draft_journal::left_by_a_try(Some(&tried), "ada@example.test"), Some("<d@example.test>"));
    assert_eq!(draft_journal::left_by_a_try(Some(&tried), "bo@example.test"), None);
    assert!(draft_journal::asks_removal(Some(&Waiting { discard: true, ..Waiting::default() })));
    assert!(!draft_journal::holds_a_save(Some(&Waiting { raw: Some(Vec::new()), ..Waiting::default() })));
}

#[test]
fn a_composers_files_are_decoded_strictly_and_judged_in_pythons_words() {
    let files = [json!({"filename": "", "b64": "aGk=", "content_type": "multipart/related"})];
    let [file] = attachments::decode(Some(&files)).unwrap().try_into().unwrap();
    assert_eq!(
        (file.filename.as_str(), file.content_type.as_str(), file.data.as_slice()),
        ("attachment", "application/octet-stream", b"hi".as_slice())
    );
    let refused = |item: serde_json::Value| attachments::decode(Some(&[item])).unwrap_err();
    assert_eq!(refused(json!("x")), "bad attachment");
    assert_eq!(refused(json!({"b64": "aGk"})), "bad attachment encoding");
    assert_eq!(refused(json!({"b64": "a Gk="})), "bad attachment encoding");
    assert_eq!(refused(json!({"b64": 5})), "bad attachment encoding");
    assert_eq!(refused(json!({"b64": "", "cid": 5})), "bad attachment");
    assert_eq!(attachments::strict_base64("aGVsbG8="), Some(b"hello".to_vec()));
    assert_eq!(attachments::strict_base64("aGVsbG8"), None);
}

#[test]
fn a_send_time_must_be_ahead_and_within_a_year() {
    let now = 1_000_000 * 1_000_000;
    assert_eq!(attachments::send_after(None, now), Ok(0));
    assert_eq!(attachments::send_after(Some(0), now), Ok(0));
    assert_eq!(attachments::send_after(Some(1_000_000), now), Err("that send time has passed"));
    assert_eq!(attachments::send_after(Some(1_000_001), now), Ok(1_000_001));
    assert_eq!(attachments::send_after(Some(1_000_000 + FURTHEST), now), Err("pick a send time within a year"));
    assert_eq!(attachments::send_after(Some(1_000_000 + FURTHEST - 1), now), Ok(1_000_000 + FURTHEST - 1));
}

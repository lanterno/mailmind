//! What a command's row says, what reverses it and what the tray shows, which message a try acts on, and where a
//! filing verb sends a message and which way (`tests/unit/mailbox/test_command_rules.py`, `test_destination.py` and
//! `test_move_strategy.py`). Values in and values out: nothing here reaches a server.

use std::collections::BTreeMap;

use mm_mailbox::command::{
    self, Command, Message, Outcome, Target, idempotency_key, is_first_try, names_a_request, of_try, same_epoch,
    takes_a_move,
};
use mm_mailbox::destination::{Destination, MoveStrategy, Provider, Way, choose, known_without_server, resolve};
use serde_json::{Value, json};

fn message() -> Message {
    Message {
        pk: 3,
        account: "ada@example.test".into(),
        folder: "INBOX".into(),
        uid: 5,
        message_id: Some("<m@example.test>".into()),
        subject: Some("Lunch".into()),
        from_name: Some("Ada".into()),
        from_addr: Some("ada@example.test".into()),
        thread_id: Some("rf:1".into()),
        pending_removal: None,
        flags: [1, 0, 0, 0, 0],
        message_key: Some(11),
    }
}

fn row() -> Command {
    Command {
        id: 1,
        account: "a@example.test".into(),
        kind: "flag".into(),
        message_id: "<m@example.test>".into(),
        src_folder: "INBOX".into(),
        dest: None,
        params: json!({"flags": {"seen": true}}),
        state: "error".into(),
        attempts: 3,
        next_attempt_at: 0,
        applied: false,
        begun: false,
        outcome: json!({}),
        last_error: Some("refused".into()),
        created_at: "2026-11-02T12:00:00+00:00".into(),
    }
}

// -- which message a try acts on --

fn target(uid: Option<i64>, uidvalidity: Option<u32>, first_try: bool, has_message_id: bool) -> Target {
    of_try(uid, Some(7), uidvalidity, first_try, has_message_id)
}

#[test]
fn a_first_try_acts_on_the_uid_seen_and_a_later_one_asks_by_message_id() {
    assert_eq!(target(Some(5), Some(7), true, true), Target { by_message_id: false, uid: Some(5) });
    assert_eq!(target(Some(5), Some(7), false, true), Target { by_message_id: true, uid: Some(5) });
}

#[test]
fn a_uid_from_another_epoch_is_never_believed() {
    assert_eq!(target(Some(5), Some(9), true, true), Target { by_message_id: true, uid: None });
    assert_eq!(target(Some(5), Some(9), true, false), Target { by_message_id: false, uid: None });
    assert_eq!(target(None, Some(7), true, true), Target { by_message_id: true, uid: None });
}

#[test]
fn an_epoch_either_side_doesnt_say_counts_as_the_same() {
    for (epoch, uidvalidity) in [(Some(7), Some(7)), (None, Some(7)), (Some(7), None), (None, None), (Some(0), Some(9))]
    {
        assert!(same_epoch(epoch, uidvalidity), "{epoch:?} {uidvalidity:?}");
    }
    assert!(!same_epoch(Some(7), Some(9)));
}

#[test]
fn a_try_is_a_first_unless_an_earlier_one_may_have_reached_the_server() {
    assert!(is_first_try(true, true));
    assert!(is_first_try(false, false));
    assert!(!is_first_try(false, true));
}

// -- accepting a move --

#[test]
fn a_clients_id_names_its_request_and_anything_else_makes_it_its_own() {
    assert!(names_a_request(Some("m1")) && names_a_request(Some(&"x".repeat(128))));
    assert!(!names_a_request(Some("")) && !names_a_request(Some(&"x".repeat(129))) && !names_a_request(None));
}

#[test]
fn a_key_goes_by_the_message_id_and_else_the_row() {
    assert_eq!(
        idempotency_key("trash", "a@example.test", "<m@example.test>", 3, "r"),
        "trash:a@example.test:<m@example.test>:r"
    );
    assert_eq!(idempotency_key("trash", "a@example.test", "", 3, "r"), "trash:a@example.test:3:r");
}

#[test]
fn a_message_gone_going_or_there_already_is_not_taken() {
    let going = Message { pending_removal: Some(4), ..message() };
    assert!(takes_a_move(Some(&message()), None) && takes_a_move(Some(&message()), Some("Receipts")));
    assert!(!takes_a_move(None, None));
    assert!(!takes_a_move(Some(&going), None));
    assert!(!takes_a_move(Some(&message()), Some("INBOX")));
}

#[test]
fn what_a_move_keeps_of_its_message() {
    let unread = Message { flags: [0; 5], ..message() };
    assert_eq!(
        command::move_params(3, &unread, Some(7)),
        json!({"pk": 3, "uid": 5, "epoch": 7, "seen": false, "subject": "Lunch", "thread_id": "rf:1"})
    );
}

#[test]
fn a_flag_change_keeps_the_flags_it_replaces() {
    let params = command::flag_params(&message(), None, &[("seen", false), ("flagged", true)]);
    assert_eq!(params["flags"], json!({"seen": false, "flagged": true}));
    assert_eq!(params["was"], json!({"seen": true, "flagged": false}));
    assert_eq!(command::flags_of(params.get("was")), vec![("seen".to_owned(), true), ("flagged".to_owned(), false)]);
}

#[test]
fn an_undo_entry_says_where_it_went_and_how_to_show_it() {
    let entry =
        command::undo_entry("junk", 9, &message(), "<m@example.test>", &Destination::folder(Some("Spam".into())));
    assert_eq!(
        entry,
        json!({"kind": "move", "command": 9, "account": "ada@example.test", "src_folder": "INBOX", "dest_folder": "Spam",
               "message_id": "<m@example.test>", "label": "Marked junk", "subject": "Lunch", "who": "Ada",
               "thread_id": "rf:1"})
    );
    let gmail = Destination { folder: None, drop_inbox_label: true };
    let archived = command::undo_entry("archive", 9, &message(), "<m@example.test>", &gmail);
    assert_eq!((archived["kind"].as_str(), archived["dest_folder"].as_str()), (Some("gmail_archive"), Some("")));
    assert_eq!(archived["label"], "Archived");
    let nameless = Message { from_name: Some(String::new()), ..message() };
    let entry = command::undo_entry("move", 9, &nameless, "<m@example.test>", &Destination::folder(Some("X".into())));
    assert_eq!(entry["who"], "ada@example.test");
}

// -- what reverses it, and the tray --

#[test]
fn only_the_entry_of_this_move_reverses_it() {
    let base = json!({"account": "a@example.test", "message_id": "<m@example.test>", "src_folder": "INBOX"});
    let with = |name: &str, value: &str| {
        let mut entry = base.clone();
        entry["kind"] = json!("move");
        entry[name] = json!(value);
        entry
    };
    for kind in ["move", "gmail_archive"] {
        assert!(command::reverses(&with("kind", kind), &row()));
    }
    for (name, value) in
        [("kind", "snooze"), ("account", "b@example.test"), ("message_id", "<n@example.test>"), ("src_folder", "Sent")]
    {
        assert!(!command::reverses(&with(name, value), &row()), "{name}");
    }
}

#[test]
fn a_row_given_up_on_says_why() {
    assert_eq!(command::why_given_up(Outcome::Removed), "account removed");
    assert_eq!(command::why_given_up(Outcome::Refused), "refused");
    assert!(Outcome::NoFolder.wont_happen() && !Outcome::Queued.wont_happen() && !Outcome::Cancelled.wont_happen());
}

#[test]
fn the_tray_counts_a_try_as_waiting() {
    let counts: BTreeMap<String, i64> =
        [("queued", 2), ("running", 1), ("error", 4)].map(|(k, v)| (k.into(), v)).into();
    assert_eq!(command::tray_counts(&counts), (3, 4));
    assert_eq!(command::tray_counts(&BTreeMap::new()), (0, 0));
}

#[test]
fn the_tray_shows_a_change_as_the_page_reads_it() {
    assert_eq!(
        command::tray_item(&row(), &json!("Lunch")),
        json!({"id": 1, "account": "a@example.test", "op": "flag", "folder": "INBOX", "dest": null,
               "flags": {"seen": true}, "state": "error", "attempts": 3, "error": "refused", "subject": "Lunch",
               "next_attempt_at": 0, "created_at": "2026-11-02T12:00:00+00:00"})
    );
}

#[test]
fn bool_reads_json_as_python_does() {
    for falsy in [json!(null), json!(false), json!(0), json!(0.0), json!(""), json!([]), json!({})] {
        assert!(!command::truthy(&falsy), "{falsy}");
    }
    for truthy in [json!(true), json!(1), json!("x"), json!([0]), json!({"a": 0})] {
        assert!(command::truthy(&truthy), "{truthy}");
    }
}

// -- where it goes --

fn playing<'a>(folders: &'a [(&'a str, &'a str)]) -> impl FnOnce(&[&str]) -> Result<Option<String>, ()> + 'a {
    move |roles| Ok(roles.iter().find_map(|role| folders.iter().find(|(r, _)| r == role).map(|(_, f)| (*f).to_owned())))
}

fn asked(roles: &[&str]) -> Result<Option<String>, ()> {
    panic!("no reason to ask for {roles:?}")
}

fn gmail() -> Provider {
    Provider::of_kind("gmail")
}

fn at(folder: &str) -> Destination {
    Destination::folder(Some(folder.into()))
}

#[test]
fn gmails_archive_goes_nowhere_and_asks_nothing() {
    let dropped = Destination { folder: None, drop_inbox_label: true };
    assert_eq!(resolve("archive", None, &gmail(), Some("Trash"), asked), Ok(dropped.clone()));
    assert_eq!(resolve("archive", Some("Receipts"), &gmail(), Some("Trash"), asked), Ok(dropped));
}

#[test]
fn elsewhere_an_archive_goes_to_the_archive_role_and_all_mail_only_without_one() {
    let plain = Provider::default();
    let both = [("archive", "Archive"), ("all", "All Mail")];
    assert_eq!(resolve("archive", None, &plain, None, playing(&both)), Ok(at("Archive")));
    assert_eq!(resolve("archive", None, &plain, None, playing(&[("all", "All Mail")])), Ok(at("All Mail")));
    assert_eq!(resolve("archive", None, &plain, None, playing(&[])), Ok(Destination::default()));
}

#[test]
fn a_folder_the_command_names_is_where_it_goes() {
    for kind in ["trash", "archive", "junk", "move"] {
        assert_eq!(resolve(kind, Some("Receipts"), &Provider::default(), Some("Trash"), asked), Ok(at("Receipts")));
    }
    assert_eq!(resolve("move", None, &Provider::default(), Some("Trash"), asked), Ok(Destination::default()));
}

#[test]
fn trash_and_junk_go_where_the_account_and_its_provider_say() {
    assert_eq!(resolve("trash", None, &gmail(), Some("[Gmail]/Trash"), asked), Ok(at("[Gmail]/Trash")));
    assert_eq!(resolve("junk", None, &gmail(), None, playing(&[("junk", "Junk")])), Ok(at("Junk")));
    assert_eq!(resolve("junk", None, &gmail(), None, playing(&[])), Ok(at("[Gmail]/Spam")));
    assert_eq!(resolve("junk", None, &Provider::default(), None, playing(&[])), Ok(Destination::default()));
}

#[test]
fn as_a_command_is_accepted_only_what_needs_no_server_is_known() {
    assert_eq!(known_without_server("trash", false, Some("Trash")), at("Trash"));
    assert!(known_without_server("archive", true, Some("Trash")).drop_inbox_label);
    assert_eq!(known_without_server("archive", false, Some("Trash")), Destination::default());
    assert_eq!(known_without_server("junk", false, Some("Trash")), Destination::default());
}

// -- which way --

fn way(kind: &str, provider: &Provider, capabilities: &[&str], named: Option<&str>) -> MoveStrategy {
    let capabilities: Vec<String> = capabilities.iter().map(|c| (*c).to_owned()).collect();
    let find = |roles: &[&str]| -> Result<Option<String>, ()> {
        let first = roles.first().copied().unwrap_or_default();
        Ok(Some(format!("{}{}", first[..1].to_uppercase(), &first[1..])))
    };
    let place = resolve(kind, named, provider, Some("Trash"), find).unwrap_or_default();
    choose(kind, &place, &capabilities, provider.marks_trash_read, Some("Trash"))
}

#[test]
fn every_filing_but_gmails_archive_goes_as_the_server_offers() {
    for provider in [Provider::of_kind("gmail"), Provider::of_kind("proton"), Provider::of_kind("imap")] {
        for kind in ["trash", "archive", "junk", "move"] {
            if kind == "archive" && provider.archive_is_label_removal {
                continue;
            }
            assert_eq!(way(kind, &provider, &["IMAP4REV1", "MOVE"], Some("X")).way, Way::Move);
            assert_eq!(way(kind, &provider, &["IMAP4REV1", "UIDPLUS"], Some("X")).way, Way::Copy);
        }
    }
    let labels = MoveStrategy { way: Way::Labels, read_seen_first: false };
    assert_eq!(way("archive", &gmail(), &["MOVE"], Some("Archive")), labels);
    assert_eq!(way("archive", &gmail(), &[], None), labels);
}

#[test]
fn a_trash_reads_seen_first_only_where_the_server_will_mark_it_read() {
    let proton = Provider::of_kind("proton");
    assert!(way("trash", &proton, &["MOVE"], None).read_seen_first);
    assert!(way("trash", &proton, &[], None).read_seen_first);
    for kind in ["archive", "junk"] {
        assert!(!way(kind, &proton, &["MOVE"], None).read_seen_first, "{kind}");
    }
    assert!(!way("trash", &gmail(), &["MOVE"], None).read_seen_first);
    assert!(!way("trash", &Provider::of_kind("imap"), &["MOVE"], None).read_seen_first);
    assert!(way("move", &proton, &["MOVE"], Some("Trash")).read_seen_first);
    assert!(!way("move", &proton, &["MOVE"], Some("Folders/Clients")).read_seen_first);
    assert!(!way("move", &Provider::of_kind("imap"), &["MOVE"], Some("Trash")).read_seen_first);
}

#[test]
fn a_param_that_is_null_is_none() {
    let held = Command { params: json!({"pk": null, "uid": 5}), ..row() };
    assert_eq!(held.param("pk"), None);
    assert_eq!(held.param("uid"), Some(&Value::from(5)));
}

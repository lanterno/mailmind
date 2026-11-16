//! A folder a person makes, renames or deletes (`mm_mailbox::folder_change`): the name they typed as IMAP keeps it,
//! which folders are theirs to change, and what the stored names become once one is renamed. The Python app's
//! tests/unit/mailbox/test_folder_change.py, case for case.

use std::collections::BTreeSet;

use mm_kernel::errors::{Kind, Problem};
use mm_mailbox::folder_change::{
    GONE, NO_NAME, PLAYS_A_PART, SERVERS_OWN, TAKEN, TOO_LONG, UNSENDABLE, as_listed, changeable, changeable_names,
    has_folders_inside, modified_utf7, moved, nested, new_name, renamed_to,
};
use mm_mailbox::roles::{Folder, from_modified_utf7, own_name};

fn folder(name: &str, attributes: &[&str]) -> Folder {
    Folder {
        name: name.to_owned(),
        attributes: attributes.iter().map(|a| (*a).to_owned()).collect::<BTreeSet<_>>(),
        delimiter: Some("/".to_owned()),
    }
}

fn rail() -> Vec<Folder> {
    vec![
        folder("INBOX", &[]),
        folder("Sent", &["\\sent"]),
        folder("Drafts", &[]),
        folder("Archive", &[]),
        folder("Junk", &[]),
        folder("Trash", &[]),
        folder("Bin", &[]),
        folder("Clients", &[]),
        folder("Clients/2026", &[]),
        folder("Old", &["\\noselect"]),
    ]
}

fn gmail() -> Vec<Folder> {
    vec![
        folder("INBOX", &[]),
        folder("[Gmail]", &["\\noselect"]),
        folder("[Gmail]/All Mail", &["\\all"]),
        folder("[Gmail]/Trash", &["\\trash"]),
        folder("[Gmail]/Starred", &["\\flagged"]),
        folder("[Gmail]/Important", &["\\important"]),
        folder("[Gmail]/Chats", &[]),
        folder("Receipts", &[]),
        folder("Work/Clients", &[]),
    ]
}

fn bridge() -> Vec<Folder> {
    vec![
        folder("INBOX", &[]),
        folder("Trash", &["\\trash"]),
        folder("Folders", &["\\noselect"]),
        folder("Labels", &["\\noselect"]),
        folder("Folders/Clients", &[]),
    ]
}

fn courier() -> Vec<Folder> {
    ["INBOX", "INBOX.Sent", "INBOX.Trash", "INBOX.Clients"]
        .into_iter()
        .map(|name| Folder { delimiter: Some(".".to_owned()), ..Folder::named(name) })
        .collect()
}

fn said(problem: Problem) -> (Kind, String) {
    (problem.kind, problem.said)
}

#[test]
fn a_typed_name_goes_as_list_gives_it_back() {
    for (typed, listed) in [
        ("Receipts", "Receipts"),
        ("  Receipts \t", "Receipts"),
        ("Été & co", "&AMk-t&AOk- &- co"),
        ("日本語", "&ZeVnLIqe-"),
        ("Tom & Jerry", "Tom &- Jerry"),
        ("Say \"hi\"", "Say \\\"hi\\\""),
        ("a\\b", "a\\\\b"),
        ("🙂", "&2D3eQg-"),
    ] {
        assert_eq!(as_listed(Some(typed), Some("/")).unwrap(), listed, "{typed}");
    }
    assert_eq!(modified_utf7("ü"), "&APw-");
}

#[test]
fn a_name_that_cant_be_one_is_refused_in_words_that_dont_repeat_it() {
    let long = "x".repeat(201);
    for (typed, words) in [
        (Some(""), NO_NAME.to_owned()),
        (Some("   "), NO_NAME.to_owned()),
        (None, NO_NAME.to_owned()),
        (Some(long.as_str()), TOO_LONG.to_owned()),
        (Some("two\nlines"), UNSENDABLE.to_owned()),
        (Some("a\tb"), UNSENDABLE.to_owned()),
        (Some("Work/Clients"), nested("/")),
    ] {
        assert_eq!(said(as_listed(typed, Some("/")).unwrap_err()), (Kind::Refused, words));
    }
    assert_eq!(as_listed(Some(&"x".repeat(200)), Some("/")).unwrap(), "x".repeat(200));
}

#[test]
fn a_name_may_hold_a_slash_where_the_server_separates_with_a_dot() {
    assert_eq!(as_listed(Some("Work/Clients"), Some(".")).unwrap(), "Work/Clients");
    assert!(as_listed(Some("Work.Clients"), Some(".")).is_err());
}

#[test]
fn the_folders_that_play_a_part_and_the_accounts_trash_are_never_changed() {
    let rail = rail();
    for name in ["INBOX", "inbox", "Sent", "Drafts", "Archive", "Junk", "Trash", "Bin"] {
        let refused = changeable(&rail, name, Some("Bin")).unwrap_err();
        assert_eq!(said(refused), (Kind::Refused, PLAYS_A_PART.to_owned()), "{name}");
    }
}

#[test]
fn a_folder_the_server_keeps_is_never_changed() {
    let gmail = gmail();
    for name in ["[Gmail]/Starred", "[Gmail]/Important", "[Gmail]/Chats"] {
        let refused = changeable(&gmail, name, Some("[Gmail]/Trash")).unwrap_err();
        assert_eq!(said(refused), (Kind::Refused, SERVERS_OWN.to_owned()), "{name}");
    }
}

#[test]
fn a_folder_with_a_special_use_attribute_is_the_servers_wherever_it_sits() {
    let folders = vec![
        folder("INBOX", &[]),
        folder("Sent", &["\\sent"]),
        folder("Virtual/Flagged", &["\\flagged"]),
        folder("Old/Sent", &["\\sent"]),
    ];
    for name in ["Virtual/Flagged", "Old/Sent"] {
        let refused = changeable(&folders, name, Some("Trash")).unwrap_err();
        assert_eq!(said(refused), (Kind::Refused, SERVERS_OWN.to_owned()), "{name}");
    }
}

#[test]
fn the_rail_offers_to_change_only_a_persons_own() {
    let names = |folders: &[Folder], trash| changeable_names(folders, Some(trash)).into_iter().collect::<Vec<_>>();
    assert_eq!(names(&gmail(), "[Gmail]/Trash"), ["Receipts", "Work/Clients"]);
    assert_eq!(names(&rail(), "Bin"), ["Clients", "Clients/2026"]);
}

#[test]
fn a_folder_not_listed_or_not_selectable_is_gone() {
    let rail = rail();
    for name in ["Nowhere", "Old"] {
        assert_eq!(said(changeable(&rail, name, Some("Trash")).unwrap_err()), (Kind::NotFound, GONE.to_owned()));
    }
    assert_eq!(changeable(&rail, "Clients", Some("Trash")).unwrap().name, "Clients");
}

#[test]
fn a_folder_with_folders_inside_is_known() {
    let rail = rail();
    assert!(has_folders_inside(&rail, &folder("Clients", &[])));
    assert!(!has_folders_inside(&rail, &folder("Clients/2026", &[])));
    assert!(!has_folders_inside(&rail, &Folder::named("Clients")));
}

#[test]
fn a_rename_keeps_the_folder_where_it_is() {
    let rail = rail();
    assert_eq!(renamed_to(&folder("Clients/2026", &[]), Some("2027"), &rail).unwrap(), "Clients/2027");
    assert_eq!(renamed_to(&folder("Clients", &[]), Some("Customers"), &rail).unwrap(), "Customers");
    assert_eq!(renamed_to(&folder("Clients", &[]), Some("Clients"), &rail).unwrap(), "Clients");
    let taken = renamed_to(&folder("Clients", &[]), Some("Archive"), &rail).unwrap_err();
    assert_eq!(said(taken), (Kind::Conflict, TAKEN.to_owned()));
    let courier = courier();
    assert_eq!(renamed_to(&courier[3], Some("Customers"), &courier).unwrap(), "INBOX.Customers");
}

#[test]
fn a_folder_renamed_to_the_name_it_has_stays_as_it_is() {
    for (name, typed) in [
        ("R&-D", "R&D"),
        ("&AMk-t&AOk-", "Été"),
        ("Say \\\"hi\\\"", "Say \"hi\""),
        ("Clients/R&-D", " R&D "),
        ("Tab\tbed", "Tab\tbed"),
    ] {
        let mut folders = rail();
        folders.push(folder(name, &[]));
        assert_eq!(renamed_to(&folder(name, &[]), Some(typed), &folders).unwrap(), name, "{name}");
    }
}

#[test]
fn a_folders_own_name_is_its_last_part_as_a_person_reads_it() {
    assert_eq!(own_name("R&-D", Some("/")), "R&D");
    assert_eq!(own_name("Folders/&AMk-t&AOk-", Some("/")), "Été");
    assert_eq!(own_name("INBOX.Work", Some(".")), "Work");
    assert_eq!(own_name("a\\\"b", None), "a\"b");
    assert_eq!(from_modified_utf7("&-&--x&ZeVnLIqe-"), "&&-x日本語");
    assert_eq!(from_modified_utf7("&2D3eQg-"), "🙂");
    assert_eq!(from_modified_utf7("broken &!!- and &"), "broken &!!- and &");
}

#[test]
fn a_new_folder_is_at_the_top_by_a_name_not_taken() {
    let rail = rail();
    assert_eq!(new_name(Some("Receipts"), &rail).unwrap(), "Receipts");
    assert_eq!(new_name(Some("Receipts"), &[folder("INBOX", &[]), folder("Clients", &[])]).unwrap(), "Receipts");
    for taken in ["Archive", "inbox"] {
        assert_eq!(said(new_name(Some(taken), &rail).unwrap_err()), (Kind::Conflict, TAKEN.to_owned()));
    }
}

#[test]
fn a_new_folder_goes_where_the_account_keeps_a_persons_own() {
    assert_eq!(new_name(Some("Receipts"), &bridge()).unwrap(), "Folders/Receipts");
    assert_eq!(new_name(Some("Receipts"), &courier()).unwrap(), "INBOX.Receipts");
    assert_eq!(new_name(Some("Bills"), &gmail()).unwrap(), "Bills");
    assert_eq!(said(new_name(Some("Clients"), &bridge()).unwrap_err()), (Kind::Conflict, TAKEN.to_owned()));
    assert_eq!(said(new_name(Some("Folders/Receipts"), &bridge()).unwrap_err()).0, Kind::Refused);
}

#[test]
fn a_renamed_folders_stored_names_follow_it_with_the_folders_inside() {
    assert_eq!(moved("Clients", "Clients", "Customers", Some("/")).as_deref(), Some("Customers"));
    assert_eq!(moved("Clients/2026", "Clients", "Customers", Some("/")).as_deref(), Some("Customers/2026"));
    assert_eq!(moved("Clientsville", "Clients", "Customers", Some("/")), None);
    assert_eq!(moved("Clients/2026", "Clients", "Customers", None), None);
}

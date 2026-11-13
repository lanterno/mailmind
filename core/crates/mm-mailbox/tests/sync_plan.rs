//! What a sync of one folder asks the server for, and the flags of a message as the columns hold them.

use mm_mailbox::session::Search;
use mm_mailbox::sync_plan::{CUT, WHOLE, columns_of, plan_flags, plan_folder, reads_bodies};

#[test]
fn a_folder_never_read_or_renumbered_is_read_whole() {
    let first = plan_folder(Some(7), None, 0, false, None);
    assert!(first.whole && !first.reset && !first.read_flags);
    assert_eq!(first.search(), Search::All);

    let renumbered = plan_folder(Some(8), Some(7), 40, false, None);
    assert!(renumbered.reset && renumbered.whole);

    let asked = plan_folder(Some(7), Some(7), 40, true, None);
    assert!(asked.whole && !asked.reset);
}

#[test]
fn a_folder_read_before_is_read_above_its_watermark() {
    let plan = plan_folder(Some(7), Some(7), 40, false, None);
    assert!(!plan.whole && plan.read_flags);
    assert_eq!(plan.search(), Search::Above(40));
    // N:* always matches the last UID, even one below N.
    assert_eq!(plan.to_fetch(vec![38, 41, 42]), (vec![41, 42], None));
    assert!(plan.arrives_in("INBOX") && !plan.arrives_in("Archive"));
    let zero = plan_folder(Some(0), Some(7), 40, false, None);
    assert!(!zero.reset, "a UIDVALIDITY of 0 says nothing");
}

#[test]
fn a_whole_scan_says_whether_it_read_everything() {
    let plan = plan_folder(None, None, 0, false, Some(2));
    assert_eq!(plan.to_fetch(vec![1, 2, 3]), (vec![2, 3], Some(CUT)));
    assert_eq!(plan.to_fetch(vec![1, 2]), (vec![1, 2], Some(WHOLE)));
    let unbounded = plan_folder(None, None, 0, false, Some(0));
    assert_eq!(unbounded.to_fetch(vec![1, 2, 3]), (vec![1, 2, 3], Some(WHOLE)));
}

#[test]
fn stored_flags_are_read_by_mod_sequence_where_the_server_keeps_one() {
    let none = plan_flags(None, Some(5));
    assert_eq!((none.unchanged, none.changed_since, none.mark(false, false)), (false, None, None));
    assert!(plan_flags(Some(9), Some(9)).unchanged);
    let since = plan_flags(Some(9), Some(5));
    assert_eq!(since.changed_since, Some(5));
    assert_eq!(since.mark(false, true), Some(9));
    assert_eq!(since.mark(false, false), None, "a refused search leaves the mark");
    assert_eq!(since.mark(true, true), None, "a row held back leaves the mark");
    let ahead = plan_flags(Some(9), Some(12));
    assert_eq!((ahead.changed_since, ahead.mark(false, false)), (None, Some(9)));
}

#[test]
fn flags_are_read_whatever_case_the_server_wrote() {
    let said = ["\\SEEN".to_owned(), "\\flagged".to_owned(), "$Junk".to_owned()];
    assert_eq!(columns_of(&said), [1, 1, 0, 0, 0]);
    assert!(reads_bodies("inbox") && !reads_bodies("Sent"));
}

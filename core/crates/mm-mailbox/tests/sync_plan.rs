//! What a sync of one folder asks the server for, the steps it stores what it found in, and the flags of a message as
//! the columns hold them.

use mm_mailbox::session::Search;
use mm_mailbox::sync_plan::{
    CHUNK, CUT, Scan, Step, WHOLE, columns_of, older_steps, plan_flags, plan_folder, reads_bodies, still_to_say,
};

fn uids(from: u32, to: u32) -> Vec<u32> {
    (from..=to).collect()
}

#[test]
fn a_folder_never_read_or_renumbered_is_read_whole() {
    let first = plan_folder(Some(7), None, 0, Scan::Never, false, None);
    assert!(first.whole && !first.reset && !first.read_flags);
    assert_eq!(first.search(), Search::All);

    let renumbered = plan_folder(Some(8), Some(7), 40, Scan::Whole, false, None);
    assert!(renumbered.reset && renumbered.whole);

    let asked = plan_folder(Some(7), Some(7), 40, Scan::Whole, true, None);
    assert!(asked.whole && !asked.reset);
}

#[test]
fn a_folder_no_scan_has_begun_is_read_as_a_first_scan_though_it_has_a_watermark() {
    // A quit part way through a first scan, as before a step stored the watermark and the scan_state at once.
    let left = plan_folder(Some(7), Some(7), 40, Scan::Never, false, None);
    assert!(left.whole && !left.reset && !left.read_flags);
    assert_eq!(left.search(), Search::All);
    assert!(!left.arrives_in("INBOX"), "what was there is no new mail");
    // It stores the newest now and leaves the rest to the background, as any first scan does, so it has begun.
    let [step] = left.steps(uids(1, CHUNK as u32 + 10)).try_into().unwrap();
    assert_eq!(step.leaves, Some(Scan::Down(11)));
    assert!(step.leaves.unwrap().begun());
    // A scan cut to the newest N has begun none of that, and still reads above its watermark.
    assert!(!plan_folder(Some(7), Some(7), 40, Scan::Cut, false, None).whole);
}

#[test]
fn a_folder_read_before_is_read_above_its_watermark() {
    let plan = plan_folder(Some(7), Some(7), 40, Scan::Whole, false, None);
    assert!(!plan.whole && plan.read_flags);
    assert_eq!(plan.search(), Search::Above(40));
    // N:* always matches the last UID, even one below N.
    assert_eq!(plan.to_fetch(vec![38, 41, 42]), (vec![41, 42], None));
    assert!(plan.arrives_in("INBOX") && !plan.arrives_in("Archive"));
    let zero = plan_folder(Some(0), Some(7), 40, Scan::Whole, false, None);
    assert!(!zero.reset, "a UIDVALIDITY of 0 says nothing");
}

#[test]
fn a_whole_scan_says_whether_it_read_everything() {
    let plan = plan_folder(None, None, 0, Scan::Never, false, Some(2));
    assert_eq!(plan.to_fetch(vec![1, 2, 3]), (vec![2, 3], Some(CUT)));
    assert_eq!(plan.to_fetch(vec![1, 2]), (vec![1, 2], Some(WHOLE)));
    let unbounded = plan_folder(None, None, 0, Scan::Never, false, Some(0));
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

#[test]
fn only_new_inbox_mail_is_arrival() {
    let incremental = plan_folder(Some(7), Some(7), 40, Scan::Whole, false, None);
    assert!(incremental.arrives_in("INBOX") && incremental.arrives_in("inbox"));
    assert!(!incremental.arrives_in("Archive"), "another folder isn't arrival");
    assert!(
        !plan_folder(Some(7), Some(7), 40, Scan::Whole, true, None).arrives_in("INBOX"),
        "a whole scan reads what was there"
    );
    assert!(!plan_folder(Some(7), None, 0, Scan::Never, false, None).arrives_in("INBOX"), "so does a first one");
}

#[test]
fn a_filter_says_only_what_the_row_lacks() {
    let wanted = [("flagged", true), ("seen", true)];
    assert_eq!(still_to_say(&wanted, &[1, 0, 0, 0, 0]), [("flagged", true)]);
    assert!(still_to_say(&wanted, &[1, 1, 0, 0, 0]).is_empty());
    assert_eq!(still_to_say(&wanted, &[0; 5]), wanted);
    assert_eq!(still_to_say(&[("seen", false)], &[1, 0, 0, 0, 0]), [("seen", false)]);
}

#[test]
fn what_a_folders_scans_have_read_is_kept_as_its_column_holds_it() {
    for scan in [Scan::Never, Scan::Cut, Scan::Down(701), Scan::Whole] {
        assert_eq!(Scan::read(Some(&scan.state())), scan);
    }
    assert_eq!(
        (Scan::Whole.state(), Scan::Cut.state(), Scan::Down(701).state()),
        (WHOLE.into(), CUT.into(), "down-to:701".into())
    );
    assert_eq!(Scan::read(None), Scan::Never);
    assert_eq!(Scan::read(Some("down-to:")), Scan::Never, "a state it doesn't know reads as never read");
    assert!(Scan::Down(1).begun() && Scan::Whole.begun() && !Scan::Cut.begun() && !Scan::Never.begun());
}

#[test]
fn a_first_scan_stores_the_newest_chunk_now_and_leaves_the_rest_below_it() {
    let plan = plan_folder(Some(7), None, 0, Scan::Never, false, None);
    let steps = plan.steps(uids(1, 1200));
    assert_eq!(steps, [Step { uids: uids(701, 1200), watermark: 1200, leaves: Some(Scan::Down(701)), holds: None }]);
    assert_eq!(CHUNK, 500);
    // A folder no bigger than a chunk is read whole at once.
    let small = plan.steps(uids(1, 40));
    assert_eq!(small, [Step { uids: uids(1, 40), watermark: 40, leaves: Some(Scan::Whole), holds: None }]);
    // An empty one is read whole too.
    assert_eq!(plan.steps(Vec::new()), [Step { leaves: Some(Scan::Whole), ..Step::default() }]);
}

#[test]
fn a_folder_still_being_read_is_caught_up_above_its_watermark_even_when_asked_whole() {
    let plan = plan_folder(Some(7), Some(7), 1200, Scan::Down(701), true, None);
    assert!(!plan.whole && plan.read_flags);
    assert_eq!(plan.search(), Search::Above(1200));
    assert!(!plan_folder(Some(7), Some(7), 1200, Scan::Cut, true, None).steps(uids(1, 3)).is_empty());
    // A renumbered one is read afresh, whatever it had read.
    let renumbered = plan_folder(Some(8), Some(7), 1200, Scan::Down(701), false, None);
    assert!(renumbered.reset && renumbered.whole && renumbered.scan == Scan::Never);
}

#[test]
fn new_mail_is_stored_oldest_first_a_chunk_a_step_the_watermark_rising_with_each() {
    let plan = plan_folder(Some(7), Some(7), 1200, Scan::Whole, false, None);
    let steps = plan.steps(uids(1200, 2300));
    let shape: Vec<(u32, u32, u32)> = steps.iter().map(|s| (s.uids[0], *s.uids.last().unwrap(), s.watermark)).collect();
    assert_eq!(shape, [(1201, 1700, 1700), (1701, 2200, 2200), (2201, 2300, 2300)]);
    assert!(steps.iter().all(|s| s.leaves.is_none() && s.holds.is_none()));
    // Nothing new is still one step, which keeps the sync's time.
    assert_eq!(plan.steps(vec![1200]), [Step::default()]);
}

#[test]
fn a_scan_cut_to_the_newest_stores_them_newest_first_and_says_it_was_cut() {
    let plan = plan_folder(None, None, 0, Scan::Never, false, Some(600));
    let steps = plan.steps(uids(1, 1000));
    let shape: Vec<(u32, u32, Option<Scan>)> = steps.iter().map(|s| (s.uids[0], s.watermark, s.leaves)).collect();
    assert_eq!(shape, [(501, 1000, Some(Scan::Cut)), (401, 1000, Some(Scan::Cut))]);
    let whole = plan.steps(uids(1, 600));
    let shape: Vec<(u32, Option<Scan>)> = whole.iter().map(|s| (s.uids[0], s.leaves)).collect();
    assert_eq!(shape, [(101, Some(Scan::Cut)), (1, Some(Scan::Whole))]);
}

#[test]
fn older_mail_is_read_newest_first_each_step_held_to_where_the_last_left_off() {
    let steps = older_steps(701, &uids(1, 1200), 4);
    let shape: Vec<(u32, u32, Option<Scan>, Option<Scan>)> =
        steps.iter().map(|s| (s.uids[0], *s.uids.last().unwrap(), s.holds, s.leaves)).collect();
    assert_eq!(
        shape,
        [(201, 700, Some(Scan::Down(701)), Some(Scan::Down(201))), (1, 200, Some(Scan::Down(201)), Some(Scan::Whole))]
    );
    assert!(steps.iter().all(|s| s.watermark == 0), "older mail leaves the watermark");
    // No more than asked for, the rest left for the next round.
    let first = older_steps(2501, &uids(1, 2500), 2);
    let shape: Vec<(u32, Option<Scan>)> = first.iter().map(|s| (s.uids[0], s.leaves)).collect();
    assert_eq!(shape, [(2001, Some(Scan::Down(2001))), (1501, Some(Scan::Down(1501)))]);
    // Nothing below: the folder is read whole.
    assert_eq!(
        older_steps(5, &[], 4),
        [Step { leaves: Some(Scan::Whole), holds: Some(Scan::Down(5)), ..Step::default() }]
    );
}

//! The body backfill (`Syncing.backfill_round` and `domain.body_backfill`): each account in turn at about four dozen
//! messages a round, newest first, a folder's dozen in a FETCH; none older than twelve months; a busy gate ending only
//! its account's turn; a lost connection resting its account for a minute and setting each message it fell on aside a
//! little longer each time, each alone from then on; a body not given waiting six hours; and when the next round is
//! due. Against a store and a fetch in memory, on a clock the test moves.

#![allow(clippy::unwrap_used)]

use std::collections::{HashMap, HashSet};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use mm_kernel::errors::Fault;
use mm_kernel::seams::{Clock, Moment};
use mm_mailbox::backfill::{Backfill, BackfillParts};
use mm_mailbox::backfill_ports::{BackfillFetch, BodiesWanted, Tried, Wanted};
use mm_mailbox::body_backfill::{
    self, BACKLOG_WAIT, BATCH, Batch, CAUGHT_UP_WAIT, NOT_GIVEN_WAIT, REACH, ROUND, SetAside, UNREACHABLE_REST,
};
use mm_mailbox::command_ports::{MoveAccount, MoveAccounts};

const ADA: &str = "ada@example.test";
const BEA: &str = "bea@example.test";
const NOW: i64 = 1_793_620_800_000_000;
const SECOND: i64 = 1_000_000;
const DAY: i64 = 86_400;

#[derive(Debug)]
struct Moving(Mutex<i64>);

impl Moving {
    fn shift(&self, seconds: i64) {
        *self.0.lock().unwrap() += seconds * SECOND;
    }
}

impl Clock for Moving {
    fn now(&self) -> Moment {
        Moment::from_micros(*self.0.lock().unwrap())
    }
}

#[derive(Debug)]
struct Accounts(Vec<&'static str>);

impl MoveAccounts for Accounts {
    fn move_account(&self, _email: &str) -> Result<Option<MoveAccount>, Fault> {
        Ok(None)
    }

    fn emails(&self) -> Result<Vec<String>, Fault> {
        Ok(self.0.iter().map(|e| (*e).to_owned()).collect())
    }
}

/// What the server does when a message's body is asked for.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Server {
    Gives,
    GivesNothing,
    /// The connection goes over it, and nothing of its batch is kept.
    Lost,
}

/// One message: its pk, its folder and how many days old it is.
type Message = (i64, &'static str, i64);

/// Each account's mail, newest first, which bodies are kept, and what the server does for each pk; with each try
/// written down, and the accounts whose gate is held.
#[derive(Debug, Default)]
struct Mail {
    of: Mutex<Vec<(String, Vec<Message>)>>,
    kept: Mutex<HashSet<i64>>,
    server: Mutex<HashMap<i64, Server>>,
    held: Mutex<HashSet<String>>,
    tried: Mutex<Vec<(String, String, Vec<i64>)>>,
}

impl Mail {
    fn new(accounts: &[(&str, Vec<Message>)]) -> Arc<Mail> {
        let mail = Mail::default();
        *mail.of.lock().unwrap() = accounts.iter().map(|(a, mail)| ((*a).to_owned(), mail.clone())).collect();
        Arc::new(mail)
    }

    fn server(&self, pk: i64, does: Server) {
        self.server.lock().unwrap().insert(pk, does);
    }

    /// Each batch tried since last asked: its account, folder and pks.
    fn tried(&self) -> Vec<(String, String, Vec<i64>)> {
        std::mem::take(&mut *self.tried.lock().unwrap())
    }

    /// The pks of each batch tried since last asked.
    fn tried_pks(&self) -> Vec<Vec<i64>> {
        self.tried().into_iter().map(|(_, _, pks)| pks).collect()
    }
}

/// `pks` in the Inbox, a day old each.
fn inbox(pks: std::ops::RangeInclusive<i64>) -> Vec<Message> {
    pks.map(|pk| (pk, "INBOX", 1)).collect()
}

impl BodiesWanted for Mail {
    fn bodies_to_fetch(&self, account: &str, skip: &[i64], since: i64, limit: usize) -> Result<Vec<Wanted>, Fault> {
        let kept = self.kept.lock().unwrap();
        let of = self.of.lock().unwrap();
        let mail = of.iter().find(|(a, _)| a == account).map(|(_, mail)| mail.clone()).unwrap_or_default();
        Ok(mail
            .into_iter()
            .filter(|(pk, _, days)| !kept.contains(pk) && !skip.contains(pk) && NOW / SECOND - days * DAY >= since)
            .take(limit)
            .map(|(pk, folder, _)| Wanted { pk, folder: folder.to_owned(), uid: u32::try_from(pk).unwrap() })
            .collect())
    }

    fn still_without_body(&self, pks: &[i64]) -> Result<Vec<i64>, Fault> {
        let kept = self.kept.lock().unwrap();
        Ok(pks.iter().copied().filter(|pk| !kept.contains(pk)).collect())
    }
}

impl BackfillFetch for Mail {
    fn try_bodies(&self, account: &str, folder: &str, messages: &[(i64, u32)]) -> Tried {
        let pks: Vec<i64> = messages.iter().map(|(pk, _)| *pk).collect();
        assert!(messages.iter().all(|(pk, uid)| i64::from(*uid) == *pk), "each with its UID");
        self.tried.lock().unwrap().push((account.to_owned(), folder.to_owned(), pks.clone()));
        if self.held.lock().unwrap().contains(account) {
            return Tried::Busy;
        }
        let server = self.server.lock().unwrap();
        let does = |pk: &i64| server.get(pk).copied().unwrap_or(Server::Gives);
        if pks.iter().any(|pk| does(pk) == Server::Lost) {
            return Tried::Unreachable;
        }
        self.kept.lock().unwrap().extend(pks.iter().copied().filter(|pk| does(pk) == Server::Gives));
        Tried::Ran
    }
}

fn backfill(mail: &Arc<Mail>, accounts: &[&'static str]) -> (Backfill, Arc<Moving>) {
    let clock = Arc::new(Moving(Mutex::new(NOW)));
    let backfill = Backfill::new(BackfillParts {
        accounts: Arc::new(Accounts(accounts.to_vec())),
        wanted: mail.clone(),
        fetch: mail.clone(),
        clock: clock.clone(),
        pause: Duration::ZERO,
    });
    (backfill, clock)
}

fn range(from: i64, to: i64) -> Vec<i64> {
    (from..=to).collect()
}

// The policy.

#[test]
fn a_round_takes_each_accounts_first_then_each_ones_second_until_its_limit_is_reached() {
    let waiting = [vec!["a1", "a2", "a3"], vec![], vec!["c1"], vec!["d1", "d2"]];
    assert_eq!(body_backfill::in_turn(&waiting, 12, |_| 1), ["a1", "c1", "d1", "a2", "d2", "a3"]);
    assert_eq!(body_backfill::in_turn(&waiting, 4, |_| 1), ["a1", "c1", "d1", "a2"]);
    assert_eq!(body_backfill::in_turn::<&str>(&[], ROUND, |_| 1), Vec::<&str>::new());
    // Counted by size, the one that reaches the limit is the last.
    let sized: [Vec<usize>; 2] = [vec![10, 10], vec![5]];
    assert_eq!(body_backfill::in_turn(&sized, 16, |n| *n), [10, 5, 10]);
    assert_eq!(body_backfill::in_turn(&sized, 15, |n| *n), [10, 5]);
    assert_eq!(body_backfill::in_turn(&sized, 0, |n| *n), Vec::<usize>::new());
}

#[test]
fn a_folders_messages_go_a_dozen_together_the_batch_with_the_newest_first() {
    let wanted = |pk: i64, folder: &str| Wanted { pk, folder: folder.to_owned(), uid: u32::try_from(pk).unwrap() };
    let mut mail = vec![wanted(1, "INBOX"), wanted(2, "Archive"), wanted(3, "INBOX")];
    mail.extend((4..=16).map(|pk| wanted(pk, "Archive")));
    let batches = body_backfill::batches(&mail, &HashSet::new());
    let shape: Vec<(String, Vec<i64>)> =
        batches.iter().map(|b| (b.folder.clone(), b.messages.iter().map(|(pk, _)| *pk).collect())).collect();
    let archive: Vec<i64> = [2].into_iter().chain(4..=14).collect();
    assert_eq!(
        shape,
        [("INBOX".to_owned(), vec![1, 3]), ("Archive".to_owned(), archive), ("Archive".to_owned(), vec![15, 16])]
    );
    assert_eq!(batches[0], Batch { folder: "INBOX".to_owned(), messages: vec![(1, 1), (3, 3)] });
    assert_eq!(BATCH, 12);
    // What a lost connection fell on goes alone, and the rest of its folder together still.
    let alone = body_backfill::batches(&mail[..4], &HashSet::from([3]));
    let shape: Vec<Vec<i64>> = alone.iter().map(|b| b.messages.iter().map(|(pk, _)| *pk).collect()).collect();
    assert_eq!(shape, [vec![1], vec![2, 4], vec![3]]);
}

#[test]
fn a_lost_connection_sets_its_message_aside_a_minute_then_twice_that_each_time_up_to_six_hours() {
    let mut was = None;
    let mut waits = Vec::new();
    for _ in 0..12 {
        let one = body_backfill::connection_lost(was, NOW);
        waits.push((one.until - NOW) / SECOND);
        was = Some(one);
    }
    assert_eq!(waits, [60, 120, 240, 480, 960, 1920, 3840, 7680, 15360, 21600, 21600, 21600]);
    assert_eq!(was.unwrap().connections_lost, 12);
    // Past any count a wait could double from, it stays at six hours.
    let many = body_backfill::connection_lost(Some(SetAside { until: 0, connections_lost: 400 }), NOW);
    assert_eq!(many.until - NOW, NOT_GIVEN_WAIT * SECOND);
    assert_eq!(body_backfill::not_given(NOW), SetAside { until: NOW + NOT_GIVEN_WAIT * SECOND, connections_lost: 0 });
}

#[test]
fn a_round_leaves_out_only_what_still_waits() {
    let set_aside = HashMap::from([
        (1, SetAside { until: NOW + 1, connections_lost: 0 }),
        (2, SetAside { until: NOW, connections_lost: 3 }),
        (3, SetAside { until: NOW + 60 * SECOND, connections_lost: 1 }),
    ]);
    assert_eq!(body_backfill::still_waiting(&set_aside, NOW), [1, 3]);
}

#[test]
fn the_next_round_comes_soon_while_mail_is_left_after_a_rest_and_else_in_ten_minutes() {
    assert_eq!(body_backfill::next_round(true, true), BACKLOG_WAIT);
    assert_eq!(body_backfill::next_round(true, false), 8);
    assert_eq!(body_backfill::next_round(false, true), UNREACHABLE_REST);
    assert_eq!(body_backfill::next_round(false, false), CAUGHT_UP_WAIT);
    assert_eq!(CAUGHT_UP_WAIT, 600);
}

// A round.

#[test]
fn a_round_asks_each_account_in_turn_a_dozen_a_fetch_about_four_dozen_in_all_then_catches_up() {
    let mail = Mail::new(&[(ADA, inbox(1..=60)), (BEA, inbox(101..=105))]);
    let (backfill, _) = backfill(&mail, &[ADA, BEA]);
    assert_eq!(backfill.round().unwrap(), BACKLOG_WAIT);
    assert_eq!(
        mail.tried_pks(),
        [range(1, 12), range(101, 105), range(13, 24), range(25, 36), range(37, 48)],
        "53 asked, the batch that passed 48 the last"
    );
    assert_eq!(backfill.round().unwrap(), BACKLOG_WAIT);
    assert_eq!(mail.tried_pks(), [range(49, 60)]);
    assert_eq!(backfill.round().unwrap(), CAUGHT_UP_WAIT);
    assert!(mail.tried().is_empty());
}

#[test]
fn each_folders_mail_is_asked_for_in_its_own_fetch() {
    let mail = Mail::new(&[(ADA, vec![(1, "INBOX", 1), (2, "Archive", 2), (3, "INBOX", 3), (4, "Sent", 4)])]);
    let (backfill, _) = backfill(&mail, &[ADA]);
    backfill.round().unwrap();
    let tried: Vec<(String, Vec<i64>)> = mail.tried().into_iter().map(|(_, folder, pks)| (folder, pks)).collect();
    assert_eq!(
        tried,
        [("INBOX".to_owned(), vec![1, 3]), ("Archive".to_owned(), vec![2]), ("Sent".to_owned(), vec![4])]
    );
}

#[test]
fn mail_older_than_twelve_months_is_left_to_be_opened() {
    let year = REACH / DAY;
    assert_eq!(year, 365);
    let mail =
        Mail::new(&[(ADA, vec![(1, "INBOX", 1), (2, "INBOX", year - 1), (3, "INBOX", year + 1), (4, "INBOX", 900)])]);
    let (backfill, _) = backfill(&mail, &[ADA]);
    backfill.round().unwrap();
    assert_eq!(mail.tried_pks(), [vec![1, 2]]);
    assert_eq!(backfill.round().unwrap(), CAUGHT_UP_WAIT);
    assert!(mail.tried().is_empty());
}

#[test]
fn a_busy_gate_ends_only_its_accounts_turn() {
    let mut ada = inbox(1..=3);
    ada.push((4, "Archive", 4));
    let mail = Mail::new(&[(ADA, ada), (BEA, inbox(21..=23))]);
    mail.held.lock().unwrap().insert(ADA.to_owned());
    let (backfill, _) = backfill(&mail, &[ADA, BEA]);
    assert_eq!(backfill.round().unwrap(), BACKLOG_WAIT);
    assert_eq!(mail.tried_pks(), [range(1, 3), range(21, 23)], "Ada's Archive waits for the next round");
    // Nothing is set aside: the next round asks again from the newest.
    mail.held.lock().unwrap().clear();
    backfill.round().unwrap();
    assert_eq!(mail.tried_pks(), [range(1, 3), vec![4]]);
}

#[test]
fn a_lost_connection_rests_its_account_a_minute_and_each_message_it_fell_on_goes_alone_after() {
    let mail = Mail::new(&[(ADA, inbox(1..=3)), (BEA, inbox(21..=21))]);
    mail.server(2, Server::Lost);
    let (backfill, clock) = backfill(&mail, &[ADA, BEA]);
    // Ada's turn ends where the connection went; Bea's comes all the same.
    assert_eq!(backfill.round().unwrap(), BACKLOG_WAIT);
    assert_eq!(mail.tried_pks(), [range(1, 3), vec![21]]);
    // Ada rests, and Bea has nothing left: the next round is when the rest ends.
    assert_eq!(backfill.round().unwrap(), UNREACHABLE_REST);
    clock.shift(59);
    backfill.round().unwrap();
    assert!(mail.tried().is_empty());
    // A minute on, each is tried alone: the first comes, and the connection goes over the second again, so it waits
    // two minutes now, and Ada rests another.
    clock.shift(1);
    backfill.round().unwrap();
    assert_eq!(mail.tried_pks(), [vec![1], vec![2]]);
    clock.shift(60);
    assert_eq!(backfill.round().unwrap(), BACKLOG_WAIT);
    assert_eq!(mail.tried_pks(), [vec![3]], "the one behind it isn't held back");
    clock.shift(60);
    mail.server(2, Server::Gives);
    assert_eq!(backfill.round().unwrap(), BACKLOG_WAIT);
    assert_eq!(mail.tried_pks(), [vec![2]]);
    assert_eq!(backfill.round().unwrap(), CAUGHT_UP_WAIT);
}

#[test]
fn a_body_not_given_waits_six_hours_while_the_rest_of_its_batch_is_kept() {
    let mail = Mail::new(&[(ADA, inbox(1..=3))]);
    mail.server(2, Server::GivesNothing);
    let (backfill, clock) = backfill(&mail, &[ADA]);
    backfill.round().unwrap();
    assert_eq!(mail.tried_pks(), [range(1, 3)]);
    assert_eq!(*mail.kept.lock().unwrap(), HashSet::from([1, 3]));
    assert_eq!(backfill.round().unwrap(), CAUGHT_UP_WAIT);
    assert!(mail.tried().is_empty());
    clock.shift(NOT_GIVEN_WAIT - 1);
    backfill.round().unwrap();
    assert!(mail.tried().is_empty());
    clock.shift(1);
    mail.server(2, Server::Gives);
    backfill.round().unwrap();
    assert_eq!(mail.tried_pks(), [vec![2]]);
}

#[test]
fn a_message_set_aside_and_opened_meanwhile_is_let_go() {
    let mail = Mail::new(&[(ADA, inbox(1..=1))]);
    mail.server(1, Server::GivesNothing);
    let (backfill, clock) = backfill(&mail, &[ADA]);
    backfill.round().unwrap();
    assert_eq!(mail.tried_pks(), [vec![1]]);
    mail.kept.lock().unwrap().insert(1);
    clock.shift(NOT_GIVEN_WAIT);
    // Its body is kept now: nothing is asked once its wait would have ended.
    assert_eq!(backfill.round().unwrap(), CAUGHT_UP_WAIT);
    assert!(mail.tried().is_empty());
}

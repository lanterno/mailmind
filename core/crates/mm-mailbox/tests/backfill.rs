//! The body backfill (`Syncing.backfill_round` and `domain.body_backfill`): each account in turn at a round's dozen,
//! newest first; a busy gate ending only its account's turn; a lost connection resting its account for a minute and
//! setting its message aside a little longer each time; a body not given waiting six hours; and when the next round is
//! due. Against a store and a fetch in memory, on a clock the test moves.

#![allow(clippy::unwrap_used)]

use std::collections::{HashMap, HashSet};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use mm_kernel::errors::Fault;
use mm_kernel::seams::{Clock, Moment};
use mm_mailbox::backfill::{Backfill, BackfillParts};
use mm_mailbox::backfill_ports::{BackfillFetch, BodiesWanted, Tried};
use mm_mailbox::body_backfill::{
    self, BACKLOG_WAIT, CAUGHT_UP_WAIT, NOT_GIVEN_WAIT, ROUND, SetAside, UNREACHABLE_REST,
};
use mm_mailbox::command_ports::{MoveAccount, MoveAccounts};

const ADA: &str = "ada@example.test";
const BEA: &str = "bea@example.test";
const NOW: i64 = 1_793_620_800_000_000;
const SECOND: i64 = 1_000_000;

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
    Lost,
}

/// Each account's mail, newest first, which bodies are kept, and what the server does for each pk; with each try
/// written down, and the accounts whose gate is held.
#[derive(Debug, Default)]
struct Mail {
    of: Mutex<Vec<(String, Vec<i64>)>>,
    kept: Mutex<HashSet<i64>>,
    server: Mutex<HashMap<i64, Server>>,
    held: Mutex<HashSet<String>>,
    tried: Mutex<Vec<(String, i64)>>,
}

impl Mail {
    fn new(accounts: &[(&str, std::ops::RangeInclusive<i64>)]) -> Arc<Mail> {
        let mail = Mail::default();
        *mail.of.lock().unwrap() = accounts.iter().map(|(a, pks)| ((*a).to_owned(), pks.clone().collect())).collect();
        Arc::new(mail)
    }

    fn server(&self, pk: i64, does: Server) {
        self.server.lock().unwrap().insert(pk, does);
    }

    fn tried(&self) -> Vec<(String, i64)> {
        std::mem::take(&mut *self.tried.lock().unwrap())
    }

    fn tried_pks(&self) -> Vec<i64> {
        self.tried().into_iter().map(|(_, pk)| pk).collect()
    }
}

impl BodiesWanted for Mail {
    fn bodies_to_fetch(&self, account: &str, skip: &[i64], limit: usize) -> Result<Vec<i64>, Fault> {
        let kept = self.kept.lock().unwrap();
        let of = self.of.lock().unwrap();
        let pks = of.iter().find(|(a, _)| a == account).map(|(_, pks)| pks.clone()).unwrap_or_default();
        Ok(pks.into_iter().filter(|pk| !kept.contains(pk) && !skip.contains(pk)).take(limit).collect())
    }

    fn still_without_body(&self, pks: &[i64]) -> Result<Vec<i64>, Fault> {
        let kept = self.kept.lock().unwrap();
        Ok(pks.iter().copied().filter(|pk| !kept.contains(pk)).collect())
    }
}

impl BackfillFetch for Mail {
    fn try_body(&self, account: &str, pk: i64) -> Tried {
        self.tried.lock().unwrap().push((account.to_owned(), pk));
        if self.held.lock().unwrap().contains(account) {
            return Tried::Busy;
        }
        match self.server.lock().unwrap().get(&pk).copied().unwrap_or(Server::Gives) {
            Server::Gives => {
                self.kept.lock().unwrap().insert(pk);
                Tried::Ran
            }
            Server::GivesNothing => Tried::Ran,
            Server::Lost => Tried::Unreachable,
        }
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

// The policy.

#[test]
fn a_round_takes_each_accounts_first_then_each_ones_second_up_to_its_limit() {
    let waiting = [vec!["a1", "a2", "a3"], vec![], vec!["c1"], vec!["d1", "d2"]];
    assert_eq!(body_backfill::in_turn(&waiting, 12), ["a1", "c1", "d1", "a2", "d2", "a3"]);
    assert_eq!(body_backfill::in_turn(&waiting, 4), ["a1", "c1", "d1", "a2"]);
    assert_eq!(body_backfill::in_turn::<&str>(&[], ROUND), Vec::<&str>::new());
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
fn a_round_asks_each_account_in_turn_newest_first_a_dozen_at_most_then_catches_up() {
    let mail = Mail::new(&[(ADA, 1..=10), (BEA, 21..=25)]);
    let (backfill, _) = backfill(&mail, &[ADA, BEA]);
    assert_eq!(backfill.round().unwrap(), BACKLOG_WAIT);
    assert_eq!(mail.tried_pks(), [1, 21, 2, 22, 3, 23, 4, 24, 5, 25, 6, 7]);
    assert_eq!(backfill.round().unwrap(), BACKLOG_WAIT);
    assert_eq!(mail.tried_pks(), [8, 9, 10]);
    assert_eq!(backfill.round().unwrap(), CAUGHT_UP_WAIT);
    assert_eq!(mail.tried_pks(), Vec::<i64>::new());
}

#[test]
fn a_busy_gate_ends_only_its_accounts_turn() {
    let mail = Mail::new(&[(ADA, 1..=3), (BEA, 21..=23)]);
    mail.held.lock().unwrap().insert(ADA.to_owned());
    let (backfill, _) = backfill(&mail, &[ADA, BEA]);
    assert_eq!(backfill.round().unwrap(), BACKLOG_WAIT);
    assert_eq!(mail.tried(), [(ADA.to_owned(), 1), (BEA.to_owned(), 21), (BEA.to_owned(), 22), (BEA.to_owned(), 23)]);
    // Nothing is set aside: the next round asks again from the newest.
    mail.held.lock().unwrap().clear();
    backfill.round().unwrap();
    assert_eq!(mail.tried_pks(), [1, 2, 3]);
}

#[test]
fn a_lost_connection_rests_its_account_a_minute_and_its_message_waits_longer_each_time() {
    let mail = Mail::new(&[(ADA, 1..=2), (BEA, 21..=21)]);
    mail.server(1, Server::Lost);
    let (backfill, clock) = backfill(&mail, &[ADA, BEA]);
    // Ada's turn ends where the connection went; Bea's comes all the same.
    assert_eq!(backfill.round().unwrap(), BACKLOG_WAIT);
    assert_eq!(mail.tried_pks(), [1, 21]);
    // Ada rests, and Bea has nothing left: the next round is when the rest ends.
    assert_eq!(backfill.round().unwrap(), UNREACHABLE_REST);
    clock.shift(59);
    backfill.round().unwrap();
    assert_eq!(mail.tried_pks(), Vec::<i64>::new());
    // A minute on, the message is tried again, and lost again: now it waits two minutes, and Ada rests a minute.
    clock.shift(1);
    backfill.round().unwrap();
    assert_eq!(mail.tried_pks(), [1]);
    clock.shift(60);
    assert_eq!(backfill.round().unwrap(), BACKLOG_WAIT);
    assert_eq!(mail.tried_pks(), [2]);
    clock.shift(60);
    mail.server(1, Server::Gives);
    assert_eq!(backfill.round().unwrap(), BACKLOG_WAIT);
    assert_eq!(mail.tried_pks(), [1]);
    assert_eq!(backfill.round().unwrap(), CAUGHT_UP_WAIT);
}

#[test]
fn a_body_not_given_waits_six_hours_while_the_mail_behind_it_is_asked_for() {
    let mail = Mail::new(&[(ADA, 1..=2)]);
    mail.server(1, Server::GivesNothing);
    mail.server(2, Server::GivesNothing);
    let (backfill, clock) = backfill(&mail, &[ADA]);
    backfill.round().unwrap();
    assert_eq!(mail.tried_pks(), [1, 2]);
    assert_eq!(backfill.round().unwrap(), CAUGHT_UP_WAIT);
    assert_eq!(mail.tried_pks(), Vec::<i64>::new());
    clock.shift(NOT_GIVEN_WAIT - 1);
    backfill.round().unwrap();
    assert_eq!(mail.tried_pks(), Vec::<i64>::new());
    clock.shift(1);
    mail.server(1, Server::Gives);
    backfill.round().unwrap();
    assert_eq!(mail.tried_pks(), [1, 2]);
}

#[test]
fn a_message_set_aside_and_opened_meanwhile_is_let_go() {
    let mail = Mail::new(&[(ADA, 1..=1)]);
    mail.server(1, Server::GivesNothing);
    let (backfill, clock) = backfill(&mail, &[ADA]);
    backfill.round().unwrap();
    assert_eq!(mail.tried_pks(), [1]);
    mail.kept.lock().unwrap().insert(1);
    clock.shift(NOT_GIVEN_WAIT);
    // Its body is kept now: nothing is asked once its wait would have ended.
    assert_eq!(backfill.round().unwrap(), CAUGHT_UP_WAIT);
    assert_eq!(mail.tried_pks(), Vec::<i64>::new());
}

//! The IMAP gates: a job holds its account's gate, a held gate turns away a job that won't wait, a job never waits
//! inside a job, and waiting jobs go in priority order.

#![allow(clippy::unwrap_used)]

use std::sync::mpsc;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use mm_mailbox::gates::{EVERY_ACCOUNT, Gates, Priority, Refused, Wait, on_gate};
use mm_server::gates::AccountGates;

const ADA: &str = "ada@example.test";
const BEA: &str = "bea@example.test";

/// A gate held on a thread of its own, given back when this drops.
struct Holding(Option<(mpsc::Sender<()>, thread::JoinHandle<()>)>);

impl Drop for Holding {
    fn drop(&mut self) {
        if let Some((release, holder)) = self.0.take() {
            let _ = release.send(());
            let _ = holder.join();
        }
    }
}

fn hold(gates: &Arc<AccountGates>, account: &'static str) -> Holding {
    let (release, released) = mpsc::channel::<()>();
    let (taken, is_taken) = mpsc::channel::<()>();
    let gates = gates.clone();
    let holder = thread::spawn(move || {
        let _ = on_gate(&*gates, account, Priority::Sync, Wait::Ever, || {
            let _ = taken.send(());
            let _ = released.recv();
        });
    });
    is_taken.recv().unwrap();
    Holding(Some((release, holder)))
}

#[test]
fn a_job_runs_with_its_gate_held_and_gives_it_back() {
    let gates = AccountGates::new(true);
    let seen = on_gate(&gates, ADA, Priority::Interactive, Wait::Ever, || gates.held(ADA));
    assert_eq!(seen, Ok(true));
    assert!(!gates.held(ADA));
    assert!(!gates.held(EVERY_ACCOUNT));
}

#[test]
fn a_held_gate_turns_away_a_job_that_wont_wait_and_one_whose_wait_runs_out() {
    let gates = Arc::new(AccountGates::new(true));
    let release = hold(&gates, ADA);
    assert_eq!(on_gate(&*gates, ADA, Priority::Interactive, Wait::Not, || 1), Err(Refused::Busy));
    let waited = on_gate(&*gates, ADA, Priority::Interactive, Wait::For(Duration::from_millis(30)), || 1);
    assert_eq!(waited, Err(Refused::Busy));
    // Another account's gate is its own.
    assert_eq!(on_gate(&*gates, BEA, Priority::Interactive, Wait::Not, || 2), Ok(2));
    drop(release);
    assert_eq!(on_gate(&*gates, ADA, Priority::Interactive, Wait::Ever, || 3), Ok(3));
}

#[test]
fn one_gate_for_every_account_unless_each_has_its_own() {
    let gates = Arc::new(AccountGates::new(false));
    let release = hold(&gates, ADA);
    assert!(gates.held(BEA));
    assert_eq!(on_gate(&*gates, BEA, Priority::Interactive, Wait::Not, || 1), Err(Refused::Busy));
    drop(release);
    assert_eq!(on_gate(&*gates, BEA, Priority::Interactive, Wait::Not, || 1), Ok(1));
}

#[test]
fn every_account_waits_for_every_gate() {
    let gates = Arc::new(AccountGates::new(true));
    let release = hold(&gates, BEA);
    assert!(gates.held(EVERY_ACCOUNT));
    assert_eq!(on_gate(&*gates, EVERY_ACCOUNT, Priority::Maintenance, Wait::Not, || 1), Err(Refused::Busy));
    drop(release);
    assert_eq!(on_gate(&*gates, EVERY_ACCOUNT, Priority::Maintenance, Wait::Not, || 1), Ok(1));
}

#[test]
fn a_job_never_waits_on_a_gate_inside_a_job() {
    let gates = AccountGates::new(true);
    let inner = on_gate(&gates, ADA, Priority::Interactive, Wait::Ever, || {
        on_gate(&gates, BEA, Priority::Interactive, Wait::Ever, || 1)
    });
    assert_eq!(inner, Ok(Err(Refused::Nested)));
    let not_waiting = on_gate(&gates, ADA, Priority::Interactive, Wait::Ever, || {
        on_gate(&gates, BEA, Priority::Interactive, Wait::Not, || 1)
    });
    assert_eq!(not_waiting, Ok(Ok(1)));
}

#[test]
fn waiting_jobs_go_in_priority_order_then_in_the_order_they_came() {
    let gates = Arc::new(AccountGates::new(true));
    let release = hold(&gates, ADA);
    let order = Arc::new(Mutex::new(Vec::new()));
    let mut waiting = Vec::new();
    for (name, priority) in
        [("sync", Priority::Sync), ("queued", Priority::AfterUser), ("click", Priority::Interactive)]
    {
        let (gates, order) = (gates.clone(), order.clone());
        waiting.push(thread::spawn(move || {
            on_gate(&*gates, ADA, priority, Wait::Ever, || order.lock().unwrap().push(name)).unwrap();
        }));
        thread::sleep(Duration::from_millis(30));
    }
    drop(release);
    for one in waiting {
        one.join().unwrap();
    }
    assert_eq!(*order.lock().unwrap(), ["click", "queued", "sync"]);
}

//! Each account's syncs in flight (`flights`): a removal stops those in the air, and what is to run after them runs once
//! the last has landed.

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use mm_mailbox::flights::Flights;

const ADA: &str = "ada@example.test";
const BEA: &str = "bea@example.test";

#[test]
fn a_removal_stops_each_flight_of_the_account_in_the_air_and_no_other() {
    let flights = Flights::default();
    let ada = flights.fly(ADA);
    let ada_again = flights.fly("Ada@Example.test");
    let bea = flights.fly(BEA);
    flights.stop("ADA@example.test");
    assert!(ada.stopped() && ada_again.stopped());
    assert!(!bea.stopped());
}

#[test]
fn a_removal_with_nothing_in_the_air_stops_no_later_flight() {
    let flights = Flights::default();
    flights.stop(ADA);
    assert!(!flights.fly(ADA).stopped());
}

#[test]
fn a_flight_landed_is_forgotten_with_its_stop() {
    let flights = Flights::default();
    {
        let _ada = flights.fly(ADA);
        flights.stop(ADA);
        assert!(flights.stopped(ADA));
    }
    assert!(!flights.stopped(ADA));
}

#[test]
fn what_runs_after_the_flights_runs_once_the_last_lands() {
    let flights = Flights::default();
    let ran = Arc::new(AtomicUsize::new(0));
    let outer = flights.fly(ADA);
    let inner = flights.fly(ADA);
    let counted = ran.clone();
    assert!(flights.after(ADA, Box::new(move || _ = counted.fetch_add(1, Ordering::SeqCst))));
    drop(inner);
    assert_eq!(ran.load(Ordering::SeqCst), 0);
    drop(outer);
    assert_eq!(ran.load(Ordering::SeqCst), 1);
}

#[test]
fn with_none_in_the_air_nothing_waits_to_run() {
    let flights = Flights::default();
    let ran = Arc::new(Mutex::new(false));
    let marked = ran.clone();
    assert!(!flights.after(ADA, Box::new(move || *marked.lock().unwrap() = true)));
    drop(flights.fly(ADA));
    assert!(!*ran.lock().unwrap());
}

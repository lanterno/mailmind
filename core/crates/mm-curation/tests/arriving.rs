//! A person's filters judged of a message as it arrives, as the Python app's `tests/unit/curation/
//! test_filters_on_arrival.py` has them: what the filters it fits ask comes together, one no filter fits is asked
//! nothing, and a store that can't judge them asks nothing and stops nothing.

#![allow(clippy::unwrap_used)]

use std::sync::{Arc, Mutex};

use mm_curation::arrival_ports::Judge;
use mm_curation::arriving::{Arriving, flags_of};
use mm_curation::ports::RuleBook;
use mm_curation::rulebook::{Filter, Rules};
use mm_kernel::errors::{Failure, Fault};

#[derive(Debug)]
struct Book(Result<Vec<Filter>, Fault>);

impl RuleBook for Book {
    fn load(&self) -> Result<Rules, Fault> {
        self.0.clone().map(|filters| Rules { filters, ..Rules::default() })
    }

    fn change(&self, _apply: &mut dyn FnMut(&mut Rules) -> Result<(), Failure>) -> Result<(), Failure> {
        unreachable!("a filter judged changes no rule")
    }
}

/// Holds the queries it is told hold, and remembers what it was asked; or can't be read.
#[derive(Debug, Default)]
struct Searches {
    holding: Vec<&'static str>,
    broken: bool,
    asked: Mutex<Vec<(i64, Vec<String>)>>,
}

impl Judge for Searches {
    fn holds(&self, pk: i64, queries: &[&str]) -> Result<Vec<bool>, Fault> {
        self.asked.lock().unwrap().push((pk, queries.iter().map(|q| (*q).to_owned()).collect()));
        if self.broken {
            return Err(Fault::new("DatabaseError"));
        }
        Ok(queries.iter().map(|q| self.holding.contains(q)).collect())
    }
}

fn filter(q: &str, star: bool, read: bool) -> Filter {
    Filter { id: format!("f-{q}"), q: q.to_owned(), star, read }
}

fn arriving(filters: Vec<Filter>, searches: Searches) -> (Arriving, Arc<Searches>) {
    let searches = Arc::new(searches);
    (Arriving::new(Arc::new(Book(Ok(filters))), searches.clone()), searches)
}

#[test]
fn what_the_filters_a_message_fits_ask_comes_together() {
    let filters = vec![
        filter("from:lin@example.test", true, false),
        filter("subject:lunch", false, true),
        filter("absent-word-nowhere", true, true),
    ];
    let holding = vec!["from:lin@example.test", "subject:lunch"];
    let (arriving, searches) = arriving(filters, Searches { holding, ..Searches::default() });
    assert_eq!(arriving.on_arrival(7), [("flagged", true), ("seen", true)]);
    let asked = searches.asked.lock().unwrap();
    assert_eq!(
        *asked,
        [(7, vec!["from:lin@example.test".into(), "subject:lunch".into(), "absent-word-nowhere".into()])]
    );
}

#[test]
fn a_message_no_filter_fits_is_asked_nothing_of() {
    let (arriving, _) = arriving(vec![filter("from:lin@example.test", true, true)], Searches::default());
    assert!(arriving.on_arrival(7).is_empty());
}

#[test]
fn with_no_filter_nothing_is_judged() {
    let (arriving, searches) = arriving(Vec::new(), Searches::default());
    assert!(arriving.on_arrival(7).is_empty());
    assert!(searches.asked.lock().unwrap().is_empty());
}

#[test]
fn a_filter_that_cant_be_judged_asks_nothing() {
    let broken = Searches { holding: vec!["from:lin@example.test"], broken: true, ..Searches::default() };
    let (arriving, _) = arriving(vec![filter("from:lin@example.test", true, false)], broken);
    assert!(arriving.on_arrival(7).is_empty());
    let unread = Arriving::new(Arc::new(Book(Err(Fault::new("DatabaseError")))), Arc::new(Searches::default()));
    assert!(unread.on_arrival(7).is_empty());
}

#[test]
fn two_filters_asking_the_same_ask_it_once() {
    let both = [filter("a", true, true), filter("b", true, false), filter("c", false, true)];
    assert_eq!(flags_of(&both), [("flagged", true), ("seen", true)]);
    assert_eq!(flags_of(&both[2..]), [("seen", true)]);
    assert!(flags_of(&[]).is_empty());
}

//! What `/api/debug/health` counts of the outbox (`compose.outbox.QUEUE.counts` and `len(compose.drafts.waiting())`):
//! the sends in each state as stored, and the saves the tray names, which are overdue half a minute after their push
//! was due while a push job runs, and as soon as it was due when none does.

#![allow(clippy::unwrap_used)]

use std::collections::BTreeMap;
use std::sync::{Arc, Mutex};

use mm_compose::outbox::{Outbox, WAITING_AFTER};
use mm_compose::ports::{Row, StoredOutbox};
use mm_kernel::errors::Fault;
use mm_kernel::seams::{Clock, Moment};

#[derive(Debug)]
struct Frozen;

/// Monday 2 November 2026, noon UTC.
const NOW: i64 = 1_793_620_800;

impl Clock for Frozen {
    fn now(&self) -> Moment {
        Moment::from_micros(NOW * 1_000_000 + 400_000)
    }
}

/// Saves due at each of `due`, and the moment each read asked them by.
#[derive(Debug, Default)]
struct Stored {
    due: Vec<i64>,
    asked: Mutex<Vec<i64>>,
}

impl StoredOutbox for Stored {
    fn recent(&self, _limit: i64) -> Result<Vec<Row>, Fault> {
        Ok(vec![])
    }

    fn overdue(&self, before: i64) -> Result<Vec<Row>, Fault> {
        self.asked.lock().unwrap().push(before);
        Ok(self.due.iter().filter(|due| **due <= before).map(|_| Row::new()).collect())
    }

    fn counts(&self) -> Result<BTreeMap<String, i64>, Fault> {
        Ok(BTreeMap::from([("queued".to_owned(), 2), ("sent".to_owned(), 5)]))
    }
}

fn outbox(due: Vec<i64>, pushing: bool) -> (Outbox, Arc<Stored>) {
    let stored = Arc::new(Stored { due, asked: Mutex::default() });
    (Outbox::new(stored.clone(), Arc::new(Frozen), pushing), stored)
}

#[test]
fn a_save_is_named_half_a_minute_after_its_push_was_due_while_a_push_job_runs() {
    let due = vec![NOW - WAITING_AFTER - 1, NOW - WAITING_AFTER, NOW - WAITING_AFTER + 1, NOW];
    let (pushing, stored) = outbox(due.clone(), true);
    assert_eq!(pushing.drafts_waiting().unwrap(), 2);
    assert_eq!(*stored.asked.lock().unwrap(), vec![NOW - WAITING_AFTER]);
    let (read_only, stored) = outbox(due, false);
    assert_eq!(read_only.drafts_waiting().unwrap(), 4);
    assert_eq!(*stored.asked.lock().unwrap(), vec![NOW]);
}

#[test]
fn the_sends_are_counted_in_each_state_as_stored() {
    let (outbox, _) = outbox(vec![], true);
    assert_eq!(
        outbox.counts().unwrap().into_iter().collect::<Vec<_>>(),
        vec![("queued".to_owned(), 2), ("sent".to_owned(), 5)]
    );
}

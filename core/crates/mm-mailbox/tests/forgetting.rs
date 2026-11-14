//! The forget round's cutoff: thirty days before the clock's whole second, as seconds for `gone_at` and written as
//! `done_at` is for the change queue, as the Python app's round reckons them from `int(time.time())`.

#![allow(clippy::unwrap_used)]

use std::sync::{Arc, Mutex};

use mm_kernel::errors::Fault;
use mm_kernel::seams::{Clock, Moment};
use mm_mailbox::forgetting::{ForgetStore, Forgetting, Forgotten};

#[derive(Debug)]
struct At(i64);

impl Clock for At {
    fn now(&self) -> Moment {
        Moment::from_micros(self.0)
    }
}

/// What it was asked to let go of; `fails` makes it fail as a storage would.
#[derive(Debug, Default)]
struct Asked {
    cutoffs: Mutex<Vec<(i64, String)>>,
    fails: bool,
}

impl ForgetStore for Asked {
    fn forget(&self, gone_before: i64, done_before: &str) -> Result<Forgotten, Fault> {
        if self.fails {
            return Err(Fault::new("StorageError"));
        }
        self.cutoffs.lock().unwrap().push((gone_before, done_before.to_owned()));
        Ok(Forgotten { keys: 2, commands: 1 })
    }
}

#[test]
fn the_cutoff_is_thirty_days_before_the_whole_second() {
    let asked = Arc::new(Asked::default());
    // 2026-11-02 12:00:00.75 UTC
    let forgetting = Forgetting::new(asked.clone(), Arc::new(At(1_793_620_800_750_000)));
    assert_eq!(forgetting.round().unwrap(), Forgotten { keys: 2, commands: 1 });
    assert_eq!(*asked.cutoffs.lock().unwrap(), [(1_791_028_800, "2026-10-03T12:00:00+00:00".to_owned())]);
}

#[test]
fn a_round_the_store_fails_says_so() {
    let forgetting = Forgetting::new(Arc::new(Asked { fails: true, ..Asked::default() }), Arc::new(At(0)));
    assert_eq!(forgetting.round().unwrap_err().class, "StorageError");
}

//! The morning's brief and the week's review, written before anyone opens them (`Briefs.write_ahead` and
//! `Reviews.write_ahead`, as `bootstrap.background`'s opt-in rounds run them): in the person's morning, with none
//! stored yet, and once triage has read the last day's mail, which is what each is made from. What is held back, or
//! fails to come back, waits for the next round.

use std::sync::Arc;

use mm_kernel::errors::{Failure, Fault, Kind};
use mm_kernel::seams::Clock;
use mm_kernel::zone::Zone;
use serde_json::Value;

use crate::asking::Asking;
use crate::background_ports::Triage;
use crate::ports::AccountList;
use crate::refusals::AiFailure;
use crate::review::{WRITTEN_AHEAD_ON, weekday};

/// The local hours a brief may be written ahead of the first open: from five until noon.
pub const AHEAD_FROM: i64 = 5;
pub const AHEAD_UNTIL: i64 = 12;
/// How far back triage must have caught up first, in seconds.
pub const AHEAD_READS_BACK: i64 = 24 * 3600;

/// Whether a brief may be written ahead at this local hour.
pub fn is_morning(hour: i64) -> bool {
    (AHEAD_FROM..AHEAD_UNTIL).contains(&hour)
}

/// Whether the week's review may be written ahead on this day (counted from the epoch) at this local hour: the morning
/// brief's hours, on the week's first day.
pub fn is_ahead_morning(day: i64, hour: i64) -> bool {
    weekday(day) == WRITTEN_AHEAD_ON && is_morning(hour)
}

/// What a card asked for ahead came to, as a round hears it: a call never sent and a model that didn't answer wait
/// for the next round, and anything else fails it.
fn settled(made: Result<Value, AiFailure>) -> Result<(), Failure> {
    match made {
        Ok(_) | Err(AiFailure::NotSent(_)) => Ok(()),
        Err(AiFailure::Failure(Failure::Problem(problem))) if problem.kind == Kind::Unreachable => Ok(()),
        Err(AiFailure::Failure(failure)) => Err(failure),
    }
}

#[derive(Debug)]
pub struct Ahead {
    asking: Arc<Asking>,
    accounts: Arc<dyn AccountList>,
    triage: Arc<dyn Triage>,
    clock: Arc<dyn Clock>,
    zone: Arc<dyn Zone>,
}

impl Ahead {
    pub fn new(
        asking: Arc<Asking>,
        accounts: Arc<dyn AccountList>,
        triage: Arc<dyn Triage>,
        clock: Arc<dyn Clock>,
        zone: Arc<dyn Zone>,
    ) -> Ahead {
        Ahead { asking, accounts, triage, clock, zone }
    }

    /// The day (counted from the epoch) and the hour it is in the person's zone, and the time in seconds.
    fn local_now(&self) -> (i64, i64, i64) {
        let now = self.clock.now().whole_seconds();
        let (day, second) = mm_kernel::zone::day_and_second(&*self.zone, now);
        (day, second / 3600, now)
    }

    /// Whether triage has read the last day's mail.
    fn caught_up(&self, now: i64) -> Result<bool, Fault> {
        Ok(self.triage.needs_summary(1, Some(now - AHEAD_READS_BACK))?.is_empty())
    }

    /// Today's brief, before anyone opens the Inbox: in the morning, with none stored, once triage has caught up.
    pub fn write_brief_ahead(&self) -> Result<(), Failure> {
        let (_, hour, now) = self.local_now();
        if !is_morning(hour) || !self.caught_up(now)? {
            return Ok(());
        }
        settled(self.asking.briefs.build(&self.accounts.emails()?, false, true, "brief-ahead"))
    }

    /// The week's review, before anyone opens it: on Monday morning, with none stored this week, once triage has caught
    /// up.
    pub fn write_review_ahead(&self) -> Result<(), Failure> {
        let (day, hour, now) = self.local_now();
        if !is_ahead_morning(day, hour) || !self.caught_up(now)? {
            return Ok(());
        }
        settled(self.asking.reviews.build(&self.accounts.emails()?, false, true, "review-ahead", true))
    }
}

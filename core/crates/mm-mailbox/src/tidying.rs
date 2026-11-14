//! Tidy by itself (`application.tidying.round`): once a day, after the person's day has started, what the brief's Tidy
//! offers is archived through the Mailbox's filing, under one undo group named for the day, which is also its
//! commands' mutation id. A day whose run filed something is done; nothing to file leaves nothing recorded, so a later
//! round looks again.

use std::sync::Arc;

use mm_kernel::errors::Failure;
use mm_kernel::seams::Clock;
use mm_kernel::zone::Zone;
use serde_json::Value;

use crate::filing::{Filing, NamedCopy};
use crate::ports::DigestReads;
use crate::tidy::{self, RunFiling, TidyLog};

/// A run's moves go through the one command path, as a person's marked rows do.
impl RunFiling for Filing {
    fn archive(&self, rows: &[(NamedCopy, bool)], group: &str) -> Result<Value, Failure> {
        self.file_many("archive", rows, Some(group), Some(group))
    }
}

#[derive(Debug)]
pub struct Tidying {
    log: Arc<dyn TidyLog>,
    digest: Arc<dyn DigestReads>,
    filing: Arc<dyn RunFiling>,
    clock: Arc<dyn Clock>,
    zone: Arc<dyn Zone>,
}

/// What wires Tidying.
#[derive(Debug)]
pub struct TidyingParts {
    pub log: Arc<dyn TidyLog>,
    /// The brief's list, which a run files from.
    pub digest: Arc<dyn DigestReads>,
    pub filing: Arc<dyn RunFiling>,
    pub clock: Arc<dyn Clock>,
    /// The person's zone: their day, and when it starts.
    pub zone: Arc<dyn Zone>,
}

impl Tidying {
    pub fn new(parts: TidyingParts) -> Tidying {
        let TidyingParts { log, digest, filing, clock, zone } = parts;
        Tidying { log, digest, filing, clock, zone }
    }

    /// Today's run, if none has filed anything yet: the group it went under, or None where there was nothing to do.
    pub fn round(&self) -> Result<Option<String>, Failure> {
        let (day, second) = mm_kernel::zone::day_and_second(&*self.zone, self.clock.now().whole_seconds());
        if second / 3600 < tidy::FIRST_HOUR {
            return Ok(None);
        }
        let (y, m, d) = mm_kernel::when::civil(day);
        let group = tidy::group_of(&format!("{y:04}-{m:02}-{d:02}"));
        if self.log.ran_under(&group)? {
            return Ok(None);
        }
        let rows = self.digest.digest(tidy::READS)?.rows;
        let taken_back = self.log.archives_undone()?;
        let picked: Vec<(NamedCopy, bool)> = rows
            .iter()
            .filter(|r| tidy::takes(r, &taken_back))
            .map(|r| {
                let number = |name: &str| r.get(name).and_then(Value::as_i64);
                let folder = r.get("folder").and_then(Value::as_str).unwrap_or_default().to_owned();
                ((number("message_key").unwrap_or_default(), folder, number("uid")), false)
            })
            .collect();
        if picked.is_empty() {
            return Ok(None);
        }
        self.filing.archive(&picked, &group)?;
        Ok(Some(group))
    }
}

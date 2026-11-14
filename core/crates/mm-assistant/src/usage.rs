//! What AI cost each local day, in all and by feature (`Gateway.usage`), from the usage ledger: what
//! `/api/debug/ai` lists, and what `/api/debug/health` says of today.

use std::sync::Arc;

use mm_kernel::errors::Fault;
use mm_kernel::seams::Clock;
use mm_kernel::zone::Zone;
use serde_json::{Map, Value, json};

use crate::ports::UsageDays;

/// The ledger's report of the days gone, each counted against the person's local midnight as a call is.
#[derive(Debug)]
pub struct UsageReport {
    ledger: Arc<dyn UsageDays>,
    clock: Arc<dyn Clock>,
    zone: Arc<dyn Zone>,
}

/// The local date `days` after 1970-01-01, `YYYY-MM-DD`.
fn written(days: i64) -> String {
    let (year, month, day) = mm_kernel::when::civil(days);
    format!("{year:04}-{month:02}-{day:02}")
}

impl UsageReport {
    pub fn new(ledger: Arc<dyn UsageDays>, clock: Arc<dyn Clock>, zone: Arc<dyn Zone>) -> UsageReport {
        UsageReport { ledger, clock, zone }
    }

    /// Each of the last `days` local days (at least one), newest first: its calls and their cost in micro-dollars, in
    /// all and by feature. A day without calls is there with zeros.
    pub fn days(&self, days: i64) -> Result<Vec<Value>, Fault> {
        let (today, _) = mm_kernel::zone::day_and_second(&*self.zone, self.clock.now().whole_seconds());
        let wanted: Vec<String> = (0..days.max(1)).map(|back| written(today - back)).collect();
        let mut out: Vec<(i64, i64, Map<String, Value>)> = vec![(0, 0, Map::new()); wanted.len()];
        let since = wanted.last().map_or("", String::as_str);
        for row in self.ledger.days(since)? {
            // A day after today's, which a clock set back would leave, isn't one of them.
            let Some(at) = wanted.iter().position(|day| *day == row.day) else { continue };
            let (calls, cost, features) = &mut out[at];
            *calls += row.calls;
            *cost += row.cost_micros;
            features.insert(row.feature, json!({"calls": row.calls, "cost_micros": row.cost_micros}));
        }
        Ok(wanted
            .into_iter()
            .zip(out)
            .map(|(day, (calls, cost, features))| {
                json!({"day": day, "calls": calls, "cost_micros": cost, "features": features})
            })
            .collect())
    }
}

//! A person's filters judged of a message as it arrives (`Filters.on_arrival`, `domain.filters.flags_of`): what the
//! filters it fits ask of it, together, as stars and read marks. No filter moves mail, and none may stop a sync: one
//! that can't be judged asks nothing.

use std::sync::Arc;

use mm_kernel::log;

use crate::arrival_ports::Judge;
use crate::ports::RuleBook;
use crate::rulebook::Filter;

const TAG: &str = "filters";

/// What the filters a message fits ask of it, together, as the flag columns name them.
pub fn flags_of<'a>(fits: impl IntoIterator<Item = &'a Filter>) -> Vec<(&'static str, bool)> {
    let mut flags: Vec<(&'static str, bool)> = Vec::new();
    for f in fits {
        if f.star && !flags.contains(&("flagged", true)) {
            flags.push(("flagged", true));
        }
        if f.read && !flags.contains(&("seen", true)) {
            flags.push(("seen", true));
        }
    }
    flags
}

#[derive(Debug)]
pub struct Arriving {
    rules: Arc<dyn RuleBook>,
    judge: Arc<dyn Judge>,
}

impl Arriving {
    pub fn new(rules: Arc<dyn RuleBook>, judge: Arc<dyn Judge>) -> Arriving {
        Arriving { rules, judge }
    }

    /// What the filters ask of the message `pk`, just stored and indexed.
    pub fn on_arrival(&self, pk: i64) -> Vec<(&'static str, bool)> {
        let judged = self.rules.load().and_then(|rules| {
            if rules.filters.is_empty() {
                return Ok(Vec::new());
            }
            let queries: Vec<&str> = rules.filters.iter().map(|f| f.q.as_str()).collect();
            let holds = self.judge.holds(pk, &queries)?;
            Ok(flags_of(rules.filters.iter().zip(holds).filter(|(_, held)| *held).map(|(f, _)| f)))
        });
        judged.unwrap_or_else(|fault| {
            log::warning(TAG, "a filter could not be judged", &[], Some(fault.class));
            Vec::new()
        })
    }
}

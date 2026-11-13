//! What is said of a sender beside their name as a message opens (`discovery.contacts.facts`): how many of their
//! messages there are, how many were answered, and how long ago the first came. One read, so opening a message asks
//! nothing more of the page.

use std::fmt;
use std::sync::Arc;

use mm_kernel::ago::ago;
use mm_kernel::errors::Fault;
use mm_kernel::seams::{Clock, Moment};
use serde_json::{Value, json};

use crate::ports::{Row, StoredTally};

/// The address a sender's facts are for, lowercase, or None for what couldn't be one (`contacts.address`).
pub fn address(addr: Option<&str>) -> Option<String> {
    let a = mm_kernel::text::strip(addr.unwrap_or_default()).to_lowercase();
    a.contains('@').then_some(a)
}

fn number_or_zero(v: Option<&Value>) -> Value {
    match v {
        Some(Value::Number(n)) if n.as_f64().is_some_and(|f| f != 0.0) => Value::Number(n.clone()),
        _ => json!(0),
    }
}

/// The facts of a tally (`contacts.facts`).
pub fn facts(tally: &Row, now: Moment) -> Value {
    let first = tally.get("first").and_then(|v| v.as_i64().or_else(|| v.as_f64().map(|f| f as i64)));
    json!({
        "count": number_or_zero(tally.get("n")),
        "replied": number_or_zero(tally.get("replied")),
        "first_ago": first.filter(|f| *f != 0).map(|f| ago(Some(f), now)),
    })
}

pub struct SenderFacts {
    stored: Arc<dyn StoredTally>,
    clock: Arc<dyn Clock>,
}

impl fmt::Debug for SenderFacts {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("SenderFacts").finish_non_exhaustive()
    }
}

impl SenderFacts {
    pub fn new(stored: Arc<dyn StoredTally>, clock: Arc<dyn Clock>) -> SenderFacts {
        SenderFacts { stored, clock }
    }

    /// What is said of the sender `addr`, null for what can't be an address.
    pub fn facts(&self, addr: Option<&str>) -> Result<Value, Fault> {
        let Some(a) = address(addr) else { return Ok(Value::Null) };
        Ok(facts(&self.stored.tally(&a)?, self.clock.now()))
    }
}

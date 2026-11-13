//! The per-account counts the page shows (`mailmind.mailbox.application.stats`), from what is stored: no server is
//! asked. They are kept for `TTL`, or until something that moves them says so (`invalidate`): a filing landing or
//! given back, a flag changed, a snooze or a mute, an undo, ghosts pruned.

use std::sync::{Arc, Mutex};

use mm_kernel::errors::Fault;
use mm_kernel::seams::Clock;
use mm_kernel::when;
use serde_json::{Value, json};

use crate::ports::{AccountList, InboxPlans};

/// How long the counts are kept, in microseconds, when nothing says they moved.
pub const TTL: i64 = 90 * 1_000_000;

#[derive(Debug)]
pub struct LiveStats {
    accounts: Arc<dyn AccountList>,
    curation: Arc<dyn InboxPlans>,
    clock: Arc<dyn Clock>,
    /// The counts last read, and the moment (in microseconds) they go stale.
    kept: Mutex<Option<(Value, i64)>>,
}

impl LiveStats {
    pub fn new(accounts: Arc<dyn AccountList>, curation: Arc<dyn InboxPlans>, clock: Arc<dyn Clock>) -> LiveStats {
        LiveStats { accounts, curation, clock, kept: Mutex::new(None) }
    }

    /// Each account's stored plan with its alias, the buckets, the totals across accounts, and when this was said:
    /// the counts kept, while they are fresh.
    pub fn live(&self) -> Result<Value, Fault> {
        let now = self.clock.now().micros();
        if let Some((counts, until)) = &*self.kept.lock().unwrap_or_else(std::sync::PoisonError::into_inner)
            && *until > now
        {
            return Ok(counts.clone());
        }
        let counts = self.count()?;
        *self.kept.lock().unwrap_or_else(std::sync::PoisonError::into_inner) =
            Some((counts.clone(), now.saturating_add(TTL)));
        Ok(counts)
    }

    /// Something moved the counts: the next read counts afresh.
    pub fn invalidate(&self) {
        *self.kept.lock().unwrap_or_else(std::sync::PoisonError::into_inner) = None;
    }

    fn count(&self) -> Result<Value, Fault> {
        let accounts = self.accounts.accounts()?;
        let plans = self.curation.plans_stored(&accounts)?;
        let mut out = Vec::with_capacity(plans.len());
        let (mut in_inbox, mut junk, mut keep) = (0, 0, 0);
        for (account, mut plan) in accounts.into_iter().zip(plans) {
            let count = |name: &str| plan.get(name).and_then(Value::as_i64).unwrap_or_default();
            in_inbox += count("in_inbox");
            junk += count("junk");
            keep += count("keep");
            plan.insert("alias".into(), json!(account.alias));
            out.push(Value::Object(plan));
        }
        Ok(json!({
            "accounts": out,
            "buckets": self.curation.buckets(),
            "totals": {"in_inbox": in_inbox, "junk": junk, "keep": keep},
            "generated": when::iso(self.clock.now()),
        }))
    }
}

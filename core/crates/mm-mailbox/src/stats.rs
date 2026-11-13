//! The per-account counts the page shows (`mailmind.mailbox.application.stats`), from what is stored: no server is
//! asked. The Python app keeps them 90 seconds; they are quick enough to read afresh each time.

use std::sync::Arc;

use mm_kernel::errors::Fault;
use mm_kernel::seams::Clock;
use mm_kernel::when;
use serde_json::{Value, json};

use crate::ports::{AccountList, InboxPlans};

#[derive(Debug)]
pub struct LiveStats {
    accounts: Arc<dyn AccountList>,
    curation: Arc<dyn InboxPlans>,
    clock: Arc<dyn Clock>,
}

impl LiveStats {
    pub fn new(accounts: Arc<dyn AccountList>, curation: Arc<dyn InboxPlans>, clock: Arc<dyn Clock>) -> LiveStats {
        LiveStats { accounts, curation, clock }
    }

    /// Each account's stored plan with its alias, the buckets, the totals across accounts, and when this was said.
    pub fn live(&self) -> Result<Value, Fault> {
        let mut out = Vec::new();
        let (mut in_inbox, mut junk, mut keep) = (0, 0, 0);
        for account in self.accounts.accounts()? {
            let mut plan = self.curation.plan_stored(&account)?;
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

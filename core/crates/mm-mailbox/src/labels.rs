//! A Gmail account's own labels and how many messages carry each: its lanes in the rail (`mailmind.mailbox
//! .application.queries`' `labels`). Any other account has none. A pure read: no server is asked.

use std::sync::Arc;

use mm_kernel::errors::{Failure, Problem};
use serde_json::{Value, json};

use crate::ports::StoredLabels;

#[derive(Debug)]
pub struct Labels {
    store: Arc<dyn StoredLabels>,
}

impl Labels {
    pub fn new(store: Arc<dyn StoredLabels>) -> Labels {
        Labels { store }
    }

    /// `account`'s labels, each with its count. With no account named it is refused.
    pub fn of_account(&self, account: Option<&str>) -> Result<Value, Failure> {
        let account = account.filter(|a| !a.is_empty()).ok_or_else(|| Problem::refused("account required"))?;
        let labels: Vec<Value> = self
            .store
            .labels_of_account(account)?
            .into_iter()
            .map(|(name, count)| json!({"name": name, "count": count}))
            .collect();
        Ok(json!({"ok": true, "labels": labels}))
    }
}

//! The settings page's question: how does each account stand? (`mailmind.accounts.application.health` and
//! `domain.health`.) From what its syncs came to: the Mailbox reports what happened, and this says what it means.

use std::sync::Arc;

use mm_kernel::errors::Failure;
use mm_kernel::seams::Clock;
use mm_kernel::when;
use serde_json::{Value, json};

use crate::ports::{SyncRecord, Synced};

/// Whether an account is well: a sync has worked, and none has failed since.
pub fn healthy(synced: &Synced) -> bool {
    synced.last_ok_ts.as_deref().is_some_and(|ts| !ts.is_empty())
        && synced.last_error.as_deref().is_none_or(str::is_empty)
}

fn row(s: &Synced) -> Value {
    json!({
        "account": s.account,
        "last_ok_ts": s.last_ok_ts,
        "last_error_ts": s.last_error_ts,
        "last_error_kind": s.last_error_kind,
        "last_error": s.last_error,
        "sync_count": s.sync_count,
        "healthy": healthy(s),
        "live": s.live,
    })
}

#[derive(Debug)]
pub struct Health {
    syncs: Arc<dyn SyncRecord>,
    clock: Arc<dyn Clock>,
}

impl Health {
    pub fn new(syncs: Arc<dyn SyncRecord>, clock: Arc<dyn Clock>) -> Health {
        Health { syncs, clock }
    }

    /// Each account's durable sync health, merged with what its sync is doing now. A pure read: no server is asked.
    pub fn answer(&self) -> Result<Value, Failure> {
        let accounts: Vec<Value> = self.syncs.sync_record()?.iter().map(row).collect();
        Ok(json!({"ok": true, "accounts": accounts, "generated": when::iso(self.clock.now())}))
    }
}

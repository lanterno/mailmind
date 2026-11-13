//! How each account stands, from what its syncs came to.

#![allow(clippy::unwrap_used)]

use std::sync::Arc;

use mm_accounts::health::{Health, healthy};
use mm_accounts::ports::{SyncRecord, Synced};
use mm_kernel::errors::Fault;
use mm_kernel::seams::{Clock, Moment};
use serde_json::json;

#[derive(Debug)]
struct Frozen;

impl Clock for Frozen {
    fn now(&self) -> Moment {
        Moment::from_micros(1_793_620_800_000_000)
    }
}

fn synced(ok: Option<&str>, error: Option<&str>) -> Synced {
    Synced {
        account: "ada@example.test".into(),
        last_ok_ts: ok.map(str::to_owned),
        last_error_ts: error.map(|_| "2026-11-01T11:00:00+00:00".to_owned()),
        last_error_kind: error.map(|_| "TIMEOUT".to_owned()),
        last_error: error.map(str::to_owned),
        sync_count: 3,
        live: String::new(),
    }
}

#[test]
fn an_account_is_well_when_a_sync_has_worked_and_none_has_failed_since() {
    let ok = Some("2026-11-01T10:00:00+00:00");
    assert!(healthy(&synced(ok, None)));
    assert!(healthy(&synced(ok, Some(""))));
    assert!(!healthy(&synced(ok, Some("TimeoutError"))));
    assert!(!healthy(&synced(None, None)));
    assert!(!healthy(&synced(Some(""), None)));
}

#[derive(Debug)]
struct Record;

impl SyncRecord for Record {
    fn sync_record(&self) -> Result<Vec<Synced>, Fault> {
        Ok(vec![synced(Some("2026-11-01T10:00:00+00:00"), Some("TimeoutError"))])
    }
}

#[test]
fn each_account_s_health_is_said_with_when() {
    let answer = Health::new(Arc::new(Record), Arc::new(Frozen)).answer().unwrap();
    assert_eq!(
        answer,
        json!({"ok": true, "generated": "2026-11-02T12:00:00+00:00", "accounts": [{
            "account": "ada@example.test", "last_ok_ts": "2026-11-01T10:00:00+00:00",
            "last_error_ts": "2026-11-01T11:00:00+00:00", "last_error_kind": "TIMEOUT", "last_error": "TimeoutError",
            "sync_count": 3, "healthy": false, "live": ""}]})
    );
}

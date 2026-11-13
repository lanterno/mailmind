//! What the Mailbox counts from what is stored: each account's inbox by bucket, a Gmail account's labels, and what
//! each account's syncs came to.

#![allow(clippy::unwrap_used)]

use std::sync::Arc;

use mm_kernel::errors::{Failure, Fault, Kind};
use mm_kernel::seams::{Clock, Moment};
use mm_mailbox::labels::Labels;
use mm_mailbox::ports::{AccountList, InboxPlans, Listed, StoredLabels, StoredSyncs, SyncHealth};
use mm_mailbox::stats::LiveStats;
use mm_mailbox::sync_record::SyncRecords;
use serde_json::{Map, Value, json};

#[derive(Debug)]
struct Frozen;

impl Clock for Frozen {
    fn now(&self) -> Moment {
        Moment::from_micros(1_793_620_800_000_000)
    }
}

#[derive(Debug)]
struct Two;

impl AccountList for Two {
    fn accounts(&self) -> Result<Vec<Listed>, Fault> {
        Ok(vec![
            Listed { email: "ada@example.test".into(), kind: "imap".into(), alias: Some("Ada".into()) },
            Listed { email: "bea@example.test".into(), kind: "proton".into(), alias: None },
        ])
    }
}

#[derive(Debug)]
struct Plans;

impl InboxPlans for Plans {
    fn plan_stored(&self, account: &Listed) -> Result<Map<String, Value>, Fault> {
        let (in_inbox, junk) = if account.email.starts_with("ada") { (12, 3) } else { (2, 0) };
        let plan = json!({"account": account.email, "kind": account.kind, "in_inbox": in_inbox, "by_bucket": {},
                          "junk": junk, "keep": in_inbox - junk, "top_senders": {}, "error": null});
        Ok(plan.as_object().unwrap().clone())
    }

    fn buckets(&self) -> Value {
        json!([{"name": "spam/scam", "is_junk": true}])
    }
}

#[test]
fn the_counts_name_each_account_s_plan_with_its_alias_and_the_totals() {
    let stats = LiveStats::new(Arc::new(Two), Arc::new(Plans), Arc::new(Frozen)).live().unwrap();
    assert_eq!(stats["totals"], json!({"in_inbox": 14, "junk": 3, "keep": 11}));
    assert_eq!(stats["generated"], json!("2026-11-02T12:00:00+00:00"));
    assert_eq!(stats["buckets"], json!([{"name": "spam/scam", "is_junk": true}]));
    let accounts = stats["accounts"].as_array().unwrap();
    assert_eq!((&accounts[0]["alias"], &accounts[1]["alias"]), (&json!("Ada"), &Value::Null));
    assert_eq!((&accounts[1]["kind"], &accounts[1]["in_inbox"]), (&json!("proton"), &json!(2)));
    assert_eq!(accounts[0].as_object().unwrap().len(), 9);
}

#[derive(Debug)]
struct Gmail;

impl StoredLabels for Gmail {
    fn labels_of_account(&self, account: &str) -> Result<Vec<(String, i64)>, Fault> {
        Ok(if account == "ada@example.test" { vec![("apple".into(), 1), ("Receipts".into(), 3)] } else { Vec::new() })
    }
}

#[test]
fn an_account_s_labels_come_with_their_counts() {
    let labels = Labels::new(Arc::new(Gmail));
    assert_eq!(
        labels.of_account(Some("ada@example.test")).unwrap(),
        json!({"ok": true, "labels": [{"name": "apple", "count": 1}, {"name": "Receipts", "count": 3}]})
    );
    assert_eq!(labels.of_account(Some("cy@example.test")).unwrap(), json!({"ok": true, "labels": []}));
}

#[test]
fn labels_with_no_account_named_are_refused() {
    let labels = Labels::new(Arc::new(Gmail));
    for account in [None, Some("")] {
        match labels.of_account(account) {
            Err(Failure::Problem(problem)) => {
                assert_eq!((problem.kind, problem.said.as_str()), (Kind::Refused, "account required"))
            }
            other => panic!("{other:?}"),
        }
    }
}

#[derive(Debug)]
struct Syncs;

fn health(account: &str, folder: &str) -> SyncHealth {
    SyncHealth {
        account: account.into(),
        folder: folder.into(),
        last_ok_ts: None,
        last_error: None,
        last_error_ts: None,
        last_error_kind: None,
        sync_count: 0,
    }
}

impl StoredSyncs for Syncs {
    fn sync_health(&self) -> Result<Vec<SyncHealth>, Fault> {
        Ok(vec![
            health("ada@example.test", "Archive"),
            health("ada@example.test", "INBOX"),
            health("bea@example.test", ""),
        ])
    }
}

#[test]
fn an_account_s_sync_record_is_its_inbox_s_and_none_has_run_since_the_start() {
    let record = SyncRecords::new(Arc::new(Syncs)).sync_record().unwrap();
    let named: Vec<(&str, &str, &str)> =
        record.iter().map(|r| (r.health.account.as_str(), r.health.folder.as_str(), r.live)).collect();
    assert_eq!(named, [("ada@example.test", "INBOX", ""), ("bea@example.test", "", "")]);
}

//! An account's entry asking for its Drafts, Archive and Sent to be warmed: only `"warm_folders": true` asks
//! (`normalized`'s `a.get("warm_folders") is True`), which counts while MAILMIND_WARM_FOLDERS is unset.

#![allow(clippy::unwrap_used)]

use mm_accounts::Account;
use serde_json::{Value, json};

fn asks(given: Option<Value>) -> bool {
    let mut entry = json!({"email": "ada@example.test", "host": "imap.example.test", "port": 993});
    if let Some(given) = given {
        entry["warm_folders"] = given;
    }
    Account::from_entry(&entry).unwrap().warm_folders
}

#[test]
fn only_true_asks_for_warming() {
    assert!(asks(Some(json!(true))));
    for not in [None, Some(json!(false)), Some(json!(1)), Some(json!("true")), Some(Value::Null)] {
        assert!(!asks(not.clone()), "{not:?}");
    }
}

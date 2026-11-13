//! What a person hears of many moves at once (`filing::of_many`), as the Python app's `filing_answer.of_many`.

#![allow(clippy::unwrap_used)]

use std::collections::{BTreeMap, HashMap};

use mm_mailbox::filing::of_many;
use serde_json::json;

/// A move the worker has, waiting on its account's gate, is counted as queued, as one queued is.
#[test]
fn one_the_worker_took_over_counts_as_queued() {
    let names = BTreeMap::from([(1, (10, "INBOX".to_owned(), 100)), (2, (20, "INBOX".to_owned(), 200))]);
    for status in ["queued", "waiting"] {
        let results = HashMap::from([
            (1, Ok(json!({"ok": true, "undoable": true}))),
            (2, Ok(json!({"ok": true, "msg": "queued", "status": status, "undoable": true}))),
        ]);
        let said = of_many(&[(1, None), (2, None)], &results, &names).unwrap();
        assert_eq!((said["msg"].clone(), said["queued"].clone()), (json!("queued"), json!(1)), "{status}");
    }
}

//! The preferences block: what a file says, what a change may say, and the AI limit as the Assistant reads it.

use mm_accounts::preferences::{apply, changes, daily_limit_usd, importable, read};
use serde_json::{Map, Value, json};

fn object(value: Value) -> Map<String, Value> {
    match value {
        Value::Object(map) => map,
        _ => Map::new(),
    }
}

#[test]
fn a_file_with_no_block_reads_as_the_defaults() {
    let read = read(&object(json!({"accounts": []})));
    assert_eq!(read.len(), 21);
    assert_eq!(read["undo_send_seconds"], json!(10));
    assert_eq!(read["theme"], json!("day"));
    assert_eq!(read["single_keys"], json!(true));
    assert_eq!(read["quiet_until"], json!("07:00"));
    assert_eq!(read.keys().next().map(String::as_str), Some("undo_send_seconds"));
}

#[test]
fn what_a_hand_edit_left_that_cant_be_one_is_the_default() {
    let raw = object(json!({"preferences": {"undo_send_seconds": 10.0, "theme": "teal", "single_keys": 0,
                                            "quiet_from": "24:00", "density": "cozy"}}));
    let read = read(&raw);
    assert_eq!(read["undo_send_seconds"], json!(10));
    assert_eq!(read["theme"], json!("day"));
    assert_eq!(read["single_keys"], json!(true));
    assert_eq!(read["quiet_from"], json!("22:00"));
    assert_eq!(read["density"], json!("cozy"));
}

#[test]
fn one_value_that_cant_be_refuses_the_whole_change() {
    let asked = object(json!({"theme": "night", "undo_send_seconds": 61}));
    let refused = changes(&asked).err().map(|p| p.said);
    assert_eq!(refused.as_deref(), Some("the Undo Send window must be 5 to 60 seconds"));
    let asked = object(json!({"ai_daily_limit_usd": 0.0}));
    let refused = changes(&asked).err().map(|p| p.said);
    assert_eq!(refused.as_deref(), Some("the AI daily limit must be more than $0 and at most $1000"));
    let asked = object(json!({"density": null}));
    assert_eq!(changes(&asked).err().map(|p| p.said).as_deref(), Some("density can't be that"));
}

#[test]
fn a_change_writes_the_block_and_the_limit_at_the_top() {
    let mut raw = object(json!({"accounts": [], "preferences": "lost"}));
    let wanted = changes(&object(json!({"theme": "dusk", "ai_daily_limit_usd": 12.5, "other": 1}))).unwrap_or_default();
    apply(&mut raw, &wanted);
    assert_eq!(
        Value::Object(raw),
        json!({"accounts": [], "preferences": {"theme": "dusk"}, "ai_daily_limit_usd": 12.5})
    );
}

#[test]
fn an_import_takes_only_what_the_file_doesnt_say() {
    let raw = object(json!({"preferences": {"theme": "night"}}));
    let held = object(json!({"theme": "dusk", "density": "compact", "read_size": "huge", "ai_daily_limit_usd": 3}));
    assert_eq!(Value::Object(importable(&held, &raw)), json!({"density": "compact"}));
}

#[test]
fn the_daily_limit_reads_as_python_reads_it() {
    assert_eq!(daily_limit_usd(&object(json!({}))), 15.0);
    assert_eq!(daily_limit_usd(&object(json!({"ai_daily_limit_usd": 12}))), 12.0);
    assert_eq!(daily_limit_usd(&object(json!({"ai_daily_limit_usd": " 1_2.5 "}))), 12.5);
    assert_eq!(daily_limit_usd(&object(json!({"ai_daily_limit_usd": "1__2"}))), 15.0);
    assert_eq!(daily_limit_usd(&object(json!({"ai_daily_limit_usd": true}))), 1.0);
    assert_eq!(daily_limit_usd(&object(json!({"ai_daily_limit_usd": -3}))), 15.0);
    assert_eq!(daily_limit_usd(&object(json!({"ai_daily_limit_usd": 0.0000005}))), 0.0);
}

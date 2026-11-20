//! What a verdict asks of you as a row carries it (`mailmind.kernel.attention.answers`): a message read as junk asks
//! nothing, whatever the AI said of it before it was.

use mm_kernel::verdict::answers;
use serde_json::{Map, Value, json};

fn answered(importance: Option<i64>, category: Option<&str>, action: Option<&str>, junk: bool) -> Value {
    let mut row = Map::new();
    answers(&mut row, importance, category, action, junk);
    Value::Object(row)
}

#[test]
fn a_message_that_isnt_junk_asks_what_its_verdict_says() {
    assert_eq!(
        answered(Some(4), Some("action-needed"), Some("Pay the invoice"), false),
        json!({"needs_you": true, "reply_worthy": true, "commitment_worthy": true, "safe_to_file": false})
    );
    assert_eq!(
        answered(Some(1), Some("info"), Some(""), false),
        json!({"needs_you": false, "reply_worthy": false, "commitment_worthy": false, "safe_to_file": true})
    );
}

#[test]
fn a_message_read_as_junk_asks_nothing_whatever_its_verdict_says() {
    let nothing = json!({"needs_you": false, "reply_worthy": false, "commitment_worthy": false, "safe_to_file": false});
    assert_eq!(answered(Some(4), Some("action-needed"), Some("Pay the invoice"), true), nothing);
    assert_eq!(answered(Some(1), Some("info"), Some(""), true), nothing);
}

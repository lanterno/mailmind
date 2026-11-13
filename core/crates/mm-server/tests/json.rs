//! JSON in Python's `json.dumps` bytes.

#![allow(clippy::unwrap_used)]

use mm_server::json::dumps;
use serde_json::json;

#[test]
fn writes_what_python_writes() {
    let value = json!({"type": "about:blank", "n": [1, -2, true, null], "s": "caf\u{e9} \u{1f600}\u{7f}\"\\\n\u{1}"});
    // `|` stands for a backslash, so the expected escapes read as written.
    let expected =
        r#"{"type": "about:blank", "n": [1, -2, true, null], "s": "caf|u00e9 |ud83d|ude00|u007f|"|||n|u0001"}"#;
    assert_eq!(dumps(&value), expected.replace('|', "\\"));
    assert_eq!(dumps(&json!({})), "{}");
    assert_eq!(dumps(&json!([])), "[]");
    assert_eq!(dumps(&json!({"z": 1, "a": {"b": []}})), r#"{"z": 1, "a": {"b": []}}"#, "keys in the order written");
}

#[test]
fn writes_floats_as_pythons_repr_does() {
    let floats = json!([1.0, 0.5, 1e16, 1e15, 1e-5, 0.0001, -2.5e-7, 123456789.125]);
    assert_eq!(dumps(&floats), "[1.0, 0.5, 1e+16, 1000000000000000.0, 1e-05, 0.0001, -2.5e-07, 123456789.125]");
}

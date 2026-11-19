//! Whom a stored message went to, from its To and Cc as the store keeps them: the cases of
//! tests/fixtures/addressees.json, which the Python app's `kernel.addressees` is held to too, so both servers name the
//! same people.

use mm_kernel::addressees::{listed, people};
use serde_json::{Value, json};

fn cases() -> Vec<Value> {
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../tests/fixtures/addressees.json");
    let text = std::fs::read_to_string(path).expect("the shared cases");
    let fixture: Value = serde_json::from_str(&text).expect("the cases read");
    fixture["cases"].as_array().expect("a list of cases").clone()
}

#[test]
fn each_person_is_read_once_in_order_with_the_name_written() {
    let cases = cases();
    assert!(cases.len() > 10);
    for case in cases {
        let found: Vec<Value> =
            people(case["text"].as_str()).into_iter().map(|(name, addr)| json!([name, addr])).collect();
        assert_eq!(Value::Array(found), case["people"], "{}", case["why"]);
    }
}

fn as_map(text: Option<&str>) -> Value {
    Value::Object(listed(text).into_iter().map(|(name, value)| (name.to_owned(), value)).collect())
}

#[test]
fn a_row_carries_the_first_two_and_counts_the_rest() {
    let text = "Lin Mori <lin@example.test>, bea@example.test Cy <cy@example.test>, dee@example.test";
    assert_eq!(
        as_map(Some(text)),
        json!({"sent_to": [{"name": "Lin Mori", "addr": "lin@example.test"}, {"name": null, "addr": "bea@example.test"}],
               "sent_to_more": 2})
    );
}

#[test]
fn a_row_of_fewer_counts_none_more() {
    for (text, first, more) in [
        (Some("Lin <lin@example.test>"), 1, 0),
        (Some("Lin <lin@example.test> bea@example.test"), 2, 0),
        (Some(""), 0, 0),
        (None, 0, 0),
    ] {
        let row = as_map(text);
        assert_eq!((row["sent_to"].as_array().map(Vec::len), row["sent_to_more"].as_u64()), (Some(first), Some(more)));
    }
}

#[test]
fn the_same_person_twice_counts_once() {
    assert_eq!(as_map(Some("Lin <lin@example.test>, LIN@example.test, lin@example.test"))["sent_to_more"], 0);
}

//! A message of a few headers over a plain text, byte for byte as Python's `EmailMessage` writes one with each header
//! set by its name and `set_content(text)`: the cases in `fixtures/plain_message_oracle.json`, each with the bytes
//! Python wrote for it. The export writes one for each copy whose whole message was never fetched.
#![allow(clippy::unwrap_used)]

use mm_compose::builder::{Header, plain_message};
use serde_json::Value;

/// The oracle's text for bytes: each character one byte.
fn latin1(s: &str) -> Vec<u8> {
    s.chars().map(|c| u8::try_from(u32::from(c)).unwrap()).collect()
}

/// Header names live as long as the program, as the export's do.
fn name(text: &str) -> &'static str {
    Box::leak(text.to_owned().into_boxed_str())
}

#[test]
fn each_case_is_written_as_python_writes_it() {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fixtures/plain_message_oracle.json");
    let cases: Vec<Value> = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    assert!(cases.len() >= 16);
    for case in &cases {
        let text_at = |h: &Value, i: usize| h[i].as_str().unwrap().to_owned();
        let headers: Vec<Header> = case["headers"]
            .as_array()
            .unwrap()
            .iter()
            .map(|h| match h[0].as_str().unwrap() {
                "Person" => Header::Person(name(h[1].as_str().unwrap()), text_at(h, 2), text_at(h, 3)),
                _ => Header::Named(name(h[1].as_str().unwrap()), text_at(h, 2)),
            })
            .collect();
        let text = case["text"].as_str().unwrap();
        if let Some(refused) = case["error"].as_str() {
            let words = plain_message(&headers, text).unwrap_err();
            assert_eq!(format!("ValueError: {words}"), refused);
            continue;
        }
        let made = plain_message(&headers, text).unwrap();
        let expected = latin1(case["raw"].as_str().unwrap());
        assert_eq!(
            String::from_utf8_lossy(&made),
            String::from_utf8_lossy(&expected),
            "headers {headers:?}, text {text:?}"
        );
        assert_eq!(made, expected);
    }
}

#[test]
fn a_text_that_fits_goes_as_it_is_and_one_that_doesnt_is_encoded() {
    let plain = |text: &str| String::from_utf8(plain_message(&[], text).unwrap()).unwrap();
    assert!(plain("hello").contains("Content-Transfer-Encoding: 7bit\r\n"));
    assert!(plain("héllo").contains("Content-Transfer-Encoding: 8bit\r\n"));
    assert!(plain(&"a".repeat(79)).contains("Content-Transfer-Encoding: quoted-printable\r\n"));
    assert!(plain(&"ж".repeat(79)).contains("Content-Transfer-Encoding: base64\r\n"));
    assert!(plain("").ends_with("MIME-Version: 1.0\r\n\r\n\r\n"));
}

#[test]
fn a_header_that_would_break_a_line_is_refused() {
    let refused = plain_message(&[Header::Named("Subject", "one\nTwo: injected".to_owned())], "x");
    assert_eq!(refused.unwrap_err(), "Header values may not contain linefeed or carriage return characters");
}

//! A POST's body as the Python app's route reads it and its schemas let its fields through
//! (`mailmind.http.route.Route.handle`, `http.schemas.common`).

#![allow(clippy::unwrap_used)]

use axum::body::Body;
use axum::http::Request;
use mm_kernel::errors::Problem;
use mm_server::body::{self, Fields, Unread, as_int};
use serde_json::json;

fn read(body: &'static str, declared: bool) -> Result<Fields, Unread> {
    let mut request = Request::post("/api/archive");
    if declared {
        request = request.header("content-length", body.len());
    }
    let runtime = tokio::runtime::Builder::new_current_thread().build().unwrap();
    runtime.block_on(body::read(request.body(Body::from(body)).unwrap()))
}

#[test]
fn a_body_counts_only_with_a_length_declared_and_something_in_it() {
    assert_eq!(read("", true), Ok(Fields::default()));
    assert_eq!(read(r#"{"key": 1}"#, false), Ok(Fields::default()));
    assert_eq!(read(r#"{"key": 1}"#, true).unwrap().number("key"), Ok(Some(1)));
}

#[test]
fn what_isnt_a_json_object_is_a_bad_request() {
    for body in ["[]", "7", "\"x\"", "{", "null"] {
        assert_eq!(read(body, true), Err(Unread::NotAnObject), "{body}");
    }
}

#[test]
fn a_body_past_its_cap_with_no_length_declared_is_too_large() {
    let big: &'static str = Box::leak(format!("{{\"x\": \"{}\"}}", "a".repeat(1 << 20)).into_boxed_str());
    assert_eq!(read(big, false), Err(Unread::TooLarge));
}

#[test]
fn a_number_reads_as_int_reads_it() {
    for (given, read) in [
        (json!(7), Some(7)),
        (json!(7.9), Some(7)),
        (json!(-7.9), Some(-7)),
        (json!("7"), Some(7)),
        (json!(" 7 "), Some(7)),
        (json!("1_000"), Some(1000)),
        (json!(true), Some(1)),
        (json!(9_007_199_254_740_992_i64), Some(9_007_199_254_740_992)),
        (json!(9_007_199_254_740_993_i64), None),
        (json!(1e300), None),
        (json!("7.0"), None),
        (json!("seven"), None),
        (json!(null), None),
        (json!([7]), None),
    ] {
        assert_eq!(as_int(&given), read, "{given}");
    }
}

fn fields(value: serde_json::Value) -> Fields {
    let text: &'static str = Box::leak(value.to_string().into_boxed_str());
    read(text, true).unwrap()
}

#[test]
fn each_field_is_refused_as_its_schema_refuses_it() {
    let bad = Err(Problem::bad_request());
    let given = fields(json!({"key": "12", "folder": "INBOX", "uid": null, "seen": 1, "on": false, "verb": "archive",
                             "to": 5, "rows": [{"key": 1}], "flag": "deleted"}));
    assert_eq!(given.required_number("key"), Ok(12));
    assert_eq!(given.number("uid"), Ok(None));
    assert_eq!(given.required_number("uid"), bad);
    assert_eq!(given.required_text("folder"), Ok("INBOX"));
    assert_eq!(given.text("to"), Err(Problem::bad_request()), "pydantic takes no number for a string");
    assert_eq!(given.required_text("missing"), Err(Problem::bad_request()));
    assert_eq!(given.flag("on"), Ok(Some(false)));
    assert_eq!(given.flag("seen"), Err(Problem::bad_request()), "StrictBool takes no 1");
    assert_eq!(given.one_of("verb", &["archive", "trash"]), Ok(Some("archive")));
    assert_eq!(given.one_of("flag", &["flagged", "seen"]), Err(Problem::bad_request()));
    let keys = given.list("rows", |row| row.required_number("key"));
    assert_eq!(keys, Ok(Some(vec![1])));
    let folders = given.list("rows", |row| row.required_text("folder").map(str::to_owned));
    assert_eq!(folders, Err(Problem::bad_request()));
    assert_eq!(given.given("uid"), Some(&json!(null)));
    assert_eq!(given.given("whole"), None);
}

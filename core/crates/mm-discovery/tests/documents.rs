//! The Document Hub, and what is said of a sender as a message opens, as `discovery.domain.documents` and
//! `discovery.domain.contacts` give them.

use mm_discovery::documents::{asked, is_document, kind_of, page, previewable};
use mm_discovery::ports::Row;
use mm_discovery::senders::{address, facts};
use mm_kernel::seams::Moment;
use serde_json::{Value, json};

fn row(value: Value) -> Row {
    match value {
        Value::Object(map) => map,
        _ => Row::new(),
    }
}

fn file(pk: i64, message_id: &str, filename: &str, kind: &str, from: &str) -> Row {
    row(json!({
        "pk": pk, "message_key": pk + 100, "uid": pk, "part_id": "2", "filename": filename, "content_type": kind,
        "size": 10, "message_id": message_id, "account": "ada@example.test", "from_name": "", "from_addr": from,
        "subject": "Files", "date_iso": null, "ts": 0, "folder": "INBOX",
    }))
}

#[test]
fn what_kind_of_file_by_type_and_by_name() {
    assert_eq!(kind_of("application/pdf", "x"), "pdf");
    assert_eq!(kind_of("application/octet-stream", "Plan.PDF"), "pdf");
    assert_eq!(kind_of("", "photo.heic"), "image");
    assert_eq!(kind_of("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "x"), "doc");
    assert_eq!(kind_of("text/csv", "x"), "sheet");
    assert_eq!(kind_of("", "deck.key"), "slide");
    assert_eq!(kind_of("application/x-tar", "x"), "archive");
    assert_eq!(kind_of("text/plain", "x"), "text");
    assert_eq!(kind_of("application/octet-stream", "noext"), "other");
    assert!(!is_document("text/calendar; method=REQUEST") && !is_document("application/pgp-signature"));
}

#[test]
fn a_preview_is_offered_for_an_image_by_its_type_or_its_name_where_the_type_says_nothing() {
    assert!(previewable("image/PNG; name=x", "x"));
    assert!(previewable("application/octet-stream", "a.jpeg"));
    assert!(!previewable("image/svg+xml", "a.svg"));
    assert!(!previewable("application/pdf", "a.png"));
}

#[test]
fn a_limit_or_offset_that_wont_read_is_its_default() {
    let want = asked(Some("nope"), Some("-4"), None, Some("  Plan "), None);
    assert_eq!((want.limit, want.offset, want.needle.as_str()), (400, 0, "plan"));
    assert_eq!(asked(Some("5000"), Some("1_0"), None, None, None).limit, 1000);
    assert_eq!(asked(Some("0"), Some(" 7 "), None, None, None).offset, 7);
    assert_eq!(asked(Some("99999999999999999999999"), None, None, None, None).limit, 1000);
}

#[test]
fn one_file_however_many_copies_and_each_kind_counted() {
    let rows = [
        file(1, "<m@x>", "a.pdf", "application/pdf", "lin@example.test"),
        file(2, "<m@x>", "A.PDF", "application/pdf", "lin@example.test"),
        file(3, "", "a.pdf", "application/pdf", "lin@example.test"),
        file(4, "<n@x>", "b.png", "image/png", "noor@example.test"),
        file(5, "<o@x>", "c.ics", "text/calendar", "noor@example.test"),
    ];
    let all = page(&rows, &asked(None, None, None, None, None), |_| "now".to_owned());
    assert_eq!((all["total"].clone(), all["matched"].clone()), (json!(3), json!(3)));
    assert_eq!(all["facets"], json!({"pdf": 2, "image": 1}));
    assert_eq!(all["docs"][0]["url"], "/api/message/attachment?key=101&folder=INBOX&uid=1&part_id=2");
    assert_eq!(all["docs"][2]["preview"], "/api/message/attachment/preview.html?key=104&folder=INBOX&uid=4&part_id=2");
    assert_eq!(all["docs"][0]["from"], "lin@example.test");
    assert!(all["docs"][0].get("pk").is_none());
    let images = page(&rows, &asked(None, None, Some("image"), None, None), |_| String::new());
    assert_eq!((images["total"].clone(), images["matched"].clone()), (json!(3), json!(1)));
}

#[test]
fn in_sections_by_type_the_kinds_order_and_by_sender_the_newest_first() {
    let rows = [
        file(1, "<a@x>", "b.png", "image/png", "noor@example.test"),
        file(2, "<b@x>", "a.pdf", "application/pdf", "lin@example.test"),
        file(3, "<c@x>", "c.png", "image/png", "Lin@Example.test"),
    ];
    let by_type = page(&rows, &asked(None, None, None, None, Some("type")), |_| String::new());
    assert_eq!(
        by_type["groups"],
        json!([{"id": "pdf", "name": "pdf", "count": 1}, {"id": "image", "name": "image", "count": 2}])
    );
    let order: Vec<Value> =
        by_type["docs"].as_array().unwrap_or(&Vec::new()).iter().map(|d| d["key"].clone()).collect();
    assert_eq!(order, [json!(102), json!(101), json!(103)]);
    let by_sender = page(&rows, &asked(None, None, None, None, Some("sender")), |_| String::new());
    assert_eq!(by_sender["groups"][0], json!({"id": "noor@example.test", "name": "noor@example.test", "count": 1}));
    assert_eq!(by_sender["groups"][1]["count"], 2);
    assert_eq!(by_sender["docs"][2]["group"], "lin@example.test");
}

#[test]
fn a_sender_is_an_address_and_what_is_known_of_them() {
    assert_eq!(address(Some("  Lin@Example.TEST ")), Some("lin@example.test".to_owned()));
    assert_eq!(address(Some("nobody")), None);
    assert_eq!(address(None), None);
    let now = Moment::from_micros(1_793_620_800_000_000);
    let tally = row(json!({"n": 3, "replied": null, "first": 1_793_620_800 - 3 * 86_400, "last": 1, "name": "Lin"}));
    assert_eq!(facts(&tally, now), json!({"count": 3, "replied": 0, "first_ago": "3d"}));
    let none = row(json!({"n": 0, "replied": 0, "first": null}));
    assert_eq!(facts(&none, now), json!({"count": 0, "replied": 0, "first_ago": null}));
}

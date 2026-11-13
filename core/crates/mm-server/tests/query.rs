//! A query string as the Python app's routes read it, and a number in one.

#![allow(clippy::unwrap_used)]

use mm_kernel::errors::Problem;
use mm_server::query::{Query, as_int};

#[test]
fn each_names_first_value_counts_and_a_blank_one_is_none() {
    let query = Query::parse(b"account=ada%40example.test&account=bea&folder=&limit=5&flag&=x");
    assert_eq!(query.text("account"), Some("ada@example.test"));
    assert_eq!(query.text("folder"), None);
    assert_eq!(query.text("flag"), None);
    assert_eq!(query.text(""), Some("x"));
    assert_eq!(query.number("limit", 80), Ok(5));
    assert_eq!(query.number("offset", 0), Ok(0));
}

#[test]
fn escapes_are_read_as_utf_8_and_a_plus_is_a_space() {
    let query = Query::parse(b"folder=%5BGmail%5D%2FSent+Mail&q=caf%C3%A9&bad=%ff%zz&raw=\xc3\xa9");
    assert_eq!(query.text("folder"), Some("[Gmail]/Sent Mail"));
    assert_eq!(query.text("q"), Some("caf\u{e9}"));
    assert_eq!(query.text("bad"), Some("\u{fffd}%zz"));
    assert_eq!(query.text("raw"), Some("\u{c3}\u{a9}"), "a raw byte is its Latin-1 character, as Python reads it");
}

#[test]
fn a_number_is_what_int_reads_up_to_two_to_the_fifty_three() {
    for (given, read) in [("7", Some(7)), (" -7 ", Some(-7)), ("+7", Some(7)), ("1_000", Some(1000))] {
        assert_eq!(as_int(given), read, "{given:?}");
    }
    assert_eq!(as_int("9007199254740992"), Some(1 << 53));
    for given in ["9007199254740993", "lots", "7.5", "", "_1", "1__0", "1e3", "99999999999999999999999"] {
        assert_eq!(as_int(given), None, "{given:?}");
    }
    assert_eq!(Query::parse(b"limit=lots").number("limit", 80), Err(Problem::bad_request()));
}

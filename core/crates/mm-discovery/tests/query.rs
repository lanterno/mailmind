//! The search grammar, as the Python app parsed it: the expected values here are what
//! `mailmind.discovery.domain.query.QuerySpec.parse` gave for the same text, on a machine whose zone is UTC. The
//! Python suite holds the two to the same answers over many more (tests/unit/discovery/test_query_in_rust.py).

use mm_discovery::query::{Kind, MAX_CHARS, QuerySpec, Value, Wall, is_space};

type Read = (Kind, Option<Value>, Option<&'static str>, bool, bool);

/// UTC, the zone the Python suite runs in.
fn utc(wall: Wall) -> Option<i64> {
    let month = i64::from(wall.month);
    let year = i64::from(wall.year) - i64::from(month <= 2);
    let era = year.div_euclid(400);
    let of_era = year - era * 400;
    let of_year = (153 * (month + if month > 2 { -3 } else { 9 }) + 2) / 5 + i64::from(wall.day) - 1;
    let days = era * 146_097 + of_era * 365 + of_era / 4 - of_era / 100 + of_year - 719_468;
    Some(days * 86_400 + i64::from(wall.hour * 3600 + wall.minute * 60 + wall.second))
}

fn parsed(text: &str) -> Vec<Read> {
    QuerySpec::parse(text, utc).clauses.into_iter().map(|c| (c.kind, c.value, c.field, c.negated, c.narrows)).collect()
}

fn words(text: &str) -> Option<Value> {
    Some(Value::Text(text.to_owned()))
}

fn text(found: &str) -> Read {
    (Kind::Text, words(found), None, false, true)
}

fn at(kind: Kind, seconds: i64) -> Vec<Read> {
    vec![(kind, Some(Value::Seconds(seconds)), None, false, true)]
}

#[test]
fn each_operator_reads_as_python_read_it() {
    let cases: [(&str, Read); 22] = [
        ("hello", text("hello")),
        ("from:lin", (Kind::Field, words("lin"), Some("from"), false, true)),
        ("FROM:Lin", (Kind::Field, words("Lin"), Some("from"), false, true)),
        ("from:", text("from:")),
        ("is:UNREAD", (Kind::Flag, None, Some("unread"), false, true)),
        ("is:read", (Kind::Flag, None, Some("read"), false, false)),
        ("is:snoozed", (Kind::Snoozed, None, None, false, false)),
        ("is:newsletter", (Kind::Bucket, words("newsletters/marketing"), None, false, true)),
        ("is:personal", (Kind::Bucket, words("personal/protected"), None, false, false)),
        ("is:nothing", text("is:nothing")),
        ("bucket:receipts", (Kind::Bucket, words("receipts/transactional"), None, false, true)),
        ("bucket:Anything", (Kind::Bucket, words("anything"), None, false, true)),
        ("category:Finance", (Kind::Category, words("finance"), None, false, true)),
        ("label:Travel", (Kind::Label, words("Travel"), None, false, true)),
        ("label:\"Two words\"", (Kind::Label, words("Two words"), None, false, true)),
        ("has:Attachment", (Kind::Attachment, None, None, false, true)),
        ("has:pdf", text("has:pdf")),
        ("in:Archive", (Kind::Folder, words("Archive"), None, false, false)),
        ("account:bea@example.test", (Kind::Account, words("bea@example.test"), None, false, false)),
        ("-in:Trash", (Kind::Folder, words("Trash"), None, true, false)),
        ("-is:snoozed", (Kind::Snoozed, None, None, true, false)),
        ("from:\"Ada L\"", (Kind::Field, words("Ada L"), Some("from"), false, true)),
    ];
    for (query, want) in cases {
        assert_eq!(parsed(query), vec![want], "{query:?}");
    }
    assert_eq!(parsed("in:Sent Items"), vec![(Kind::Folder, words("Sent"), None, false, false), text("Items")]);
}

#[test]
fn a_dash_negates_what_follows_it() {
    assert_eq!(parsed("-hello"), vec![(Kind::Text, words("hello"), None, true, true)]);
    assert_eq!(parsed("-"), vec![text("-")]);
    assert_eq!(parsed("--x"), vec![(Kind::Text, words("-x"), None, true, true)]);
    assert_eq!(parsed("-\"a b\""), vec![(Kind::Text, words("a b"), None, true, true)]);
}

#[test]
fn quotes_keep_a_phrase_and_a_backslash_keeps_what_follows_it() {
    assert_eq!(parsed("\"a phrase\""), vec![text("a phrase")]);
    assert_eq!(parsed("x\"y z\"w"), vec![text("xy zw")]);
    // A quoted empty word, and one of only space, say nothing.
    assert_eq!(parsed("\"\" '' \" \""), vec![]);
    // Outside quotes a backslash keeps any character; inside double quotes only a quote or a backslash, and
    // inside single quotes nothing.
    assert_eq!(parsed("a\\b"), vec![text("ab")]);
    assert_eq!(parsed("\\ a"), vec![text(" a")]);
    assert_eq!(parsed("\"\\\"a\\\"\""), vec![text("\"a\"")]);
    assert_eq!(parsed("\"a\\b\""), vec![text("a\\b")]);
    assert_eq!(parsed("'\\'"), vec![text("\\")]);
}

#[test]
fn words_part_only_at_a_space_a_tab_cr_and_lf() {
    assert_eq!(parsed("a\tb\rc\nd"), vec![text("a"), text("b"), text("c"), text("d")]);
    for inside in ["a\u{b}b", "a\u{a0}b", "a\u{1c}b", "a\u{3000}b"] {
        assert_eq!(parsed(inside), vec![text(inside)]);
    }
    assert_eq!(parsed("caf\u{e9} \u{2615} \u{200b}"), vec![text("caf\u{e9}"), text("\u{2615}"), text("\u{200b}")]);
}

#[test]
fn an_open_quote_or_a_last_backslash_leaves_the_words_between_any_space() {
    assert_eq!(parsed("\"lunch plans"), vec![text("\"lunch"), text("plans")]);
    assert_eq!(parsed("'open \u{1c} split\u{3000}here"), vec![text("'open"), text("split"), text("here")]);
    assert_eq!(parsed("a\\"), vec![text("a\\")]);
    assert_eq!(parsed("\"a\\"), vec![text("\"a\\")]);
}

#[test]
fn space_is_what_python_strips_the_information_separators_too() {
    assert_eq!(parsed("\u{1c}\u{3000}from:lin\u{1f} "), vec![(Kind::Field, words("lin"), Some("from"), false, true)]);
    let python = "\t\n\u{b}\u{c}\r\u{1c}\u{1d}\u{1e}\u{1f} \u{85}\u{a0}\u{1680}\u{2000}\u{200a}\u{2028}\u{2029}\u{202f}\u{205f}\u{3000}";
    assert!(python.chars().all(is_space));
    // A zero-width space is not space to either.
    assert!(!is_space('\u{200b}'));
    assert_eq!(QuerySpec::parse(python, utc), QuerySpec::default());
}

#[test]
fn a_query_is_cut_at_max_chars_after_the_space_around_it() {
    let a = "a".repeat(MAX_CHARS + 50);
    assert_eq!(parsed(&format!("\u{3000}{a}")), vec![text(&a[..MAX_CHARS])]);
    // The cut leaves a backslash last, and shlex gives up.
    let backslash = format!("{}\\ b", "a".repeat(MAX_CHARS - 1));
    assert_eq!(parsed(&backslash), vec![text(&backslash[..MAX_CHARS])]);
    // The cut leaves a quote open.
    let open = format!("   {}\"y z", "x".repeat(MAX_CHARS - 2));
    assert_eq!(parsed(&open), vec![text(&format!("{}\"y", "x".repeat(MAX_CHARS - 2)))]);
    let quote = format!("{} \"a b", "x".repeat(MAX_CHARS - 3));
    assert_eq!(parsed(&quote), vec![text(&"x".repeat(MAX_CHARS - 3)), text("\"a")]);
}

#[test]
fn a_word_is_lowercased_as_python_lowercased_it() {
    // A Kelvin sign lowercases to k, so it names an operator and a bucket.
    assert_eq!(parsed("BUC\u{212a}ET:x"), vec![(Kind::Bucket, words("x"), None, false, true)]);
    assert_eq!(parsed("is:mar\u{212a}eting"), vec![(Kind::Bucket, words("newsletters/marketing"), None, false, true)]);
    let category = |found: &str| vec![(Kind::Category, words(found), None, false, true)];
    assert_eq!(parsed("category:\u{130}stanbul"), category("i\u{307}stanbul"));
    // A capital sigma that ends a word is a final sigma, past case-ignorable characters.
    assert_eq!(parsed("category:\u{3a3}\u{391}\u{3a3}"), category("\u{3c3}\u{3b1}\u{3c2}"));
    assert_eq!(
        parsed("bucket:\u{3a3}\u{391}\u{345}\u{3a3}\u{345}"),
        vec![(Kind::Bucket, words("\u{3c3}\u{3b1}\u{345}\u{3c2}\u{345}"), None, false, true)]
    );
    // Python's Unicode 16, where U+0295 is a lowercase letter and Unicode 17's additions have no case.
    assert_eq!(parsed("category:\u{295}\u{3a3}"), category("\u{295}\u{3c2}"));
    assert_eq!(parsed("category:\u{a7ce}\u{3a3}"), category("\u{a7ce}\u{3c3}"));
    assert_eq!(parsed("category:A\u{1acf}\u{3a3}"), category("a\u{1acf}\u{3c3}"));
}

#[test]
fn the_unicode_tables_are_for_unicode_17() {
    // text.rs tells Unicode 17 from Python 3.14's Unicode 16. A toolchain on another version needs those tables
    // made again, from what tests/unit/discovery/test_query_in_rust.py finds.
    assert_eq!(char::UNICODE_VERSION, (17, 0, 0));
}

#[test]
fn a_time_with_an_offset_is_counted_from_it() {
    assert_eq!(parsed("before:2026-11-02T09:00:00+00:00"), at(Kind::Before, 1_793_610_000));
    assert_eq!(parsed("before:2026-11-02T09:00:00-05:00"), at(Kind::Before, 1_793_628_000));
    assert_eq!(parsed("after:2026-11-02T12:00+05:30"), at(Kind::After, 1_793_601_000));
    assert_eq!(parsed("after:2026-11-02T12:00:00,5+01:00"), at(Kind::After, 1_793_617_200));
    assert_eq!(parsed("after:2026-11-02T12:00+23:59:59.999999"), at(Kind::After, 1_793_534_400));
    // A zero offset is UTC, and the microseconds written after it go.
    assert_eq!(parsed("after:2026-11-02T12:00+00:00:00.5"), at(Kind::After, 1_793_620_800));
    assert_eq!(parsed("before:1970-01-02T00:00:00+00:00"), at(Kind::Before, 86_400));
}

#[test]
fn a_time_with_no_offset_is_where_the_zone_places_it() {
    assert_eq!(parsed("after:2026-11-02"), at(Kind::After, 1_793_577_600));
    assert_eq!(parsed("after:2026-11-02T09:30"), at(Kind::After, 1_793_611_800));
    let an_hour_ahead = |wall| utc(wall).map(|seconds| seconds - 3600);
    let spec = QuerySpec::parse("after:2026-11-02", an_hour_ahead);
    assert_eq!(spec.clauses[0].value, Some(Value::Seconds(1_793_577_600 - 3600)));
    // A time the zone can't place says nothing.
    assert_eq!(QuerySpec::parse("after:2026-11-02 lunch", |_| None).clauses.len(), 1);
}

#[test]
fn seconds_round_to_the_nearest_float_then_lose_their_fraction_as_in_python() {
    // Far from 1970 the float can't hold x.99999, and the nearest is the next second.
    assert_eq!(parsed("before:9999-12-31T23:59:59.99999+00:00"), at(Kind::Before, 253_402_300_800));
    assert_eq!(parsed("before:9999-12-31T23:59:59.99998+00:00"), at(Kind::Before, 253_402_300_799));
    assert_eq!(parsed("after:0001-01-01T00:00:00.999999+00:00"), at(Kind::After, -62_135_596_799));
    // int() cuts toward zero, so half a second before 1970 is 0.
    assert_eq!(parsed("after:1969-12-31T23:59:59.5+00:00"), at(Kind::After, 0));
    assert_eq!(parsed("after:1969-12-31T23:59:59.5"), at(Kind::After, 0));
}

#[test]
fn midnight_at_24_00_is_the_next_day_even_from_a_day_or_a_month_0() {
    assert_eq!(parsed("before:2026-11-02T24:00"), at(Kind::Before, 1_793_664_000));
    assert_eq!(parsed("before:2026-11-00T24:00"), at(Kind::Before, 1_793_491_200));
    assert_eq!(parsed("before:2026-00-00T24:00"), at(Kind::Before, 1_767_225_600));
    assert_eq!(parsed("before:2026-11-02T24:01"), vec![]);
    assert_eq!(parsed("before:2026-02-30T24:00"), vec![]);
}

#[test]
fn a_date_reads_as_cpython_read_it_beyond_iso_8601() {
    let cases = [
        ("after:2026-11-02T12:30:Z", 1_793_622_600),
        ("after:2026-11-02T12:Z", 1_793_620_800),
        ("after:2026-11-02T12:30:45.+05:00", 1_793_604_645),
        // Anything between six digits of a fraction and an offset, and anything after a NUL.
        ("after:2026-11-02T12:30:45.123456garbage+05:00", 1_793_604_645),
        ("after:2026-11-02T12:30:45.123456\u{0}junk", 1_793_622_645),
        ("after:2026-11-02T12:00Z\u{0}junk", 1_793_620_800),
        // Two more digits after the seconds are a fraction.
        ("after:2026-11-02T12304567", 1_793_622_645),
        // Any one character between the date and the time.
        ("after:2026-11-02\u{e9}12:00", 1_793_620_800),
        ("after:20261102", 1_793_577_600),
        // Week dates, one of them with a digit between the date and the time.
        ("after:2026-W45", 1_793_577_600),
        ("after:2026W451", 1_793_577_600),
        ("after:2026-W45-1T10", 1_793_613_600),
        ("after:2026W45112", 1_793_620_800),
        ("after:2026-W53", 1_798_416_000),
        ("after:2020-W53-7", 1_609_632_000),
    ];
    for (query, seconds) in cases {
        assert_eq!(parsed(query), at(Kind::After, seconds), "{query:?}");
    }
}

#[test]
fn what_reads_as_no_time_is_left_out() {
    for query in [
        "before:someday",
        "after:202611",
        "after:2026-13-01",
        "after:2026-11-02T12:30:45.12garbage+05:00",
        "after:2026-11-02T12:00Zjunk",
        "after:2026-11-02T12:00-24:00",
        "after:2027-W53",
    ] {
        assert_eq!(parsed(query), vec![], "{query:?}");
    }
    assert_eq!(parsed("before:"), vec![text("before:")]);
    assert_eq!(parsed("after:2026-11-02 12:00"), [at(Kind::After, 1_793_577_600), vec![text("12:00")]].concat());
}

#[test]
fn parse_with_reads_dates_as_it_is_told() {
    let spec = QuerySpec::parse_with("before:soon -after:later", |written| (written == "soon").then_some(7));
    let read: Vec<_> = spec.clauses.into_iter().map(|c| (c.kind, c.value, c.negated)).collect();
    assert_eq!(read, vec![(Kind::Before, Some(Value::Seconds(7)), false)]);
}

#[test]
fn what_a_query_says_about_itself() {
    let spec = |text| QuerySpec::parse(text, utc);
    for narrow in
        ["from:lin", "lin", "is:unread", "is:receipts", "has:attachment", "after:2026-01-01", "-from:bea from:lin"]
    {
        assert!(spec(narrow).narrows(), "{narrow:?}");
    }
    for wide in ["", "in:INBOX", "account:ada@example.test", "is:read", "is:snoozed", "is:personal", "-lin", "-is:junk"]
    {
        assert!(!spec(wide).narrows(), "{wide:?}");
    }
    assert!(spec("from:lin in:Archive").names_folder());
    assert!(!spec("from:lin -in:Archive").names_folder());
    assert!(spec("-is:snoozed").asks_about_snoozed());
    assert!(!spec("from:lin").asks_about_snoozed());
}

#[test]
fn each_kind_has_the_python_apps_name() {
    let kinds = [
        (Kind::Text, "text"),
        (Kind::Field, "field"),
        (Kind::Flag, "flag"),
        (Kind::Snoozed, "snoozed"),
        (Kind::Bucket, "bucket"),
        (Kind::Category, "category"),
        (Kind::Attachment, "attachment"),
        (Kind::Folder, "folder"),
        (Kind::Label, "label"),
        (Kind::Account, "account"),
        (Kind::Before, "before"),
        (Kind::After, "after"),
    ];
    for (kind, name) in kinds {
        assert_eq!(kind.name(), name);
    }
}

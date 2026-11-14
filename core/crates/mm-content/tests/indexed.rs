//! What search indexes of a body fetched, and the snippet a list shows of it (`content.text`'s `body_text_of` and
//! `snippet_of`): each case's answer is the Python app's.

#![allow(clippy::unwrap_used)]

use std::borrow::Cow;

use mm_content::mime::{Parsed, body_text_of, snippet_of};

fn of(text: Option<&'static str>, html: Option<&'static str>) -> String {
    body_text_of(&Parsed { text: text.map(Cow::Borrowed), html: html.map(Cow::Borrowed), ..Parsed::default() })
}

#[test]
fn the_plain_text_wins_and_an_empty_one_counts_for_nothing() {
    assert_eq!(of(Some("Plain wins"), Some("<p>html</p>")), "Plain wins");
    assert_eq!(of(Some(""), Some("<p>a</p>")), " a ");
    assert_eq!(of(None, None), "");
}

#[test]
fn an_htmls_text_loses_what_holds_none_its_tags_and_its_entities() {
    let html = "<html><head><title>T</title><style>p{}</style></head><body><!-- hidden --><p>Fish &amp; \
                chips&nbsp;today</p></body></html>";
    assert_eq!(of(None, Some(html)), "     Fish & chips\u{a0}today   ");
}

#[test]
fn an_element_never_closed_keeps_its_contents_and_one_closed_in_another_case_goes() {
    let html = "<SCRIPT>x()</script><p>a</p><style>never closed<p>b</p>";
    assert_eq!(of(Some(""), Some(html)), "  a  never closed b ");
}

#[test]
fn a_json_ld_block_goes_with_the_openers_that_wrap_it() {
    let text = r#"Order shipped [{"@context": "https://schema.org", "a": [1, {"b": 2}]}] thanks"#;
    assert_eq!(of(Some(text), None), "Order shipped   thanks");
    assert_eq!(of(Some(r#"x { [ {"@context": 1} ] } y"#), None), "x   y");
    assert_eq!(of(Some(r#"unbalanced {"@context": 1"#), None), r#"unbalanced {"@context": 1"#);
}

#[test]
fn what_is_indexed_stops_at_a_hundred_thousand_characters() {
    let long: &'static str = Box::leak("é".repeat(100_001).into_boxed_str());
    assert_eq!(of(Some(long), None).chars().count(), 100_000);
}

#[test]
fn a_snippet_is_one_paragraph_of_visible_words() {
    assert_eq!(snippet_of("  Hello&nbsp;\u{200b}there\n\n  &amp; you\u{1c} ok "), "Hello there & you ok");
    assert_eq!(snippet_of(&"w ".repeat(200)).chars().count(), 280);
    assert_eq!(snippet_of(""), "");
}

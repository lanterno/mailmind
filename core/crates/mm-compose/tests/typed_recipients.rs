//! Recipients as they were typed: a draft keeps one that isn't an address yet, on one line in a header of MailMind's
//! own (To and Cc) or in its save's record only (Bcc), reopens it as typed, and a send is refused at it in words that
//! name it. That the bytes are Python's, and reopen as Python reopened them, is the builder's and resume's oracles; the
//! served round trip on both servers is tests/api/test_http_draft_addresses.py.
#![allow(clippy::unwrap_used)]

use std::sync::atomic::{AtomicU64, Ordering};

use mm_compose::builder::{
    NO_RECIPIENT, SHOWN_MOST, TYPED_CC, TYPED_TO, build_message, not_an_address, one_line, recipients,
};
use mm_compose::composition::Composed;
use mm_compose::drafting::record_of;
use mm_compose::resume;
use mm_compose::spec::{Built, Draws, MessageSpec, Minting};
use mm_kernel::seams::Moment;

const LIN: &str = "Lin Mori <lin@example.test>";

#[derive(Debug, Default)]
struct Counting(AtomicU64);

impl Draws for Counting {
    fn number(&self) -> u64 {
        self.0.fetch_add(0x9e37_79b9_7f4a_7c15, Ordering::SeqCst)
    }
}

fn owned(list: &[&str]) -> Vec<String> {
    list.iter().map(|s| (*s).to_owned()).collect()
}

fn spec(to: &[&str], cc: &[&str], bcc: &[&str]) -> MessageSpec {
    MessageSpec {
        account: "ada@example.test".to_owned(),
        account_name: "Ada".to_owned(),
        to: owned(to),
        cc: owned(cc),
        bcc: owned(bcc),
        subject: "Plans".to_owned(),
        text: "Hello".to_owned(),
        ..MessageSpec::default()
    }
}

fn built(spec: &MessageSpec, draft: bool) -> Result<Built, String> {
    let draws = Counting::default();
    build_message(spec, draft, Minting { now: Moment::from_micros(1_793_620_800_000_000), pid: 4242, draws: &draws })
}

/// The lines of the bytes' headers, each fold joined to its line.
fn header_lines(raw: &[u8]) -> Vec<String> {
    let text = String::from_utf8(raw.to_vec()).unwrap();
    let head = text.split("\r\n\r\n").next().unwrap().replace("\r\n ", " ");
    head.split("\r\n").map(str::to_owned).collect()
}

fn header(raw: &[u8], name: &str) -> Vec<String> {
    let prefix = format!("{}: ", name.to_lowercase());
    header_lines(raw)
        .into_iter()
        .filter(|l| l.to_lowercase().starts_with(&prefix))
        .map(|l| l[prefix.len()..].to_owned())
        .collect()
}

// -- on one line --------------------------------------------------------------------------------------------------

#[test]
fn what_was_typed_goes_on_one_line() {
    for (typed, line) in [
        ("bob@example", "bob@example"),
        ("  bob@example \t", "bob@example"),
        ("bob@example\r\nBcc: eve@example.test", "bob@example Bcc: eve@example.test"),
        ("a\u{0}b\u{7f}c\u{85}d", "a b c d"),
        ("a\u{2028}b\u{2029}c", "a b c"),
        ("a \r\n\r\n b", "a b"),
        ("\r\n", ""),
        ("li\u{200b}n@example.test", "li\u{200b}n@example.test"),
    ] {
        assert_eq!(one_line(typed), line, "{typed:?}");
    }
}

// -- the words a send is told ------------------------------------------------------------------------------------

#[test]
fn a_refusal_names_what_was_typed_on_one_line_and_where() {
    assert_eq!(not_an_address("Cc", "bob@example"), "\u{201c}bob@example\u{201d} in Cc isn\u{2019}t a valid address.");
    assert_eq!(
        not_an_address("To", "bob@example\r\nBcc: eve@example.test"),
        "\u{201c}bob@example Bcc: eve@example.test\u{201d} in To isn\u{2019}t a valid address."
    );
}

#[test]
fn it_cuts_a_long_one_short() {
    let cut = format!("{}\u{2026}", "x".repeat(SHOWN_MOST - 1));
    assert_eq!(
        not_an_address("To", &"x".repeat(200)),
        format!("\u{201c}{cut}\u{201d} in To isn\u{2019}t a valid address.")
    );
}

#[test]
fn it_writes_a_character_that_shows_as_nothing_as_its_escape() {
    for hidden in ['\u{200b}', '\u{202e}', '\u{2066}', '\u{feff}'] {
        let said = not_an_address("To", &format!("li{hidden}n@example.test"));
        let escaped = format!("\\u{:04x}", u32::from(hidden));
        assert_eq!(said, format!("\u{201c}li{escaped}n@example.test\u{201d} in To isn\u{2019}t a valid address."));
    }
}

// -- recipients as typed -----------------------------------------------------------------------------------------

#[test]
fn a_send_is_refused_at_the_first_that_isnt_an_address() {
    let said = recipients("To", &owned(&[LIN, "bob@example", "cy@"]), false).unwrap_err();
    assert_eq!(said, "\u{201c}bob@example\u{201d} in To isn\u{2019}t a valid address.");
}

#[test]
fn a_draft_keeps_each_that_isnt_after_the_addresses_once_whatever_its_case() {
    let typed = owned(&["bob@example", LIN, "BOB@EXAMPLE", "lin@example.test", " ", "cy@\n"]);
    let (pairs, kept) = recipients("To", &typed, true).unwrap();
    assert_eq!(pairs, vec![("Lin Mori".to_owned(), "lin@example.test".to_owned())]);
    assert_eq!(kept, owned(&["bob@example", "cy@"]));
}

// -- what a draft's bytes carry ----------------------------------------------------------------------------------

#[test]
fn a_draft_keeps_to_and_cc_that_arent_addresses_in_headers_of_its_own() {
    let draft = built(&spec(&[LIN, "bob@example"], &["cy@", "Dee <dee@example.test>"], &[]), true).unwrap();
    assert_eq!(
        (header(&draft.raw, "To"), header(&draft.raw, "Cc")),
        (owned(&[LIN]), owned(&["Dee <dee@example.test>"]))
    );
    assert_eq!(
        (header(&draft.raw, TYPED_TO), header(&draft.raw, TYPED_CC)),
        (owned(&["bob@example"]), owned(&["cy@"]))
    );
    assert_eq!(
        draft.envelope,
        owned(&["lin@example.test", "dee@example.test"]),
        "what isn't an address is no recipient"
    );
}

#[test]
fn a_draft_keeps_bcc_that_isnt_an_address_nowhere_in_its_bytes() {
    let draft = built(&spec(&[LIN], &[], &["eve at example", "Quiet <quiet@example.test>"]), true).unwrap();
    let text = String::from_utf8(draft.raw.clone()).unwrap().to_lowercase();
    assert!(!text.contains("eve at example") && !text.contains("quiet"));
    assert_eq!(draft.envelope, owned(&["lin@example.test", "quiet@example.test"]));
}

#[test]
fn a_line_break_typed_in_a_recipient_makes_no_header() {
    let draft =
        built(&spec(&[LIN, "bob@example\r\nBcc: eve@example.test\r\nX-Injected: yes"], &[], &[]), true).unwrap();
    assert!(header(&draft.raw, "Bcc").is_empty() && header(&draft.raw, "X-Injected").is_empty());
    assert_eq!(header(&draft.raw, TYPED_TO), owned(&["bob@example Bcc: eve@example.test X-Injected: yes"]));
}

#[test]
fn a_send_with_one_in_any_field_is_refused_naming_it() {
    let words = |field: &str| format!("\u{201c}bob@example\u{201d} in {field} isn\u{2019}t a valid address.");
    assert_eq!(built(&spec(&[LIN, "bob@example"], &[], &[]), false).unwrap_err(), words("To"));
    assert_eq!(built(&spec(&[LIN], &["bob@example"], &[]), false).unwrap_err(), words("Cc"));
    assert_eq!(built(&spec(&[LIN], &[], &["bob@example"]), false).unwrap_err(), words("Bcc"));
}

#[test]
fn a_draft_may_have_more_recipients_than_a_send() {
    let many: Vec<String> = (0..=100).map(|i| format!("p{i}@example.test")).collect();
    let many: Vec<&str> = many.iter().map(String::as_str).collect();
    assert_eq!(built(&spec(&many, &[], &[]), true).unwrap().envelope.len(), 101);
    assert_eq!(built(&spec(&many, &[], &[]), false).unwrap_err(), "A message may have at most 100 recipients.");
}

#[test]
fn a_send_to_no_one_is_refused_in_a_sentence() {
    assert_eq!(built(&spec(&["", " "], &[], &[]), false).unwrap_err(), NO_RECIPIENT);
}

#[test]
fn an_account_that_isnt_an_address_is_named_as_from() {
    let mut from = spec(&[LIN], &[], &[]);
    from.account = "ada@example".to_owned();
    assert_eq!(built(&from, true).unwrap_err(), "\u{201c}ada@example\u{201d} in From isn\u{2019}t a valid address.");
}

// -- what a save records, and what reopens ------------------------------------------------------------------------

#[test]
fn bcc_that_isnt_an_address_yet_is_recorded_as_typed_after_the_addresses_on_one_line_and_once() {
    let composed = Composed {
        spec: spec(
            &[LIN],
            &[],
            &["eve at example", "Lin Ma <lin@example.test>", "EVE AT EXAMPLE", "bo@\r\nBcc: x", " "],
        ),
        ..Composed::default()
    };
    let record = record_of(&composed, &Built::default());
    let bcc: Vec<String> = serde_json::from_str(record.bcc.as_deref().unwrap()).unwrap();
    assert_eq!(bcc, owned(&["Lin Ma <lin@example.test>", "eve at example", "bo@ Bcc: x"]));
}

#[test]
fn a_recorded_chip_naming_no_address_reopens_as_one_that_does() {
    let recorded = r#"["lin@example.test", "eve at example"]"#;
    assert_eq!(
        resume::bcc_of(&owned(&["lin@example.test"]), Some(recorded), true),
        (owned(&["lin@example.test", "eve at example"]), 0)
    );
    assert_eq!(resume::bcc_of(&owned(&["lin@example.test"]), Some(recorded), false), (owned(&["lin@example.test"]), 1));
    assert_eq!(resume::bcc_of(&[], Some(r#"["", " \r\n "]"#), true), (vec![], 0), "a chip of nothing is none");
}

#[test]
fn what_was_typed_that_isnt_an_address_reopens_after_the_addresses_once() {
    let raw = "From: ada@example.test\r\nTo: Lin <lin@example.test>\r\nX-MailMind-Draft-To: bob@example\r\n\
               X-MailMind-Draft-To: BOB@EXAMPLE\r\nX-MailMind-Draft-To: LIN@example.test\r\nX-MailMind-Draft-Cc: a very \
               long\r\n folded line\r\nX-MailMind-Draft-Cc: =?utf-8?q?Zo=C3=AB?=\r\n\r\nHello\r\n";
    let draft = resume::read(raw.as_bytes()).unwrap();
    assert_eq!(draft.to, owned(&["Lin <lin@example.test>", "bob@example"]));
    assert_eq!(draft.cc, owned(&["a very long folded line", "Zo\u{eb}"]));
}

//! A message's identity, as the Python app computes it: the expected values here are what
//! `mailmind.mailbox.domain.identity.of` returned for the same arguments. The Python suite holds the two to the same
//! answers over many more (tests/unit/mailbox/test_identity_in_rust.py).

use mm_kernel::identity::of;

const ADA: Option<&str> = Some("ada@example.test");
const DATE: Option<&str> = Some("2026-11-02T09:00:00+00:00");
const LUNCH: Option<&str> = Some("Lunch on Friday?");
const LUNCH_ENVELOPE: &str = "~b28f90e4f453c8a0a587ffbd41ccae155b9c522a77881ea1ed2c24280663b837";

#[test]
fn a_message_id_is_its_identity() {
    assert_eq!(of(Some("<lunch-1@example.test>"), ADA, DATE, LUNCH), "<lunch-1@example.test>");
}

#[test]
fn whitespace_a_sender_folded_into_the_message_id_is_left_out() {
    assert_eq!(of(Some(" <lunch-1@\r\n example.test>\t"), ADA, DATE, LUNCH), "<lunch-1@example.test>");
}

#[test]
fn whitespace_is_what_python_splits_at_including_the_information_separators() {
    let every = "\t\n\u{b}\u{c}\r\u{1c}\u{1d}\u{1e}\u{1f} \u{85}\u{a0}\u{1680}\u{2000}\u{200a}\u{2028}\u{2029}\u{202f}\u{205f}\u{3000}";
    assert_eq!(of(Some(&format!("<a{every}b@example.test>")), None, None, None), "<ab@example.test>");
    // A zero-width space is not whitespace to either.
    assert_eq!(of(Some("<a\u{200b}b@example.test>"), None, None, None), "<a\u{200b}b@example.test>");
}

#[test]
fn a_message_with_no_message_id_is_known_by_its_envelope() {
    assert_eq!(of(None, ADA, DATE, LUNCH), LUNCH_ENVELOPE);
}

#[test]
fn a_message_id_of_only_whitespace_counts_as_none() {
    assert_eq!(of(Some(" \t "), ADA, DATE, LUNCH), LUNCH_ENVELOPE);
}

#[test]
fn an_envelope_with_nothing_in_it_still_has_an_identity() {
    assert_eq!(of(None, None, None, None), "~565d240f5343e625ae579a4d45a770f1f02c6368b5ed4d06da4fbe6f47c28866");
}

#[test]
fn an_envelope_is_hashed_as_utf_8() {
    assert_eq!(
        of(None, Some("bea@example.test"), None, Some("Caf\u{e9} \u{2615} menu")),
        "~ae58fe7db62c9bc7998aa8daec89be4730e5b8bb1a8315eb831343fb627a51d3"
    );
}

//! Why a sender is worth cleaning up, and the AI's pass over the Screener, with no I/O
//! (`tests/unit/assistant/test_rationale.py` and `test_screening.py`'s): the values here are what the Python app's
//! `domain.rationale` and `domain.screening` give for the same input.

use mm_assistant::ports::{SenderRecent, SenderStats};
use mm_assistant::{rationale, screening};
use serde_json::{Map, Value, json};

const LOOM: &str = "news@loom.example.test";
const THIMBLE: &str = "orders@thimble.example.test";
const NOW: f64 = 1_000_000_000.0;

fn row(addr: &str) -> SenderStats {
    SenderStats {
        addr: addr.into(),
        name: Some("Loom".into()),
        n: 47,
        unseen: Some(47),
        replied: Some(0),
        first_ts: Some(NOW as i64 - 180 * 86400),
        last_ts: Some(NOW as i64 - 2 * 86400),
    }
}

fn object(value: Value) -> Map<String, Value> {
    value.as_object().cloned().expect("an object")
}

fn texts(raw: &[&str]) -> Vec<String> {
    raw.iter().map(|s| (*s).to_owned()).collect()
}

#[test]
fn a_list_names_each_sender_once_in_lower_case_or_nobody() {
    assert_eq!(rationale::named(&texts(&[" News@Loom.Example.test ", LOOM, THIMBLE])), Some(texts(&[LOOM, THIMBLE])));
    let long = format!("{}@example.test", "a".repeat(400));
    let many: Vec<String> = (0..=rationale::MAX_SENDERS).map(|i| format!("a{i}@example.test")).collect();
    for raw in [vec![], texts(&["nobody"]), vec![long], many] {
        assert_eq!(rationale::named(&raw), None);
    }
}

#[test]
fn the_model_is_told_the_numbers_and_only_those() {
    assert_eq!(
        rationale::line(1, &row(LOOM), NOW).expect("a line"),
        "[1] Loom <news@loom.example.test>: 47 messages over 6 months, 47 of them never opened, 0 replied to, \
         the latest 2 days ago."
    );
    let odd = SenderStats {
        addr: "x@y".into(),
        name: None,
        n: 3,
        unseen: None,
        replied: Some(1),
        first_ts: Some(NOW as i64 - 800 * 86400 + 5),
        last_ts: Some(NOW as i64 + 3 * 86400),
    };
    assert_eq!(
        rationale::line(4, &odd, NOW).expect("a line"),
        "[4] x@y <x@y>: 3 messages over 2 years, None of them never opened, 1 replied to, the latest under a day ago."
    );
    assert_eq!(rationale::fallback(&odd, NOW).expect("words"), "None of 3 unopened over 2 years, 1 replied to.");
    assert!(rationale::line(1, &SenderStats { first_ts: None, ..row(LOOM) }, NOW).is_err());
}

#[test]
fn how_long_reads_in_words() {
    for (days, words) in [(0, "under a day"), (1, "1 day"), (59, "59 days"), (60, "2 months"), (800, "2 years")] {
        assert_eq!(rationale::span(f64::from(days) * 86400.0), words);
    }
    assert_eq!(rationale::span(-5.0), "under a day");
}

#[test]
fn a_rationale_stands_for_the_sender_it_numbers_and_a_passed_over_one_gets_the_numbers() {
    let rows = [row(LOOM), row(THIMBLE)];
    let said = rationale::clean(&object(json!({"rationales": [{"id": 2, "text": " Never opened. "}]})), &rows, NOW);
    assert_eq!(
        Value::Object(said.expect("rationales")),
        json!({LOOM: "47 of 47 unopened over 6 months, 0 replied to.", THIMBLE: "Never opened."})
    );
    for n in [json!(0), json!(2), Value::Null, json!("1"), json!(true), json!(1.0)] {
        let said = rationale::clean(&object(json!({"rationales": [{"id": n, "text": "made up"}]})), &rows[..1], NOW);
        assert_eq!(said.expect("rationales")[LOOM], "47 of 47 unopened over 6 months, 0 replied to.");
    }
    for told in [json!({}), json!({"rationales": "text"}), json!({"rationales": [1, "x", null]})] {
        let said = rationale::clean(&object(told), &rows[..1], NOW).expect("rationales");
        assert!(said[LOOM].as_str().expect("text").starts_with("47 of 47"));
    }
    let first = json!({"rationales": [{"id": 1, "text": "  "}, {"id": 1, "text": "First."}, {"id": 1, "text": "No."}]});
    assert_eq!(rationale::clean(&object(first), &rows[..1], NOW).expect("rationales")[LOOM], "First.");
    let long = rationale::clean(&object(json!({"rationales": [{"id": 1, "text": "x".repeat(900)}]})), &rows[..1], NOW);
    assert_eq!(long.expect("rationales")[LOOM].as_str().map(str::len), Some(rationale::TEXT_MAX));
}

const ADDRS: [&str; 2] = ["orders@thimble.example.test", "lin@example.test"];

fn verdict(n: Value, verdict: Value, confidence: Value) -> Map<String, Value> {
    object(json!({"verdicts": [{"id": n, "verdict": verdict, "confidence": confidence, "why": "a shop you use"}]}))
}

fn shown() -> Vec<String> {
    texts(&ADDRS)
}

#[test]
fn a_sure_verdict_stands_for_the_sender_it_numbers() {
    let told = object(json!({"verdicts": [{"id": 2, "verdict": "block", "confidence": 0.9, "why": "bulk mail"}]}));
    assert_eq!(
        screening::clean(&told, &shown()),
        [json!({"from_addr": "lin@example.test", "verdict": "block", "confidence": 0.9, "why": "bulk mail"})]
    );
    let sure = |c: Value| screening::clean(&verdict(json!(1), json!("keep"), c), &shown());
    for unsure in [json!(0.79), json!(0), json!(-1), Value::Null, json!("0.95"), json!(true)] {
        assert_eq!(sure(unsure), Vec::<Value>::new());
    }
    assert_eq!(sure(json!(screening::MIN_CONFIDENCE))[0]["confidence"], json!(0.8));
    assert_eq!(sure(json!(7))[0]["confidence"], json!(1.0));
    assert_eq!(sure(json!(0.875))[0]["confidence"], json!(0.88));
}

#[test]
fn a_number_it_wasnt_shown_or_a_verdict_that_is_no_rules_says_nothing() {
    for n in [json!(0), json!(3), json!(-1), Value::Null, json!("1"), json!(true), json!(1.0)] {
        assert_eq!(screening::clean(&verdict(n, json!("keep"), json!(0.95)), &shown()), Vec::<Value>::new());
    }
    for said in [json!("remove"), json!("allow"), json!(""), Value::Null] {
        assert_eq!(screening::clean(&verdict(json!(1), said, json!(0.95)), &shown()), Vec::<Value>::new());
    }
    for told in [json!({}), json!({"verdicts": "keep them all"}), json!({"verdicts": ["keep", 1, null]})] {
        assert_eq!(screening::clean(&object(told), &shown()), Vec::<Value>::new());
    }
}

#[test]
fn the_first_word_on_a_sender_stands_and_a_reason_is_held_to_its_length() {
    let told = object(json!({"verdicts": [
        {"id": 1, "verdict": "maybe", "confidence": 1, "why": "x"},
        {"id": 1, "verdict": "keep", "confidence": 0.95, "why": "y".repeat(500)},
        {"id": 1, "verdict": "block", "confidence": 0.95, "why": "z"},
        {"id": 2, "verdict": "block", "confidence": 0.95, "why": 5}]}));
    let got = screening::clean(&told, &shown());
    let said: Vec<(&str, usize)> = got
        .iter()
        .map(|s| (s["verdict"].as_str().unwrap_or_default(), s["why"].as_str().map_or(0, str::len)))
        .collect();
    assert_eq!(said, [("keep", screening::WHY_MAX), ("block", 1)]);
    assert_eq!(got[1]["why"], "5");
}

#[test]
fn a_senders_line_names_their_subjects_and_the_ais_first_word_and_is_held_to_its_length() {
    let sender = json!({"from_name": "Lin", "from_addr": "lin@example.test", "count": 3});
    let recent = [
        SenderRecent { subject: None, summary: Some(String::new()) },
        SenderRecent {
            subject: Some("Lunch".into()),
            summary: Some(
                "Wants lunch on Thursday at the place by the river, and asks whether noon still suits you.".into(),
            ),
        },
    ];
    assert_eq!(
        screening::line(2, &sender, &recent),
        "[2] Lin <lin@example.test>, 3 in the inbox. Subjects: (no subject); Lunch. AI on the latest: Wants lunch on \
         Thursday at the place by the river, and asks whether noon still s"
    );
    let long = [SenderRecent { subject: Some("s".repeat(2000)), summary: None }];
    let text = screening::line(1, &json!({"from_name": "N", "from_addr": ADDRS[0], "count": 1}), &long);
    assert!(text.chars().count() == 400 && text.starts_with("[1] N <orders@thimble.example.test>"));
}

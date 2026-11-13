//! The rule book as it is kept, and who is worth cleaning up after, held to the Python app's answers
//! (tests/unit/curation/test_rulebook.py, test_noise.py).

use mm_curation::cleanup::{BUCKETS, LIMIT, MIN_COUNT, age_in_days, noise_score, ranked};
use mm_curation::ports::NoisySender;
use mm_curation::rulebook::{Rules, decided, is_token, token_of};
use serde_json::json;

const NOW: f64 = 1_800_000_000.0;
const DAY: f64 = 86_400.0;

// ── the rule book ────────────────────────────────────────────────────────

#[test]
fn a_document_is_read_lowered_without_repeats() {
    let rules = Rules::normalized(&json!({
        "keep_senders": ["Lin@Example.test", "lin@example.test"],
        "block_domains": ["Prizes.example.test"],
        "expire_hours": {"News@Loom.example.test": 48, "gone@example.test": 0},
        "filters": [{"id": "f-1", "q": "from:lin", "star": true}],
    }))
    .expect("rules");
    assert_eq!(rules.list("keep_senders"), ["lin@example.test"]);
    assert_eq!(rules.list("block_domains"), ["prizes.example.test"]);
    assert_eq!(rules.expire_hours, [("news@loom.example.test".to_owned(), 48)]);
    let listed = rules.to_json();
    assert_eq!(listed["filters"], json!([{"id": "f-1", "q": "from:lin", "star": true, "read": false}]));
    assert_eq!(listed["expire_hours"], json!({"news@loom.example.test": 48}));
}

#[test]
fn a_document_that_isnt_rules_reads_as_none() {
    assert_eq!(Rules::normalized(&json!([])), None);
    assert_eq!(Rules::normalized(&json!({"keep_senders": "lin@example.test"})), None);
    assert_eq!(Rules::normalized(&json!({"filters": {}})), None);
    assert_eq!(Rules::normalized(&json!({})), Some(Rules::default()));
}

#[test]
fn a_verdict_moves_a_token_between_its_lists() {
    let mut rules = Rules::default();
    assert_eq!(rules.add("lin@example.test", "keep"), "keep_senders");
    assert_eq!(rules.add("lin@example.test", "block"), "block_senders");
    assert_eq!(rules.list("keep_senders"), Vec::<String>::new());
    assert_eq!(rules.add("prizes.example.test", "block"), "block_domains");
    assert!(decided("anyone@prizes.example.test", &rules.sets()));
    assert!(!decided("anyone@example.test", &rules.sets()));
    rules.remove("prizes.example.test");
    assert!(!decided("anyone@prizes.example.test", &rules.sets()));
}

#[test]
fn a_token_is_an_address_or_a_domain() {
    assert_eq!(token_of("  Lin@Example.test "), "lin@example.test");
    assert!(is_token("lin@example.test") && is_token("example.test"));
    assert!(!is_token("lin") && !is_token(""));
}

#[test]
fn saved_lists_are_sorted_without_repeats() {
    let mut rules = Rules::default();
    rules.add("b@example.test", "keep");
    rules.add("a@example.test", "keep");
    rules.add("b@example.test", "keep");
    assert_eq!(rules.for_saving()["keep_senders"], json!(["a@example.test", "b@example.test"]));
}

// ── who to clean up after ────────────────────────────────────────────────

fn row(addr: &str, n: i64, unseen: i64, last_ts: Option<f64>, bucket: &str) -> NoisySender {
    NoisySender {
        addr: addr.to_owned(),
        name: Some("News".to_owned()),
        n,
        unseen,
        last_ts: last_ts.map(|t| t as i64),
        last_iso: Some("2026-11-01T00:00:00+00:00".to_owned()),
        bucket: Some(bucket.to_owned()),
    }
}

fn news(n: i64) -> NoisySender {
    row("a@news.example.test", n, 0, Some(NOW), "newsletters/marketing")
}

#[test]
fn the_score_is_the_count_weighted_by_unread_and_fading_over_a_month() {
    assert!((noise_score(10, 0, 0.0) - 3.0).abs() < 1e-9);
    assert!((noise_score(10, 10, 0.0) - 10.0).abs() < 1e-9);
    assert!((noise_score(10, 0, 30.0) - 3.0 / std::f64::consts::E).abs() < 1e-9);
    assert_eq!(noise_score(0, 0, 0.0), 0.0);
}

#[test]
fn with_no_date_or_one_ahead_of_now_it_counts_as_today() {
    assert_eq!(age_in_days(Some((NOW - 3.0 * DAY) as i64), NOW), 3.0);
    assert_eq!(age_in_days(None, NOW), 0.0);
    assert_eq!(age_in_days(Some(0), NOW), 0.0);
    assert_eq!(age_in_days(Some((NOW + DAY) as i64), NOW), 0.0);
}

#[test]
fn it_is_newsletters_and_notifications_three_messages_and_forty_senders() {
    assert_eq!((BUCKETS, MIN_COUNT, LIMIT), (["newsletters/marketing", "automated-notifications"], 3, 40));
}

#[test]
fn a_candidate_is_its_rows_numbers_and_what_they_come_to() {
    let [got] =
        <[_; 1]>::try_from(ranked(&[row("a@news.example.test", 8, 2, Some(NOW), "newsletters/marketing")], NOW, 40))
            .expect("one");
    assert_eq!((got.name.as_str(), got.domain.as_str(), got.count, got.unseen), ("News", "news.example.test", 8, 2));
    assert_eq!((got.unseen_ratio, got.score, got.list_likely), (0.25, 3.8, true));
    let notification = row("a@news.example.test", 10, 0, Some(NOW), "automated-notifications");
    assert!(!ranked(&[notification], NOW, 40)[0].list_likely);
}

#[test]
fn the_ratio_and_the_score_are_shown_to_two_places_as_python_rounds() {
    let got = &ranked(&[row("a@news.example.test", 7, 2, Some(NOW), "newsletters/marketing")], NOW, 40)[0];
    assert_eq!((got.unseen_ratio, got.score), (0.29, 3.5));
    // A tie at the third place rounds to even, as Python's round does.
    let got = &ranked(&[row("a@news.example.test", 8, 1, Some(NOW), "newsletters/marketing")], NOW, 40)[0];
    assert_eq!(got.unseen_ratio, 0.12);
}

#[test]
fn the_score_is_read_at_its_age() {
    let got =
        &ranked(&[row("a@news.example.test", 20, 0, Some(NOW - 30.0 * DAY), "newsletters/marketing")], NOW, 40)[0];
    assert_eq!(got.score, 2.21);
}

#[test]
fn a_score_at_the_bar_makes_it_and_one_just_under_does_not() {
    assert_eq!(ranked(&[news(5)], NOW, 40).iter().map(|c| c.score).collect::<Vec<_>>(), [1.5]);
    let older = row("a@news.example.test", 5, 0, Some(NOW - 864.0), "newsletters/marketing");
    assert!(ranked(&[older], NOW, 40).is_empty());
}

#[test]
fn the_noisiest_come_first_and_ties_keep_their_order() {
    let rows = [
        row("a@example.test", 5, 0, Some(NOW), "newsletters/marketing"),
        row("b@example.test", 20, 0, Some(NOW), "newsletters/marketing"),
        row("c@example.test", 10, 0, Some(NOW), "newsletters/marketing"),
    ];
    let order = |limit| ranked(&rows, NOW, limit).into_iter().map(|c| c.addr).collect::<Vec<_>>();
    assert_eq!(order(40), ["b@example.test", "c@example.test", "a@example.test"]);
    assert_eq!(order(2), ["b@example.test", "c@example.test"]);
    let tied = [
        row("older@example.test", 10, 0, Some(NOW - 86.0), "newsletters/marketing"),
        row("newer@example.test", 10, 0, Some(NOW), "newsletters/marketing"),
    ];
    let order: Vec<_> = ranked(&tied, NOW, 40).into_iter().map(|c| c.addr).collect();
    assert_eq!(order, ["older@example.test", "newer@example.test"]);
}

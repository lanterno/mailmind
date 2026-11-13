//! Whether a search can be a filter, held to the Python app's answers (tests/unit/curation/test_filters.py).

use mm_discovery::arrival::refusal;
use mm_discovery::query::MAX_CHARS;

fn why(q: &str) -> Option<String> {
    refusal(q, |_| Some(0))
}

#[test]
fn what_can_be_judged_of_mail_as_it_arrives() {
    for q in [
        "from:lin",
        "subject:invoice",
        "to:ada",
        "cc:ada",
        "lunch",
        "\"a phrase\"",
        "is:newsletter",
        "bucket:spam",
        "from:lin is:unread",
        "from:lin -subject:sale",
        "in:INBOX from:lin",
        "from:lin account:ada@example.test",
        "from:lin -in:Archive",
    ] {
        assert_eq!(why(q), None, "{q}");
    }
}

#[test]
fn what_cant_says_why_in_words() {
    let long = format!("from:lin {}", "x".repeat(MAX_CHARS));
    for (q, says) in [
        ("", "something to look for"),
        ("in:INBOX", "something to look for"),
        ("is:unread", "something to look for"),
        ("is:flagged", "something to look for"),
        ("account:ada@example.test", "something to look for"),
        ("-from:lin", "something to look for"),
        ("from:lin has:attachment", "attachments"),
        ("from:lin category:finance", "AI"),
        ("from:lin is:snoozed", "snoozed"),
        ("from:lin before:2026-01-01", "before:"),
        ("from:lin after:2026-01-01", "after:"),
        ("body:invoice", "body"),
        ("from:lin -body:sale", "body"),
        ("from:lin in:Archive", "in:INBOX"),
        ("from:lin in:inbox", "in:INBOX"),
        (long.as_str(), "too long"),
    ] {
        let said = why(q).unwrap_or_default();
        assert!(said.contains(says), "{q}: {said}");
    }
}

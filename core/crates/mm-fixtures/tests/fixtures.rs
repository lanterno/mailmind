//! The fixture is fictional, and the same every time for the same seed.

#![allow(clippy::unwrap_used)]

use mm_fixtures::{ACCOUNTS, FIXTURE_KEY, FIXTURE_KEY_HEX, messages};

#[test]
fn the_same_seed_gives_the_same_mailbox() {
    let a: Vec<_> = messages(500, 7).map(|m| (m.subject, m.from_addr, m.ts)).collect();
    let b: Vec<_> = messages(500, 7).map(|m| (m.subject, m.from_addr, m.ts)).collect();
    let c: Vec<_> = messages(500, 8).map(|m| (m.subject, m.from_addr, m.ts)).collect();
    assert_eq!(a, b);
    assert_ne!(a, c);
}

#[test]
fn every_address_is_at_example_test() {
    for m in messages(5000, 7) {
        assert!(m.from_addr.ends_with(".example.test"), "{}", m.from_addr);
        assert!(ACCOUNTS.contains(&m.account.as_str()));
        assert!(m.message_id.ends_with("@mail.example.test>"));
    }
}

#[test]
fn messages_come_newest_first_with_unique_uids_per_account() {
    let all: Vec<_> = messages(5000, 7).collect();
    assert!(all.windows(2).all(|p| p[0].ts > p[1].ts));
    let mut seen = std::collections::HashSet::new();
    assert!(all.iter().all(|m| seen.insert((m.account.clone(), m.uid))));
}

#[test]
fn some_mail_is_unread_some_flagged_some_with_attachments() {
    let all: Vec<_> = messages(5000, 7).collect();
    let share = |f: fn(&mm_store::NewMessage) -> bool| all.iter().filter(|m| f(m)).count() * 100 / all.len();
    assert!((10..=20).contains(&share(|m| !m.seen)));
    assert!((1..=5).contains(&share(|m| m.flagged)));
    assert!((8..=16).contains(&share(|m| m.has_attach)));
}

#[test]
fn the_key_as_bytes_is_the_key_as_hex() {
    let hex: String = FIXTURE_KEY.iter().map(|b| format!("{b:02x}")).collect();
    assert_eq!(hex, FIXTURE_KEY_HEX);
}

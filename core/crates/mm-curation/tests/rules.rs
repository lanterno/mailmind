//! What a rule covers, as `mailmind.curation.domain.rulebook.domain_covers` and `domain_listed` answered.

use std::collections::HashSet;

use mm_curation::rules::{RuleSets, domain_covers, domain_listed};

#[test]
fn a_domain_rule_covers_the_domain_and_its_sub_domains() {
    assert!(domain_covers("example.test", "example.test"));
    assert!(domain_covers("mail.example.test", "example.test"));
    assert!(domain_covers("a.b.example.test", "example.test"));
}

#[test]
fn a_domain_rule_covers_no_lookalike() {
    assert!(!domain_covers("badexample.test", "example.test"));
    assert!(!domain_covers("example.test", "mail.example.test"));
    assert!(!domain_covers("example.test.evil", "example.test"));
}

#[test]
fn an_empty_entry_covers_the_empty_domain_and_one_ending_in_a_dot() {
    assert!(domain_covers("", ""));
    assert!(domain_covers("example.test.", ""));
    assert!(!domain_covers("example.test", ""));
}

#[test]
fn a_listed_domain_is_found_by_its_own_name_or_a_parent() {
    let listed: HashSet<String> = ["example.test", "studio.sg"].map(String::from).into();
    assert!(domain_listed("example.test", &listed));
    assert!(domain_listed("a.mail.example.test", &listed));
    assert!(domain_listed("x.studio.sg", &listed));
    assert!(!domain_listed("sg", &listed));
    assert!(!domain_listed("badexample.test", &listed));
    assert!(!domain_listed("", &listed));
}

#[test]
fn every_entry_of_a_list_is_asked() {
    let many: Vec<String> = (0..5000).map(|n| format!("d{n}.example.test")).collect();
    let listed: HashSet<String> = many.iter().cloned().collect();
    for domain in &many {
        assert!(domain_listed(&format!("mail.{domain}"), &listed));
        assert_eq!(domain_listed(domain, &listed), many.iter().any(|entry| domain_covers(domain, entry)));
    }
    assert!(!domain_listed("d5000.example.test", &listed));
}

#[test]
fn each_list_answers_for_itself() {
    let owned = |entries: &[&str]| entries.iter().map(|entry| entry.to_string()).collect::<Vec<_>>();
    let rules = RuleSets::new(
        owned(&["ada@example.test"]),
        owned(&["bo@example.test"]),
        owned(&["kept.example.test"]),
        owned(&["blocked.example.test"]),
    );
    assert!(rules.keeps_sender("ada@example.test") && !rules.blocks_sender("ada@example.test"));
    assert!(rules.blocks_sender("bo@example.test") && !rules.keeps_sender("bo@example.test"));
    assert!(rules.keeps_domain("mail.kept.example.test") && !rules.blocks_domain("mail.kept.example.test"));
    assert!(rules.blocks_domain("blocked.example.test") && !rules.keeps_domain("blocked.example.test"));
    // A sender rule names the address exactly.
    assert!(!rules.keeps_sender("Ada@example.test") && !rules.keeps_sender("ada@mail.example.test"));
}

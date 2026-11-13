//! A sweep's stored plan: how an inbox falls into buckets, who sent most of each, and the rules it reads again.

#![allow(clippy::unwrap_used)]

use std::sync::Arc;

use mm_curation::classifier::Bucket;
use mm_curation::ports::{RuleBook, Stored, StoredInbox};
use mm_curation::rules::RuleSets;
use mm_curation::sweep_plan::{self, StoredPlans};
use mm_kernel::errors::Fault;
use serde_json::{Value, json};

fn from(addr: &str) -> Stored {
    Stored { from_addr: Some(addr.into()), subject: Some("Lunch on Friday?".into()), ..Stored::default() }
}

fn mail(pairs: &[(Bucket, &str)]) -> Vec<(Bucket, String)> {
    pairs.iter().map(|(b, s)| (*b, (*s).to_owned())).collect()
}

#[test]
fn a_tally_counts_each_bucket_and_what_is_junk() {
    let tally = sweep_plan::tally(mail(&[
        (Bucket::Personal, "lin@example.test"),
        (Bucket::Spam, "winner@prizes.example.test"),
        (Bucket::Personal, "noor@example.test"),
        (Bucket::Marketing, "news@loom.example.test"),
    ]));
    assert_eq!(tally["in_inbox"], json!(4));
    assert_eq!(tally["by_bucket"], json!({"other/personal-ish": 2, "spam/scam": 1, "newsletters/marketing": 1}));
    assert_eq!(tally["junk"], json!(2));
    assert_eq!(tally["keep"], json!(2));
}

#[test]
fn every_junk_bucket_names_its_senders_even_when_it_holds_none() {
    let tally = sweep_plan::tally(mail(&[(Bucket::Receipts, "orders@thimble.example.test")]));
    assert_eq!(
        tally["top_senders"],
        json!({
            "receipts/transactional": ["orders@thimble.example.test"],
            "spam/scam": [],
            "blocked": [],
            "newsletters/marketing": [],
            "automated-notifications": [],
        })
    );
    assert_eq!(tally["by_bucket"], json!({"receipts/transactional": 1}));
}

#[test]
fn an_empty_inbox_tallies_to_nothing() {
    let tally = sweep_plan::tally(Vec::new());
    assert_eq!((&tally["in_inbox"], &tally["junk"], &tally["keep"]), (&json!(0), &json!(0), &json!(0)));
    assert_eq!(tally["by_bucket"], json!({}));
}

#[test]
fn the_top_senders_are_four_at_most_most_first_and_on_a_tie_the_first_to_come() {
    let p = Bucket::Personal;
    let tally = sweep_plan::tally(mail(&[
        (p, "a@example.test"),
        (p, "b@example.test"),
        (p, "c@example.test"),
        (p, "b@example.test"),
        (p, "d@example.test"),
        (p, "e@example.test"),
        (p, "e@example.test"),
    ]));
    assert_eq!(
        tally["top_senders"]["other/personal-ish"],
        json!(["b@example.test", "e@example.test", "a@example.test", "c@example.test"])
    );
}

#[test]
fn a_stored_message_is_judged_by_its_sender_lower_cased() {
    let (bucket, sender) = sweep_plan::stored_bucket(&from("Lin@Example.TEST"), &RuleSets::default());
    assert_eq!((bucket, sender.as_str()), (Bucket::Personal, "lin@example.test"));
}

#[test]
fn a_stored_message_ingest_found_listed_counts_as_a_list_s() {
    let rules = RuleSets::default();
    let plain = sweep_plan::stored_bucket(&from("hello@cafe.example.test"), &rules).0;
    let unsubscribable =
        Stored { list_unsub_raw: Some("<https://cafe.example.test/u>".into()), ..from("hello@cafe.example.test") };
    let marketed = Stored { bucket: Some("newsletters/marketing".into()), ..from("hello@cafe.example.test") };
    let blank = Stored { list_unsub_raw: Some(String::new()), ..from("hello@cafe.example.test") };
    assert_eq!(plain, Bucket::Personal);
    assert_eq!(sweep_plan::stored_bucket(&unsubscribable, &rules).0, Bucket::Marketing);
    assert_eq!(sweep_plan::stored_bucket(&marketed, &rules).0, Bucket::Marketing);
    assert_eq!(sweep_plan::stored_bucket(&blank, &rules).0, Bucket::Personal);
}

#[test]
fn a_stored_message_with_nothing_said_is_judged_all_the_same() {
    let (bucket, sender) = sweep_plan::stored_bucket(&Stored::default(), &RuleSets::default());
    assert_eq!(sender, "");
    assert!(Bucket::ALL.contains(&bucket));
}

#[test]
fn a_rule_book_s_lists_are_read_lower_cased() {
    let sets =
        sweep_plan::sets_of(&json!({"block_senders": ["Lin@Example.test"], "keep_domains": ["CAFE.example.test"]}))
            .unwrap();
    assert!(sets.blocks_sender("lin@example.test"));
    assert!(sets.keeps_domain("cafe.example.test"));
    assert!(!sets.keeps_sender("lin@example.test"));
}

#[test]
fn a_document_that_isnt_rules_reads_as_none() {
    for document in
        [json!([]), json!("rules"), json!({"keep_senders": "a@example.test"}), json!({"block_domains": [1]})]
    {
        assert!(sweep_plan::sets_of(&document).is_none(), "{document}");
    }
    assert!(sweep_plan::sets_of(&json!({"expire_hours": {}, "filters": []})).is_some());
}

#[test]
fn the_buckets_are_listed_with_whether_each_is_junk() {
    let listed = sweep_plan::buckets();
    let junk: Vec<&str> = listed
        .as_array()
        .unwrap()
        .iter()
        .filter(|b| b["is_junk"] == json!(true))
        .map(|b| b["name"].as_str().unwrap())
        .collect();
    assert_eq!(listed.as_array().unwrap().len(), 7);
    assert_eq!(junk, ["spam/scam", "blocked", "newsletters/marketing", "automated-notifications"]);
}

#[derive(Debug)]
struct Inbox(Vec<Stored>);

impl StoredInbox for Inbox {
    fn stored_inboxes(&self, accounts: &[&str]) -> Result<Vec<Vec<Stored>>, Fault> {
        Ok(accounts.iter().map(|a| if *a == "ada@example.test" { self.0.clone() } else { Vec::new() }).collect())
    }
}

#[derive(Debug)]
struct Book(Option<Value>);

impl RuleBook for Book {
    fn rules(&self) -> Result<Option<Value>, Fault> {
        Ok(self.0.clone())
    }
}

fn plans(rules: Option<Value>) -> StoredPlans {
    StoredPlans::new(Arc::new(Inbox(vec![from("lin@example.test"), from("lin@example.test")])), Arc::new(Book(rules)))
}

const ADA: (&str, &str) = ("ada@example.test", "imap");

#[test]
fn each_plan_names_its_account_its_kind_and_no_error_in_the_order_asked() {
    let found = plans(None).plans_stored(&[("bea@example.test", "proton"), ADA]).unwrap();
    assert_eq!(found.len(), 2);
    let (bea, ada) = (&found[0], &found[1]);
    assert_eq!(ada.len(), 8);
    assert_eq!(
        (&ada["account"], &ada["kind"], &ada["error"]),
        (&json!("ada@example.test"), &json!("imap"), &Value::Null)
    );
    assert_eq!(ada["by_bucket"], json!({"other/personal-ish": 2}));
    assert_eq!(
        (&bea["account"], &bea["kind"], &bea["in_inbox"]),
        (&json!("bea@example.test"), &json!("proton"), &json!(0))
    );
    assert!(plans(None).plans_stored(&[]).unwrap().is_empty());
}

#[test]
fn a_plan_reads_the_rules_as_they_stand() {
    let found = plans(Some(json!({"block_senders": ["LIN@example.test"]}))).plans_stored(&[ADA]).unwrap();
    assert_eq!(found[0]["by_bucket"], json!({"blocked": 2}));
    assert_eq!((&found[0]["junk"], &found[0]["keep"]), (&json!(2), &json!(0)));
}

#[test]
fn a_rule_book_that_isnt_one_fails_the_plan() {
    let failed = plans(Some(json!({"keep_senders": 1}))).plans_stored(&[ADA]).unwrap_err();
    assert_eq!(failed.class, "MalformedRules");
}

//! The classifier, as the Python app ran it: every expected value here is what
//! `mailmind.curation.domain.classifier.classify` returned for the same arguments, before it called this crate. The
//! Python suite holds the two to the same answers over many more (tests/unit/curation/test_classifier_in_rust.py).

use std::sync::LazyLock;

use mm_curation::classifier::{Bucket, by_rule, classify, is_junk, looks_human};
use mm_curation::rules::RuleSets;

fn sets(keep_senders: &[&str], block_senders: &[&str], keep_domains: &[&str], block_domains: &[&str]) -> RuleSets {
    let owned = |list: &[&str]| list.iter().map(|entry| entry.to_string()).collect::<Vec<_>>();
    RuleSets::new(owned(keep_senders), owned(block_senders), owned(keep_domains), owned(block_domains))
}

static NONE: LazyLock<RuleSets> = LazyLock::new(RuleSets::default);

/// The Python unit suite's rule book.
static UNIT: LazyLock<RuleSets> = LazyLock::new(|| sets(&[], &[], &["work-example.test"], &["spam-platform.test"]));

/// The Python characterization suite's rule book, where every scope disagrees with another.
static PRECEDENCE: LazyLock<RuleSets> = LazyLock::new(|| {
    sets(
        &["both@rules.example.test"],
        &["both@rules.example.test", "noreply@kept.example.test"],
        &["kept.example.test", "dual.example.test"],
        &["dual.example.test", "blocked.example.test"],
    )
});

/// A keep rule for each address `a_sender_rule_names_the_address_python_lower_cased` sends, as Python's
/// `strip().lower()` wrote it.
static ADDRESSES: LazyLock<RuleSets> = LazyLock::new(|| {
    sets(
        &[
            "lin@studio.example.test",
            "lin@example.test",
            "ada@example.test",
            "\u{3b1}\u{3c2}@example.test",
            "\u{3c3}\u{3b1}@example.test",
            "\u{3c3}@example.test",
            "\u{3b1}\u{301}\u{3c2}@example.test",
            "\u{3b1}'\u{3c2}@example.test",
            "\u{3b1}\u{3c2}\u{a7ce}@example.test",
            "\u{3b1}\u{3c3}\u{345}\u{3b1}@example.test",
            "\u{a7ce}@example.test",
            "i\u{307}nfo@example.test",
            "ki\u{307}i@example.test",
            "\u{df}@example.test",
        ],
        &[],
        &[],
        &[],
    )
});

static EMPTY_DOMAIN: LazyLock<RuleSets> = LazyLock::new(|| sets(&[], &[], &[""], &[]));

fn said(from_addr: &str, subject: &str, from_name: &str, has_list_unsub: bool, rules: &RuleSets) -> [String; 3] {
    let verdict = classify(from_addr, subject, from_name, has_list_unsub, rules);
    [verdict.bucket.name().to_string(), verdict.confidence.name().to_string(), verdict.reason.into_owned()]
}

#[test]
fn the_unit_suites_cases() {
    assert_eq!(
        said("teamlead@work-example.test", "Re: the roadmap", "", false, &UNIT),
        ["personal/protected", "high", "kept by rule (domain)"]
    );
    assert_eq!(
        said("billing@work-example.test", "Invoice #42", "", false, &UNIT),
        ["personal/protected", "high", "kept by rule (domain)"]
    );
    assert_eq!(
        said("noreply@spam-platform.test", "anything at all", "", false, &UNIT),
        ["blocked", "high", "blocked by rule (domain)"]
    );
    assert_eq!(
        said("x@mail.spam-platform.test", "report", "", false, &UNIT),
        ["blocked", "high", "blocked by rule (domain)"]
    );
    assert_eq!(
        said("info@email.eventsco.example", "New events near you", "", false, &UNIT),
        ["newsletters/marketing", "med", "bulk sub-domain (email.)"]
    );
    assert_eq!(
        said("newsletters@somesite.org", "Daily reminder", "", false, &UNIT),
        ["newsletters/marketing", "med", "marketing role address (newsletters@)"]
    );
    assert_eq!(
        said("digest@mail.bigbrand.example", "This week", "", false, &UNIT),
        ["newsletters/marketing", "med", "bulk sub-domain (mail.)"]
    );
    assert_eq!(
        said("namaste@studio.example", "Class schedule", "", true, &UNIT),
        ["newsletters/marketing", "high", "List-Unsubscribe header"]
    );
    assert_eq!(
        said("promo@store.example", "50% off", "", false, &UNIT),
        ["newsletters/marketing", "med", "marketing role address (promo@)"]
    );
    assert_eq!(
        said("x@sendgrid.net", "anything", "", false, &UNIT),
        ["newsletters/marketing", "high", "bulk email-service-provider domain"]
    );
    assert_eq!(
        said("info@smallco.example", "New private message", "", false, &UNIT),
        ["other/personal-ish", "low", "unclassified \u{2014} review"]
    );
    assert_eq!(
        said("hello@startup.example", "Re: our call", "", false, &UNIT),
        ["other/personal-ish", "high", "reply/forward in an active thread"]
    );
    assert_eq!(
        said("scammer@gmail.com", "Turn $1.50 into a million!!!", "", false, &UNIT),
        ["spam/scam", "high", "fraud/MLM pattern"]
    );
    assert_eq!(
        said("fraudster@gmail.com", "CONTACT WESTERN UNION PAYMENT OFFICE", "", false, &UNIT),
        ["spam/scam", "high", "fraud/MLM pattern"]
    );
    assert_eq!(
        said("mlmguy@gmail.com", "\" Matrixforall \" Team Build", "", false, &UNIT),
        ["spam/scam", "high", "fraud/MLM pattern"]
    );
    assert_eq!(
        said("news@info.somecharity.org", "Crisis: 14 millions de personnes", "", false, &UNIT),
        ["newsletters/marketing", "med", "bulk sub-domain (info.)"]
    );
    assert_eq!(
        said("no-reply@send.somefintech.example", "You can now get paid globally", "", false, &UNIT),
        ["newsletters/marketing", "med", "bulk sub-domain (send.)"]
    );
    assert_eq!(
        said("news@e.foo.example", "Looking for a Specialist", "", false, &UNIT),
        ["newsletters/marketing", "med", "bulk sub-domain (e.)"]
    );
    assert_eq!(
        said("failed-payments@mail.acme.example", "your payment was unsuccessful", "", false, &UNIT),
        ["receipts/transactional", "med", "transactional/receipt signal"]
    );
    assert_eq!(
        said("invoice+statements@mail.acme.example", "Your receipt", "", false, &UNIT),
        ["receipts/transactional", "med", "transactional/receipt signal"]
    );
    assert_eq!(
        said("no-reply@impots.gouv.fr", "avis d'imposition", "", false, &UNIT),
        ["personal/protected", "high", "government/education/medical/financial domain"]
    );
    assert_eq!(
        said("prof@stanford.edu", "seminar invite", "", false, &UNIT),
        ["personal/protected", "high", "government/education/medical/financial domain"]
    );
    assert_eq!(
        said("portal@myclinic-health.example", "your appointment", "", false, &UNIT),
        ["personal/protected", "high", "government/education/medical/financial domain"]
    );
    assert_eq!(
        said("alice.smith@gmail.com", "lunch tomorrow?", "", false, &UNIT),
        ["other/personal-ish", "med", "person on a personal mailbox"]
    );
    assert_eq!(
        said("bob.jones@randomcorp.example", "Re: our meeting notes", "", false, &UNIT),
        ["other/personal-ish", "high", "reply/forward in an active thread"]
    );
    assert_eq!(
        said("no-reply@rideshare.example", "Your trip receipt is ready", "", false, &UNIT),
        ["receipts/transactional", "med", "transactional/receipt signal"]
    );
    assert_eq!(
        said("no-reply@widgets.example", "Your account activity", "", false, &UNIT),
        ["automated-notifications", "med", "no-reply / notification address"]
    );
    assert_eq!(
        said(
            "user@example.test",
            "\u{65b0}\u{5e74}\u{5feb}\u{4e50} \u{1f389} \u{437}\u{434}\u{440}\u{430}\u{432}\u{435}\u{439}",
            "",
            false,
            &UNIT
        ),
        ["other/personal-ish", "low", "unclassified \u{2014} review"]
    );
}

#[test]
fn rules_come_first_in_their_order() {
    assert_eq!(
        said("both@rules.example.test", "hi", "", false, &PRECEDENCE),
        ["personal/protected", "high", "kept by rule (sender)"]
    );
    assert_eq!(
        said("noreply@kept.example.test", "hi", "", false, &PRECEDENCE),
        ["blocked", "high", "blocked by rule (sender)"]
    );
    assert_eq!(
        said("x@dual.example.test", "hi", "", false, &PRECEDENCE),
        ["personal/protected", "high", "kept by rule (domain)"]
    );
    assert_eq!(
        said("x@sub.kept.example.test", "You have won the lottery", "", false, &PRECEDENCE),
        ["personal/protected", "high", "kept by rule (domain)"]
    );
    assert_eq!(
        said("winner@blocked.example.test", "hi", "", false, &PRECEDENCE),
        ["blocked", "high", "blocked by rule (domain)"]
    );
    assert_eq!(
        said("x@notkept.example.test", "hi", "", false, &PRECEDENCE),
        ["other/personal-ish", "low", "unclassified \u{2014} review"]
    );
    assert_eq!(
        said("records@example.gov", "You have won the lottery", "", false, &PRECEDENCE),
        ["spam/scam", "high", "fraud/MLM pattern"]
    );
    assert_eq!(
        said("ops@bank.example.test", "Your invoice", "", false, &PRECEDENCE),
        ["personal/protected", "high", "government/education/medical/financial domain"]
    );
    assert_eq!(
        said("news@bank.example.test", "Rates", "", false, &PRECEDENCE),
        ["newsletters/marketing", "med", "marketing role address (news@)"]
    );
    assert_eq!(
        said("billing@studio.example.test", "hi", "", true, &PRECEDENCE),
        ["receipts/transactional", "med", "transactional/receipt signal"]
    );
    assert_eq!(
        said("lin@studio.example.test", "Re: lunch", "", true, &PRECEDENCE),
        ["newsletters/marketing", "high", "List-Unsubscribe header"]
    );
    assert_eq!(
        said("lin@sendgrid.example.test", "Re: lunch", "", false, &PRECEDENCE),
        ["newsletters/marketing", "high", "bulk email-service-provider domain"]
    );
    assert_eq!(
        said("lin@e.studio.example.test", "Re: lunch", "", false, &PRECEDENCE),
        ["newsletters/marketing", "med", "bulk sub-domain (e.)"]
    );
    assert_eq!(
        said("marketing@studio.example.test", "Re: your quote", "", false, &PRECEDENCE),
        ["other/personal-ish", "high", "reply/forward in an active thread"]
    );
    assert_eq!(
        said("no-reply@studio.example.test", "Re: your quote", "", false, &PRECEDENCE),
        ["automated-notifications", "med", "no-reply / notification address"]
    );
    assert_eq!(
        said("lin@studio.example.test", "AW: Termin", "", false, &PRECEDENCE),
        ["other/personal-ish", "high", "reply/forward in an active thread"]
    );
    assert_eq!(
        said("lin@studio.example.test", "R\u{e9}f: devis", "", false, &PRECEDENCE),
        ["other/personal-ish", "high", "reply/forward in an active thread"]
    );
    assert_eq!(
        said("marketing@studio.example.test", "Spring range", "", false, &PRECEDENCE),
        ["newsletters/marketing", "med", "marketing role address (marketing@)"]
    );
    assert_eq!(
        said("news@gmail.com", "hello", "", false, &PRECEDENCE),
        ["other/personal-ish", "low", "unclassified \u{2014} review"]
    );
    assert_eq!(
        said("alerts@studio.example.test", "Login", "", false, &PRECEDENCE),
        ["automated-notifications", "med", "no-reply / notification address"]
    );
    assert_eq!(
        said("lin.example@gmail.com", "hi", "", false, &PRECEDENCE),
        ["other/personal-ish", "med", "person on a personal mailbox"]
    );
    assert_eq!(
        said("make.example@gmail.com", "make money now", "", false, &PRECEDENCE),
        ["spam/scam", "high", "fraud/MLM pattern"]
    );
    assert_eq!(
        said("deals@example.tk", "make money now", "", false, &PRECEDENCE),
        ["spam/scam", "high", "fraud/MLM pattern"]
    );
    assert_eq!(
        said("lin@studio.example.test", "make money now", "", false, &PRECEDENCE),
        ["other/personal-ish", "low", "unclassified \u{2014} review"]
    );
    assert_eq!(
        said("lin@msg.example.test", "Lunch on Thursday?", "", false, &PRECEDENCE),
        ["other/personal-ish", "low", "unclassified \u{2014} review"]
    );
    assert_eq!(
        said("lin@studio.sg", "Lunch on Thursday?", "", false, &PRECEDENCE),
        ["other/personal-ish", "low", "unclassified \u{2014} review"]
    );
    assert_eq!(
        said("deals@sg.example.test", "Lunch on Thursday?", "", false, &PRECEDENCE),
        ["newsletters/marketing", "high", "bulk email-service-provider domain"]
    );
}

#[test]
fn an_address_is_read_as_python_lower_cased_and_stripped_it() {
    assert_eq!(
        said("  Lin@Studio.Example.Test\u{9}", "hi", "", false, &NONE),
        ["other/personal-ish", "low", "unclassified \u{2014} review"]
    );
    assert_eq!(
        said("\u{1c}lin@example.test\u{1f}", "hi", "", false, &NONE),
        ["other/personal-ish", "low", "unclassified \u{2014} review"]
    );
    assert_eq!(said("nobody", "hi", "", false, &NONE), ["other/personal-ish", "low", "unclassified \u{2014} review"]);
    assert_eq!(
        said("a@b@c.example.test", "hi", "", false, &NONE),
        ["other/personal-ish", "low", "unclassified \u{2014} review"]
    );
    assert_eq!(said("", "", "", false, &NONE), ["other/personal-ish", "low", "unclassified \u{2014} review"]);
}

#[test]
fn a_sender_rule_names_the_address_python_lower_cased() {
    assert_eq!(
        said("  Lin@Studio.Example.Test\u{9}", "hi", "", false, &ADDRESSES),
        ["personal/protected", "high", "kept by rule (sender)"]
    );
    assert_eq!(
        said("\u{1c}lin@example.test\u{1f}", "hi", "", false, &ADDRESSES),
        ["personal/protected", "high", "kept by rule (sender)"]
    );
    assert_eq!(
        said("\u{3000}Ada@Example.Test\u{85}", "hi", "", false, &ADDRESSES),
        ["personal/protected", "high", "kept by rule (sender)"]
    );
    // A capital sigma that ends a word lowers to a final sigma, where Python says a word ends.
    assert_eq!(
        said("\u{391}\u{3a3}@example.test", "hi", "", false, &ADDRESSES),
        ["personal/protected", "high", "kept by rule (sender)"]
    );
    assert_eq!(
        said("\u{3a3}\u{391}@example.test", "hi", "", false, &ADDRESSES),
        ["personal/protected", "high", "kept by rule (sender)"]
    );
    assert_eq!(
        said("\u{3a3}@example.test", "hi", "", false, &ADDRESSES),
        ["personal/protected", "high", "kept by rule (sender)"]
    );
    assert_eq!(
        said("\u{391}\u{301}\u{3a3}@example.test", "hi", "", false, &ADDRESSES),
        ["personal/protected", "high", "kept by rule (sender)"]
    );
    assert_eq!(
        said("\u{391}'\u{3a3}@example.test", "hi", "", false, &ADDRESSES),
        ["personal/protected", "high", "kept by rule (sender)"]
    );
    // U+A7CE came after Unicode 16, so Python neither lowers it nor counts it as a letter.
    assert_eq!(
        said("\u{391}\u{3a3}\u{a7ce}@example.test", "hi", "", false, &ADDRESSES),
        ["personal/protected", "high", "kept by rule (sender)"]
    );
    assert_eq!(
        said("\u{391}\u{3a3}\u{345}\u{391}@example.test", "hi", "", false, &ADDRESSES),
        ["personal/protected", "high", "kept by rule (sender)"]
    );
    assert_eq!(
        said("\u{a7ce}@example.test", "hi", "", false, &ADDRESSES),
        ["personal/protected", "high", "kept by rule (sender)"]
    );
    assert_eq!(
        said("\u{130}nfo@example.test", "hi", "", false, &ADDRESSES),
        ["personal/protected", "high", "kept by rule (sender)"]
    );
    assert_eq!(
        said("\u{212a}\u{130}I@example.test", "hi", "", false, &ADDRESSES),
        ["personal/protected", "high", "kept by rule (sender)"]
    );
    assert_eq!(
        said("\u{1e9e}@example.test", "hi", "", false, &ADDRESSES),
        ["personal/protected", "high", "kept by rule (sender)"]
    );
}

#[test]
fn an_empty_domain_rule_covers_what_has_no_domain() {
    // An address without a domain has the empty one.
    assert_eq!(said("nobody", "hi", "", false, &EMPTY_DOMAIN), ["personal/protected", "high", "kept by rule (domain)"]);
    assert_eq!(
        said("lin@example.test.", "hi", "", false, &EMPTY_DOMAIN),
        ["personal/protected", "high", "kept by rule (domain)"]
    );
    assert_eq!(
        said("lin@example.test", "hi", "", false, &EMPTY_DOMAIN),
        ["other/personal-ish", "low", "unclassified \u{2014} review"]
    );
}

#[test]
fn word_boundaries_are_pythons() {
    // A mark isn't a word character to Python, so a word ends before one.
    assert_eq!(
        said("lin@example.test", "Your refund\u{301}", "", false, &NONE),
        ["receipts/transactional", "med", "transactional/receipt signal"]
    );
    assert_eq!(
        said("lin@example.test", "\u{301}refund", "", false, &NONE),
        ["receipts/transactional", "med", "transactional/receipt signal"]
    );
    // Nor is connector punctuation, but `_` is.
    assert_eq!(
        said("lin@example.test", "refund\u{203f}", "", false, &NONE),
        ["receipts/transactional", "med", "transactional/receipt signal"]
    );
    assert_eq!(
        said("lin@example.test", "refund_", "", false, &NONE),
        ["other/personal-ish", "low", "unclassified \u{2014} review"]
    );
    // A superscript digit and a Roman numeral are.
    assert_eq!(
        said("lin@example.test", "\u{b2}refund", "", false, &NONE),
        ["other/personal-ish", "low", "unclassified \u{2014} review"]
    );
    assert_eq!(
        said("lin@example.test", "refund\u{2160}", "", false, &NONE),
        ["other/personal-ish", "low", "unclassified \u{2014} review"]
    );
    assert_eq!(
        said("lin@example.test", "refunds", "", false, &NONE),
        ["other/personal-ish", "low", "unclassified \u{2014} review"]
    );
    assert_eq!(
        said("lin@example.test", "Order #123", "", false, &NONE),
        ["receipts/transactional", "med", "transactional/receipt signal"]
    );
    // `order #` and `order no.` want a word after them.
    assert_eq!(
        said("lin@example.test", "Order # 123", "", false, &NONE),
        ["other/personal-ish", "low", "unclassified \u{2014} review"]
    );
    assert_eq!(
        said("lin@example.test", "order no. 5", "", false, &NONE),
        ["receipts/transactional", "med", "transactional/receipt signal"]
    );
    assert_eq!(
        said("lin@example.test", "order no.5", "", false, &NONE),
        ["receipts/transactional", "med", "transactional/receipt signal"]
    );
    assert_eq!(
        said("lin@example.test", "order nob", "", false, &NONE),
        ["other/personal-ish", "low", "unclassified \u{2014} review"]
    );
    assert_eq!(
        said("lin@example.test", "penis enlargement pills", "", false, &NONE),
        ["spam/scam", "high", "fraud/MLM pattern"]
    );
    // No cialis inside a word.
    assert_eq!(
        said("lin@example.test", "specialist", "", false, &NONE),
        ["other/personal-ish", "low", "unclassified \u{2014} review"]
    );
    assert_eq!(said("lin@example.test", "cialis\u{301}", "", false, &NONE), ["spam/scam", "high", "fraud/MLM pattern"]);
    assert_eq!(said("lin@gmail.com", "100 euros now!!!", "", false, &NONE), ["spam/scam", "high", "fraud/MLM pattern"]);
    assert_eq!(
        said("lin@gmail.com", "100 eurosx now!!!", "", false, &NONE),
        ["other/personal-ish", "med", "person on a personal mailbox"]
    );
    assert_eq!(
        said("lin@gmail.com", "PROFIT\u{b2} TODAY", "", false, &NONE),
        ["other/personal-ish", "med", "person on a personal mailbox"]
    );
    assert_eq!(
        said("lin@gmail.com", "PROFIT\u{301} TODAY", "", false, &NONE),
        ["spam/scam", "high", "fraud/MLM pattern"]
    );
}

#[test]
fn case_is_ignored_as_python_ignored_it() {
    // Python matches `i` with a dotted capital I and a dotless small i, `s` with a long s, and `k` with the Kelvin sign.
    assert_eq!(
        said("lin@example.test", "RECE\u{130}PT", "", false, &NONE),
        ["receipts/transactional", "med", "transactional/receipt signal"]
    );
    assert_eq!(
        said("lin@example.test", "rece\u{131}pt", "", false, &NONE),
        ["receipts/transactional", "med", "transactional/receipt signal"]
    );
    assert_eq!(
        said("lin@example.test", "has \u{17f}hipped", "", false, &NONE),
        ["receipts/transactional", "med", "transactional/receipt signal"]
    );
    assert_eq!(
        said("lin@example.test", "next of \u{212a}in", "", false, &NONE),
        ["spam/scam", "high", "fraud/MLM pattern"]
    );
    assert_eq!(
        said("lin@example.test", "R\u{c9}F: devis", "", false, &NONE),
        ["other/personal-ish", "high", "reply/forward in an active thread"]
    );
    assert_eq!(said("lin@example.test", "INHERITANCE", "", false, &NONE), ["spam/scam", "high", "fraud/MLM pattern"]);
    assert_eq!(
        said("x@tax.gov.\u{131}t", "hi", "", false, &NONE),
        ["personal/protected", "high", "government/education/medical/financial domain"]
    );
    // Lower-cased, a dotted capital I is `i` and a combining dot, so this domain isn't info's.
    assert_eq!(
        said("x@\u{130}nfo.example.test", "hi", "", false, &NONE),
        ["other/personal-ish", "low", "unclassified \u{2014} review"]
    );
    assert_eq!(
        said("lin@example.test", "Not\u{131}fy", "", false, &NONE),
        ["other/personal-ish", "low", "unclassified \u{2014} review"]
    );
    assert_eq!(
        said("not\u{131}fy@example.test", "hi", "", false, &NONE),
        ["automated-notifications", "med", "no-reply / notification address"]
    );
}

#[test]
fn whitespace_is_what_python_called_whitespace() {
    assert_eq!(said("lin@gmail.com", "$5\u{1c}a day", "", false, &NONE), ["spam/scam", "high", "fraud/MLM pattern"]);
    assert_eq!(
        said("lin@gmail.com", "$5\u{3000}a\u{2003}day", "", false, &NONE),
        ["spam/scam", "high", "fraud/MLM pattern"]
    );
    // A zero-width space isn't whitespace.
    assert_eq!(
        said("lin@gmail.com", "$5\u{200b}a day", "", false, &NONE),
        ["other/personal-ish", "med", "person on a personal mailbox"]
    );
    assert_eq!(
        said("lin@example.test", "\u{1f}Re: lunch", "", false, &NONE),
        ["other/personal-ish", "high", "reply/forward in an active thread"]
    );
    assert_eq!(
        said("lin@example.test", "\u{85}RE\u{a0}: lunch", "", false, &NONE),
        ["other/personal-ish", "high", "reply/forward in an active thread"]
    );
    // An Arabic-Indic digit is a digit.
    assert_eq!(
        said("lin@gmail.com", "earn $\u{663} now", "", false, &NONE),
        ["spam/scam", "high", "fraud/MLM pattern"]
    );
    assert_eq!(said("lin@gmail.com", "50 % a day", "", false, &NONE), ["spam/scam", "high", "fraud/MLM pattern"]);
    // `.` stops at a line feed, and nowhere else.
    assert_eq!(
        said("lin@gmail.com", "risk-free\u{a}returns", "", false, &NONE),
        ["other/personal-ish", "med", "person on a personal mailbox"]
    );
    assert_eq!(
        said("lin@gmail.com", "risk-free\u{d}returns", "", false, &NONE),
        ["spam/scam", "high", "fraud/MLM pattern"]
    );
}

#[test]
fn the_subject_and_the_name_are_read_together() {
    assert_eq!(
        said("lin@example.test", "you have", "won it", false, &NONE),
        ["spam/scam", "high", "fraud/MLM pattern"]
    );
    assert_eq!(said("lin@gmail.com", "make", "money", false, &NONE), ["spam/scam", "high", "fraud/MLM pattern"]);
    assert_eq!(said("lin@gmail.com", "hi", "Make Money", false, &NONE), ["spam/scam", "high", "fraud/MLM pattern"]);
}

#[test]
fn a_person_on_free_mail() {
    assert_eq!(
        said("lin2@gmail.com", "hi", "", false, &NONE),
        ["other/personal-ish", "med", "person on a personal mailbox"]
    );
    assert_eq!(
        said("li22@gmail.com", "hi", "", false, &NONE),
        ["other/personal-ish", "med", "person on a personal mailbox"]
    );
    // Python counts a superscript digit and a circled one as digits.
    assert_eq!(
        said("l\u{b2}\u{b2}@gmail.com", "hi", "", false, &NONE),
        ["other/personal-ish", "low", "unclassified \u{2014} review"]
    );
    assert_eq!(
        said("ab\u{2460}\u{2460}\u{2460}@gmail.com", "hi", "", false, &NONE),
        ["other/personal-ish", "low", "unclassified \u{2014} review"]
    );
    assert_eq!(
        said("ab\u{663}\u{663}\u{663}@gmail.com", "hi", "", false, &NONE),
        ["other/personal-ish", "low", "unclassified \u{2014} review"]
    );
    assert_eq!(
        said("0123456789ab@gmail.com", "hi", "", false, &NONE),
        ["other/personal-ish", "low", "unclassified \u{2014} review"]
    );
    assert_eq!(
        said("0123456789a@gmail.com", "hi", "", false, &NONE),
        ["other/personal-ish", "low", "unclassified \u{2014} review"]
    );
    assert_eq!(
        said("abcdefghijklmnopqr@gmail.com", "hi", "", false, &NONE),
        ["other/personal-ish", "low", "unclassified \u{2014} review"]
    );
    assert_eq!(
        said("abcdefghijklmnopq@gmail.com", "hi", "", false, &NONE),
        ["other/personal-ish", "med", "person on a personal mailbox"]
    );
    assert_eq!(
        said("\u{e9}l\u{e8}ve@gmail.com", "hi", "", false, &NONE),
        ["other/personal-ish", "med", "person on a personal mailbox"]
    );
    assert_eq!(
        said("\u{e9}\u{e8}@gmail.com", "hi", "", false, &NONE),
        ["other/personal-ish", "low", "unclassified \u{2014} review"]
    );
    assert_eq!(
        said("noreplyjo@gmail.com", "hi", "", false, &NONE),
        ["other/personal-ish", "low", "unclassified \u{2014} review"]
    );
    assert_eq!(
        said("jo.newsom@gmail.com", "hi", "", false, &NONE),
        ["other/personal-ish", "low", "unclassified \u{2014} review"]
    );
}

#[test]
fn a_local_part_looks_human_as_python_judged_it() {
    for (local, human) in [
        (None, false),
        (Some(""), false),
        (Some("lin"), true),
        (Some("lin2"), true),
        (Some("li22"), true),
        (Some("l\u{b2}\u{b2}"), false),
        (Some("ab\u{2460}\u{2460}\u{2460}"), false),
        (Some("info"), false),
        (Some("noreply.lin"), false),
        (Some("lin.newsom"), false),
        (Some("0123456789ab"), false),
        (Some("abcdefghijklmnopqr"), false),
        (Some("LIN"), false),
        (Some("\u{e9}l\u{e8}ve"), true),
        (Some("\u{e9}\u{e8}"), false),
        (Some("Lin"), true),
    ] {
        assert_eq!(looks_human(local), human, "{local:?}");
    }
}

#[test]
fn junk_is_spam_blocked_marketing_and_notifications() {
    let junk: Vec<_> = Bucket::ALL.into_iter().filter(|b| is_junk(Some(b.name()))).map(Bucket::name).collect();
    assert_eq!(junk, ["spam/scam", "blocked", "newsletters/marketing", "automated-notifications"]);
    assert!(!is_junk(None) && !is_junk(Some("")) && !is_junk(Some("Spam/Scam")));
}

#[test]
fn a_reason_says_when_a_rule_decided() {
    assert!(by_rule(Some("kept by rule (sender)")) && by_rule(Some("blocked by rule (domain)")));
    assert!(!by_rule(Some("List-Unsubscribe header")) && !by_rule(Some("")) && !by_rule(None));
}

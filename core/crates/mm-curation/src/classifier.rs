//! The classifier: a message's sender, subject and List headers read into a bucket, with how sure it is and why.
//!
//! What hand-sweeping six inboxes taught it:
//! - List-Unsubscribe alone isn't enough. Most bulk mail that leaked into the inbox left it out, so role local parts
//!   (info@, news@, hello@), bulk-sending infrastructure (mailchimp, sendgrid, beehiiv) and bulk sub-domains (e.,
//!   mail., news.) count too.
//! - Outright fraud (lotteries, MLM, "get paid", crypto doubling) is caught even from a free mailbox, so it never
//!   passes for a person.
//! - Keeping is conservative: government, education, medical and financial senders, and real people, are never junk
//!   unless they're fraud.
//!
//! The Python app wrote these rules against Python's `str` and `re`, and the buckets they gave are stored, so each
//! comparison here answers as Python did. `crate::text` says how.

use std::borrow::Cow;
use std::sync::LazyLock;

use regex::Regex;

use crate::rules::RuleSets;
use crate::text::{self, pattern, space, word, word_ends, word_starts};

/// Where a message goes. The names are what the database stores.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Bucket {
    /// Fraud, MLM and the like, whoever sent it.
    Spam,
    /// The person said always to trash this sender or domain.
    Blocked,
    /// Newsletters and marketing.
    Marketing,
    /// Mail a machine sends: no-reply, alerts.
    Notifications,
    /// Receipts and other financial records: kept.
    Receipts,
    /// Government, education, medical and financial senders, and whoever a keep rule names: kept.
    Protected,
    /// Everything else, kept: the one bucket that may still want a person's glance.
    Personal,
}

impl Bucket {
    /// Every bucket.
    pub const ALL: [Bucket; 7] = [
        Bucket::Spam,
        Bucket::Blocked,
        Bucket::Marketing,
        Bucket::Notifications,
        Bucket::Receipts,
        Bucket::Protected,
        Bucket::Personal,
    ];

    /// The bucket's name, as the database stores it.
    pub const fn name(self) -> &'static str {
        match self {
            Bucket::Spam => "spam/scam",
            Bucket::Blocked => "blocked",
            Bucket::Marketing => "newsletters/marketing",
            Bucket::Notifications => "automated-notifications",
            Bucket::Receipts => "receipts/transactional",
            Bucket::Protected => "personal/protected",
            Bucket::Personal => "other/personal-ish",
        }
    }

    /// The bucket a name names.
    pub fn named(name: &str) -> Option<Bucket> {
        Bucket::ALL.into_iter().find(|bucket| bucket.name() == name)
    }

    /// Whether mail in this bucket is junk, which ingest marks and a sweep takes.
    pub const fn is_junk(self) -> bool {
        matches!(self, Bucket::Spam | Bucket::Blocked | Bucket::Marketing | Bucket::Notifications)
    }
}

/// How sure the classifier is.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Confidence {
    High,
    Med,
    Low,
}

impl Confidence {
    /// The name the database stores.
    pub const fn name(self) -> &'static str {
        match self {
            Confidence::High => "high",
            Confidence::Med => "med",
            Confidence::Low => "low",
        }
    }
}

/// What the classifier makes of a message: its bucket, how sure, and a short reason a person can read.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Verdict {
    pub bucket: Bucket,
    pub confidence: Confidence,
    pub reason: Cow<'static, str>,
}

fn verdict(bucket: Bucket, confidence: Confidence, reason: impl Into<Cow<'static, str>>) -> Verdict {
    Verdict { bucket, confidence, reason: reason.into() }
}

// -- what a sender says ---------------------------------------------------------------------------------------------

const FREE_MAIL: &[&str] = &[
    "gmail.com",
    "googlemail.com",
    "hotmail.com",
    "hotmail.co.uk",
    "outlook.com",
    "live.com",
    "live.fr",
    "msn.com",
    "yahoo.com",
    "yahoo.co.uk",
    "yahoo.fr",
    "ymail.com",
    "icloud.com",
    "me.com",
    "mac.com",
    "aol.com",
    "gmx.com",
    "gmx.de",
    "gmx.net",
    "web.de",
    "proton.me",
    "protonmail.com",
    "pm.me",
    "hey.com",
    "fastmail.com",
    "zoho.com",
    "mail.com",
    "libero.it",
    "orange.fr",
    "free.fr",
    "qq.com",
    "163.com",
    "naver.com",
];

/// Local parts that are marketing on their own.
const ROLE_MKT: &[&str] = &[
    "news",
    "newsletter",
    "newsletters",
    "marketing",
    "promo",
    "promotions",
    "promotion",
    "offers",
    "deals",
    "digest",
    "campaigns",
    "campaign",
    "buzz",
    "magazine",
    "mailings",
    "mailing",
    "enews",
    "discover",
    "inspiration",
];

/// Small-business roles: often bulk, but also real small teams, so they count only beside another bulk signal.
const ROLE_MKT_WEAK: &[&str] = &[
    "info",
    "hello",
    "hi",
    "hey",
    "contact",
    "team",
    "social",
    "community",
    "members",
    "engage",
    "updates",
    "stories",
    "weekly",
];

/// Local parts only a machine sends from.
const ROLE_NOTIF: &[&str] = &[
    "no-reply",
    "noreply",
    "no_reply",
    "donotreply",
    "do-not-reply",
    "do_not_reply",
    "notification",
    "notifications",
    "notify",
    "alerts",
    "alert",
    "automated",
    "mailer-daemon",
    "mailer",
    "bounce",
    "postmaster",
    "mailbot",
    "auto-confirm",
    "autoconfirm",
];

const ROLE_TXN: &[&str] = &[
    "receipt",
    "receipts",
    "invoice",
    "invoices",
    "billing",
    "invoicing",
    "payment",
    "payments",
    "orders",
    "order",
    "statements",
    "accounting",
];

/// What a billing-ish local part holds, as failed-payments@ and invoice+statements@ do.
const TXN_WORDS: &[&str] = &["receipt", "invoice", "billing", "payment", "statement"];

/// Bulk-sending infrastructure, found anywhere in the domain.
const ESP_MARKERS: &[&str] = &[
    "sendgrid",
    "sparkpost",
    "mailgun",
    "mcsv.net",
    "mcdlv.net",
    "rsgsv.net",
    "list-manage",
    "mailchimp",
    "beehiiv",
    "substack",
    "ghost.io",
    "hubspot",
    "klaviyo",
    "sendinblue",
    "brevo",
    "amazonses",
    "sail thru",
    "sailthru",
    "iterable",
    "customer.io",
    "customeriomail",
    "braze",
    "mandrillapp",
    "cmail19",
    "cmail20",
    "createsend",
    "exct.net",
    "responsys",
    "marketo",
    "pardot",
    "salesforce",
    "constantcontact",
    "ccsend",
    "icontact",
    "mailjet",
    "postmarkapp",
    "sparkpostmail",
    "everestengagement",
    "convertkit",
    "ck.page",
    "drip.com",
    "activecampaign",
    "getresponse",
    "aweber",
    "emarsys",
    "bluecore",
    "cordial",
    "movableink",
    "narvar",
    "shopifyemail",
];

/// Markers too short to find inside a word: each counts only as a whole label, and never the last one (.sg is
/// Singapore's top-level domain).
const ESP_LABELS: &[&str] = &["sg"];

/// The first label of a domain that sends in bulk.
const BULK_SUBDOMAINS: &[&str] = &[
    "e",
    "em",
    "email",
    "emails",
    "mail",
    "mailing",
    "mailings",
    "news",
    "newsletter",
    "newsletters",
    "t",
    "m",
    "send",
    "sender",
    "go",
    "link",
    "links",
    "updates",
    "mktg",
    "marketing",
    "click",
    "clicks",
    "et",
    "trx",
    "message",
    "messages",
    "notif",
    "notification",
    "notifications",
    "ml",
    "campaign",
    "campaigns",
    "info",
    "hello",
    "reply",
    "comms",
    "communication",
    "communications",
    "list",
    "lists",
    "engage",
    "p",
    "e1",
];

const SUSPICIOUS_TLDS: &[&str] = &[
    "tk", "ml", "ga", "cf", "gq", "work", "click", "link", "loan", "kim", "country", "stream", "download", "racing",
    "win", "bid", "men",
];

/// Prefixes only a machine's local part starts with.
const NO_REPLY_PREFIXES: &[&str] = &["no-reply", "noreply", "no_reply", "donotreply"];

/// What a person's local part never holds.
const MACHINE_WORDS: &[&str] = &["news", "newsletter", "notif", "mailer", "mktg", "marketing", "campaign", "bounce"];

// -- what a subject says --------------------------------------------------------------------------------------------
//
// Each pattern is the Python app's, written for the `regex` crate: its `\b` as `word_starts!` or `word_ends!`, its
// `\s` as `space!`, its `$` as `\n?\z`, which is where Python's matches. Each that ignores case reads its haystack
// through `text::i_folded`.

/// Receipts, bookings and orders.
static TRANSACTIONAL: LazyLock<Regex> = LazyLock::new(|| {
    pattern(concat!(
        r"(?i)",
        word_starts!(),
        r"(?:receipt|invoice|payment received|your payment|you paid|amount paid|your bill|e-?statement|statement is|",
        r"e-?ticket|boarding pass|itinerary|booking (?:confirm|reference|is)|reservation confirm|",
        r"order (?:confirm|no|placed|shipped|dispatched)|has shipped|out for delivery|refund|tax invoice|vat|charged|",
        r"transaction (?:receipt|confirm))",
        word_ends!(),
        // `order no.` ends on a character that isn't a word's, so Python's `\b` after it wants one. `order #` is an
        // order's number whatever follows the sign, as in `Order # 123`.
        r"|",
        word_starts!(),
        r"order (?:#|no\.",
        word!(),
        r")",
    ))
});

/// Unambiguous fraud, whoever sent it.
static STRONG_SCAM: LazyLock<Regex> = LazyLock::new(|| {
    pattern(concat!(
        r"(?i)you(?:'| ?ha)ve won|you are a winner|claim your (?:prize|reward|winnings|funds)|western union|",
        r"money ?gram|wire transfer to|inheritance|next of kin|beneficiar|lottery|jackpot|unclaimed (?:funds|money)|",
        r"nigerian prince|double your (?:money|btc|bitcoin|investment)|",
        word_starts!(),
        r"(?:(?:viagra|cialis)",
        word_ends!(),
        // Python's `enlarge\w*\b` always finds where the word ends.
        r"|penis enlarge)",
    ))
});

/// Money-making, MLM, HYIP and urgency: fraud only from a sketchy sender (free mail or a junk TLD), since real
/// brands use the words too.
static WEAK_SCAM: LazyLock<Regex> = LazyLock::new(|| {
    pattern(concat!(
        r"(?i)",
        // Each phrase from where a word starts, so a budget paid, a word to learn 5 of, or a nonprofit system is no
        // fraud. A cycler too, so a recycler isn't.
        word_starts!(),
        r"(?:get paid|getpaid|make money|earn money|earn \$?\d|make \$?\d|",
        r"work from home|passive income|be your own boss|financial freedom|",
        r"revenue share|traffic exchange|paid to click|downline|cycler|",
        r"cash ?club|instant ?cash|join asap|going viral|pre-?launch|profit system|",
        r"risk[- ]free.{0,20}(?:invest|profit|return)|",
        // A transfer or a fund where its word starts, so no refund. The character before it is one `.` takes, so no
        // line feed.
        r"urgent.{0,14}(?-i:[^\p{L}\p{N}_\n])(?:transfer|fund)|",
        r"(?:ptc|m2m)",
        word_ends!(),
        r")|",
        // A day, a week or a month where its word ends, so no weekend.
        r"\$\d[\d,]*",
        space!(),
        r"*(?:a|per|/)",
        space!(),
        r"*(?:day|week|month)",
        word_ends!(),
        r"|[0-9]{2,}",
        space!(),
        r"*% (?:per|a) (?:day|month|return)|",
        // A matrix is an everyday word, so it counts only glued into a scheme's name, as in Matrixforall.
        word!(),
        r"matrix|matrix",
        word!(),
        r"|100% (?:guarantee|profit)",
    ))
});

static SHOUTY: LazyLock<Regex> = LazyLock::new(|| pattern(r"[A-Z]{6}"));

static MONEY: LazyLock<Regex> = LazyLock::new(|| {
    pattern(concat!(
        r"(?i)\$",
        space!(),
        r"?\d|\d",
        space!(),
        r"?\$|usd|bitcoin|€|million|",
        word_starts!(),
        r"(?:btc|euros?|cash|profit)",
        word_ends!(),
    ))
});

// -- protected senders ----------------------------------------------------------------------------------------------
//
// Broad on purpose: a person protects their own niche institutions with a keep rule.

static GOV: LazyLock<Regex> = LazyLock::new(|| {
    pattern(concat!(
        r"(?i)\.gov(?:\.[a-z]{2})?\n?\z|\.(?:gouv|go|gob|admin)\.[a-z]{2}\n?\z|\.gc\.ca\n?\z|\.govt\.|",
        word_starts!(),
        r"gov\.",
    ))
});

static EDU: LazyLock<Regex> =
    LazyLock::new(|| pattern(r"(?i)\.edu(?:\.[a-z]{2})?\n?\z|\.ac\.[a-z]{2}\n?\z|\.uni-[a-z]+\."));

static HEALTH: LazyLock<Regex> = LazyLock::new(|| {
    pattern(concat!(
        // A hospital, but no hotel's hospitality.
        r"(?i)sante|health|clinic|hospital(?:\z|[^i]|i(?:\z|[^t]|t(?:\z|[^y])))|patient|doctolib|dentist|",
        word_starts!(),
        // `medi` but never `media` or `medium`, which a publisher's or a media player's domain holds.
        r"(?:santé|medi(?:u?\z|u[^m]|[^au]))",
    ))
});

static FINANCE: LazyLock<Regex> = LazyLock::new(|| {
    pattern(concat!(
        // wise.com and stripe.com as whole labels, and their sub-domains: likewise.com is someone else's. Revolut
        // where its word ends, so no revolution.
        r"(?i)paypal|(?:\A|\.)wise\.com(?:\.|\n?\z)|revolut",
        word_ends!(),
        r"|(?:\A|\.)stripe\.com\n?\z|mastercard|",
        word_starts!(),
        r"(?:bank|banque|n26|visa)",
        word_ends!(),
    ))
});

static NOTIF: LazyLock<Regex> =
    LazyLock::new(|| pattern(r"(?i)no-?reply|do-?not-?reply|notification|notify|mailer-daemon|automated|noreply"));

/// Reply and forward prefixes, in several languages, at the start of a subject.
static RE_FWD: LazyLock<Regex> =
    LazyLock::new(|| pattern(concat!(r"(?i)\A", space!(), r"*(?:re|fwd|fw|aw|wg|tr|rv|r[ée]f)", space!(), r"*:")));

// -- the classifier -------------------------------------------------------------------------------------------------

/// What the classifier makes of a message from `from_addr`, as its From header named it, with this subject and
/// display name, and whether it carried a List-Unsubscribe or List-ID header. The person's rules come first.
pub fn classify(from_addr: &str, subject: &str, from_name: &str, has_list_unsub: bool, rules: &RuleSets) -> Verdict {
    let full = text::lower(text::strip(from_addr));
    let (local, domain) = full.split_once('@').unwrap_or(("", ""));

    // The person's own verdicts win over every heuristic. A sender's rule beats a domain's, so a blocked noreply@
    // overrides a kept domain, and within each keep beats block.
    if rules.keeps_sender(&full) {
        return verdict(Bucket::Protected, Confidence::High, "kept by rule (sender)");
    }
    if rules.blocks_sender(&full) {
        return verdict(Bucket::Blocked, Confidence::High, "blocked by rule (sender)");
    }
    if rules.keeps_domain(domain) {
        return verdict(Bucket::Protected, Confidence::High, "kept by rule (domain)");
    }
    if rules.blocks_domain(domain) {
        return verdict(Bucket::Blocked, Confidence::High, "blocked by rule (domain)");
    }

    // Fraud wins over the rest, a free mailbox's included.
    let subject_i = text::i_folded(subject);
    let domain_i = text::i_folded(domain);
    if is_scam(domain, subject, &subject_i, from_name) {
        return verdict(Bucket::Spam, Confidence::High, "fraud/MLM pattern");
    }
    if is_protected_domain(&domain_i, local) {
        return verdict(Bucket::Protected, Confidence::High, "government/education/medical/financial domain");
    }
    if TRANSACTIONAL.is_match(&subject_i) || ROLE_TXN.contains(&local) || TXN_WORDS.iter().any(|w| local.contains(w)) {
        return verdict(Bucket::Receipts, Confidence::Med, "transactional/receipt signal");
    }

    // Newsletters and marketing, the leak this closes.
    if has_list_unsub {
        return verdict(Bucket::Marketing, Confidence::High, "List-Unsubscribe header");
    }
    if ESP_MARKERS.iter().any(|m| domain.contains(m)) || domain.rsplit('.').skip(1).any(|l| ESP_LABELS.contains(&l)) {
        return verdict(Bucket::Marketing, Confidence::High, "bulk email-service-provider domain");
    }
    let first_label = domain.split('.').next().unwrap_or("");
    if domain.matches('.').count() >= 2 && BULK_SUBDOMAINS.contains(&first_label) {
        return verdict(Bucket::Marketing, Confidence::Med, format!("bulk sub-domain ({first_label}.)"));
    }

    // A reply or forward that got past the bulk checks is a real thread: a role address doesn't make it less so.
    if RE_FWD.is_match(&subject_i) && !ROLE_NOTIF.contains(&local) {
        return verdict(Bucket::Personal, Confidence::High, "reply/forward in an active thread");
    }

    let free_mail = FREE_MAIL.contains(&domain);
    if !free_mail && ROLE_MKT.contains(&local) {
        return verdict(Bucket::Marketing, Confidence::Med, format!("marketing role address ({local}@)"));
    }
    if !free_mail && (ROLE_NOTIF.contains(&local) || NOTIF.is_match(&text::i_folded(local))) {
        return verdict(Bucket::Notifications, Confidence::Med, "no-reply / notification address");
    }
    if free_mail && looks_human(Some(local)) {
        return verdict(Bucket::Personal, Confidence::Med, "person on a personal mailbox");
    }
    verdict(Bucket::Personal, Confidence::Low, "unclassified \u{2014} review")
}

/// Fraud: a strong pattern from anyone, or a weak one, or money shouted, from a sketchy sender.
fn is_scam(domain: &str, subject: &str, subject_i: &str, name: &str) -> bool {
    let blob = format!("{subject} {name}");
    let blob_i = text::i_folded(&blob);
    if STRONG_SCAM.is_match(&blob_i) {
        return true;
    }
    let tld = domain.rsplit_once('.').map_or("", |(_, tld)| tld);
    let sketchy = FREE_MAIL.contains(&domain) || SUSPICIOUS_TLDS.contains(&tld);
    sketchy
        && (WEAK_SCAM.is_match(&blob_i)
            || (MONEY.is_match(subject_i) && (subject.matches('!').count() >= 3 || SHOUTY.is_match(subject))))
}

/// Government and education, always; health and finance unless the local part is a marketing stream's.
fn is_protected_domain(domain_i: &str, local: &str) -> bool {
    GOV.is_match(domain_i)
        || EDU.is_match(domain_i)
        || ((HEALTH.is_match(domain_i) || FINANCE.is_match(domain_i)) && !ROLE_MKT.contains(&local))
}

/// Whether a local part looks like a person's, not a role's or a mailer's random id.
pub fn looks_human(local: Option<&str>) -> bool {
    let Some(local) = local.filter(|local| !local.is_empty()) else {
        return false;
    };
    if [ROLE_MKT, ROLE_MKT_WEAK, ROLE_NOTIF, ROLE_TXN].iter().any(|roles| roles.contains(&local))
        || NO_REPLY_PREFIXES.iter().any(|p| local.starts_with(p))
        || MACHINE_WORDS.iter().any(|w| local.contains(w))
    {
        return false;
    }
    // A long hex or random token is a machine's.
    if (local.len() >= 12 && local.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)))
        || (local.len() >= 18 && local.bytes().all(|b| b.is_ascii_digit() || b.is_ascii_lowercase()))
    {
        return false;
    }
    if 2 * text::digits(local) > local.chars().count() {
        return false;
    }
    local.bytes().any(|b| b.is_ascii_lowercase())
}

/// Whether a reason `classify` gave says one of the person's rules decided.
pub fn by_rule(reason: Option<&str>) -> bool {
    reason.is_some_and(|reason| reason.contains("by rule"))
}

/// Whether a bucket, by its name, is junk.
pub fn is_junk(bucket: Option<&str>) -> bool {
    bucket.and_then(Bucket::named).is_some_and(Bucket::is_junk)
}

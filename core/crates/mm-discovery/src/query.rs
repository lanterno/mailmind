//! The search grammar, parsed.
//!
//! ```text
//! from:VALUE subject:VALUE body:VALUE to:VALUE cc:VALUE  -> words to find, in that field
//! "a phrase"  bareword                            -> words to find anywhere
//! is:unread|read|flagged|answered|junk            -> a flag
//! is:newsletter|receipt|notification              -> a bucket, by its shorthand
//! is:snoozed                                      -> snoozed mail, which is otherwise out of sight
//! bucket:VALUE  category:VALUE                    -> exact
//! has:attachment                                  -> has one
//! in:FOLDER  account:EMAIL                        -> exact
//! label:NAME                                      -> a Gmail label the message carries, in any case
//! before:YYYY-MM-DD  after:YYYY-MM-DD             -> a time, at the person's own midnight
//! -TOKEN                                          -> negation
//! ```
//!
//! The parser is total: no text fails to parse, and text past [`MAX_CHARS`] is cut. A [`QuerySpec`] is what search,
//! saved views and a bulk command's plan all read a query as. It reads exactly as the Python app's did, down to
//! which characters are space and how a word is lowercased, so a saved search finds what it found there.

pub use crate::text::is_space;
use crate::{text, words};
pub use mm_kernel::isoformat::{Wall, seconds};

/// The most characters of a query the parser reads, after the space around it.
pub const MAX_CHARS: usize = 512;

/// The fields `FIELD:words` searches in.
pub const FIELDS: [&str; 5] = ["from", "subject", "body", "to", "cc"];
/// The flags `is:` asks about.
pub const FLAGS: [&str; 5] = ["unread", "read", "flagged", "answered", "junk"];
/// The shorthand `is:` and `bucket:` take for a bucket, and the classifier's name for it.
pub const BUCKET_ALIAS: [(&str, &str); 15] = [
    ("newsletter", "newsletters/marketing"),
    ("newsletters", "newsletters/marketing"),
    ("marketing", "newsletters/marketing"),
    ("receipt", "receipts/transactional"),
    ("receipts", "receipts/transactional"),
    ("transactional", "receipts/transactional"),
    ("notification", "automated-notifications"),
    ("notifications", "automated-notifications"),
    ("automated", "automated-notifications"),
    ("spam", "spam/scam"),
    ("scam", "spam/scam"),
    ("blocked", "blocked"),
    ("personal", "personal/protected"),
    ("protected", "personal/protected"),
    ("other", "other/personal-ish"),
];
/// What the search box offers as you type, in the order it offers them: the operators this parser reads.
pub const OPERATORS: [&str; 14] = [
    "from", "to", "cc", "subject", "body", "is", "has", "in", "label", "account", "before", "after", "bucket",
    "category",
];
/// The `is:` values worth a line of their own. Most buckets have several words, and one of each is enough to offer.
pub const IS_OFFERED: [&str; 9] =
    ["unread", "read", "flagged", "answered", "junk", "snoozed", "newsletter", "receipt", "notification"];
/// The `has:` values worth offering.
pub const HAS_OFFERED: [&str; 1] = ["attachment"];
/// `is:` values that pick out part of the mailbox: a content lane or a minority flag. Not "read", which is usually
/// most mail, though its inverse "unread" is.
const NARROW_IS: [&str; 14] = [
    "newsletter",
    "newsletters",
    "marketing",
    "receipt",
    "receipts",
    "transactional",
    "notification",
    "notifications",
    "automated",
    "spam",
    "scam",
    "flagged",
    "unread",
    "junk",
];

/// What a clause asks for.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Kind {
    /// Words to find anywhere.
    Text,
    /// Words to find in one field, which [`Clause::field`] names.
    Field,
    /// A flag, which [`Clause::field`] names.
    Flag,
    Snoozed,
    /// A bucket, by the classifier's name for it.
    Bucket,
    /// The AI's category.
    Category,
    Attachment,
    /// A folder, by its name.
    Folder,
    /// A Gmail label, by its name.
    Label,
    /// An account, by its address.
    Account,
    /// Before a time, in seconds since the epoch.
    Before,
    /// At or after a time, in seconds since the epoch.
    After,
}

impl Kind {
    /// Its name, as the Python app's `Kind` has it.
    pub fn name(self) -> &'static str {
        match self {
            Kind::Text => "text",
            Kind::Field => "field",
            Kind::Flag => "flag",
            Kind::Snoozed => "snoozed",
            Kind::Bucket => "bucket",
            Kind::Category => "category",
            Kind::Attachment => "attachment",
            Kind::Folder => "folder",
            Kind::Label => "label",
            Kind::Account => "account",
            Kind::Before => "before",
            Kind::After => "after",
        }
    }
}

/// What a clause holds besides its kind.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Value {
    Text(String),
    Seconds(i64),
}

/// One thing a query asks.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Clause {
    pub kind: Kind,
    pub value: Option<Value>,
    /// The field a [`Kind::Field`] searches, or the flag a [`Kind::Flag`] asks about.
    pub field: Option<&'static str>,
    pub negated: bool,
    /// Asked for, it picks out part of the mailbox.
    pub narrows: bool,
}

impl Clause {
    fn new(kind: Kind, value: Option<Value>, negated: bool, narrows: bool) -> Clause {
        Clause { kind, value, field: None, negated, narrows }
    }

    fn text(kind: Kind, value: &str, negated: bool, narrows: bool) -> Clause {
        Clause::new(kind, Some(Value::Text(value.to_owned())), negated, narrows)
    }
}

/// A query, read.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct QuerySpec {
    pub clauses: Vec<Clause>,
}

impl QuerySpec {
    /// Reads `text`. A date or time written with no zone is in the person's own, which `zone` places: it gives the
    /// seconds since the epoch at a wall-clock time, or None where it can't, and the clause is then left out.
    pub fn parse(text: &str, zone: impl Fn(Wall) -> Option<i64>) -> QuerySpec {
        QuerySpec::parse_with(text, |written| seconds(written, &zone))
    }

    /// Reads `text`, with `dates` reading what follows `before:` and `after:` into seconds since the epoch, or None
    /// where it reads as no time, which leaves the clause out: it says nothing, and is no word to search for either.
    pub fn parse_with(text: &str, dates: impl Fn(&str) -> Option<i64>) -> QuerySpec {
        let text = text.trim_matches(is_space);
        let text = text.char_indices().nth(MAX_CHARS).map_or(text, |(end, _)| &text[..end]);
        // A quote left open, or a backslash at the end, and Python's shlex gave up: the words are then what's
        // between the spaces.
        let tokens = words::split(text)
            .unwrap_or_else(|| text.split(is_space).filter(|w| !w.is_empty()).map(str::to_owned).collect());
        QuerySpec { clauses: tokens.iter().filter_map(|token| clause(token, &dates)).collect() }
    }

    /// Whether it picks out part of the mailbox, which a bulk command must. A folder or an account alone spans
    /// nearly everything, and a negation never narrows on its own.
    pub fn narrows(&self) -> bool {
        self.clauses.iter().any(|c| c.narrows && !c.negated)
    }

    pub fn names_folder(&self) -> bool {
        self.clauses.iter().any(|c| c.kind == Kind::Folder && !c.negated)
    }

    pub fn asks_about_snoozed(&self) -> bool {
        self.clauses.iter().any(|c| c.kind == Kind::Snoozed)
    }
}

fn clause(token: &str, dates: impl Fn(&str) -> Option<i64>) -> Option<Clause> {
    let negated = token.len() > 1 && token.starts_with('-');
    let token = if negated { &token[1..] } else { token };
    // An operator is what comes before the first colon, in any case.
    let (name, value) = token.split_once(':').unzip();
    let name = name.map(text::lower);
    let named = |operator: &str| name.as_deref() == Some(operator);
    let value = value.unwrap_or("");
    let lower = text::lower(value);
    let alias = BUCKET_ALIAS.iter().find(|(word, _)| *word == lower).map(|&(_, bucket)| bucket);
    let narrow_is = NARROW_IS.contains(&lower.as_str());
    let field = FIELDS.into_iter().find(|&f| named(f));

    if let Some(field) = field
        && !value.is_empty()
    {
        return Some(Clause { field: Some(field), ..Clause::text(Kind::Field, value, negated, true) });
    }
    if named("is") {
        if let Some(flag) = FLAGS.into_iter().find(|f| *f == lower) {
            return Some(Clause { field: Some(flag), ..Clause::new(Kind::Flag, None, negated, narrow_is) });
        }
        if lower == "snoozed" {
            return Some(Clause::new(Kind::Snoozed, None, negated, false));
        }
        if let Some(bucket) = alias {
            return Some(Clause::text(Kind::Bucket, bucket, negated, narrow_is));
        }
    }
    if !value.is_empty() {
        if named("bucket") {
            return Some(Clause::text(Kind::Bucket, alias.unwrap_or(lower.as_str()), negated, true));
        }
        if named("category") {
            return Some(Clause::text(Kind::Category, &lower, negated, true));
        }
    }
    if named("has") && lower == "attachment" {
        return Some(Clause::new(Kind::Attachment, None, negated, true));
    }
    if !value.is_empty() {
        if named("in") {
            return Some(Clause::text(Kind::Folder, value, negated, false));
        }
        if named("label") {
            return Some(Clause::text(Kind::Label, value, negated, true));
        }
        if named("account") {
            return Some(Clause::text(Kind::Account, value, negated, false));
        }
        for (operator, kind) in [("before", Kind::Before), ("after", Kind::After)] {
            if named(operator) {
                return dates(value).map(|seconds| Clause::new(kind, Some(Value::Seconds(seconds)), negated, true));
            }
        }
    }
    (!token.trim_matches(is_space).is_empty()).then(|| Clause::text(Kind::Text, token, negated, true))
}

//! Whether a query can be judged of mail as it arrives, which a filter's must be (`mailmind.discovery.domain
//! .arrival`). A message arriving has its headers, what the classifier made of them and, in the Inbox, the start of
//! its text. It has no body stored yet, no attachments read and no AI verdict. Words that need those would quietly
//! never match, so a filter is told so instead, in words for the person making it.

use mm_kernel::isoformat::Wall;

use crate::query::{Kind, MAX_CHARS, QuerySpec, Value};

const INBOX: &str = "INBOX";

/// Why this search can't be a filter, or None where it can. `zone` places a date written with no zone.
pub fn refusal(text: &str, zone: impl Fn(Wall) -> Option<i64>) -> Option<String> {
    if text.chars().count() > MAX_CHARS {
        return Some("That's too long for a filter.".to_owned());
    }
    let spec = QuerySpec::parse(text, zone);
    for c in &spec.clauses {
        let unknown_yet = match c.kind {
            Kind::Attachment => {
                Some("A filter reads mail as it arrives, before its attachments are known, so it can't use has:attachment.")
            }
            Kind::Category => Some("A filter reads mail as it arrives, before the AI has read it, so it can't use category:."),
            Kind::Snoozed => Some("New mail isn't snoozed, so a filter can't use is:snoozed."),
            Kind::Before => Some("A filter reads mail as it arrives, so a date says nothing of it. Take out before:."),
            Kind::After => Some("A filter reads mail as it arrives, so a date says nothing of it. Take out after:."),
            _ => None,
        };
        if let Some(why) = unknown_yet {
            return Some(why.to_owned());
        }
        if c.kind == Kind::Field && c.field == Some("body") {
            return Some(
                "A filter reads mail as it arrives, before its body is stored, so it can't use body:.".to_owned(),
            );
        }
        if c.kind == Kind::Folder && !c.negated && c.value != Some(Value::Text(INBOX.to_owned())) {
            return Some(format!("Filters read mail arriving in the Inbox, so the only folder one can name is in:{INBOX}."));
        }
    }
    let looks_for = spec.clauses.iter().any(|c| matches!(c.kind, Kind::Text | Kind::Field | Kind::Bucket) && !c.negated);
    (!looks_for).then(|| "A filter needs something to look for: a sender, a subject, words, or a kind of mail.".to_owned())
}

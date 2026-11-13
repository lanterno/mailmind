//! What a sweep would take, worked out from mail already stored (`mailmind.curation.domain.sweep_plan`, and
//! `application.sweep`'s `plan_stored`): how an inbox falls into buckets and which of it is junk. No server is asked,
//! so the Curate drawer's counts are instant; a sweep still reads the inbox live before it moves anything.

use std::sync::Arc;

use mm_kernel::errors::Fault;
use serde_json::{Map, Value, json};

use crate::classifier::{self, Bucket};
use crate::ports::{RuleBook, Stored, StoredInbox};
use crate::rules::RuleSets;
use crate::text;

/// How many of a bucket's senders a plan names.
pub const TOP_SENDERS: usize = 4;

/// The rule book's four lists, in the order `rulebook.KEYS` names them.
const KEYS: [&str; 4] = ["keep_senders", "block_senders", "keep_domains", "block_domains"];

/// The keep and block sets a stored rule book holds, lower-cased as `rulebook.normalized` reads them. None for a
/// document that isn't rules: not an object, or a list that isn't one of text.
pub fn sets_of(document: &Value) -> Option<RuleSets> {
    let book = document.as_object()?;
    let mut lists: [Vec<String>; 4] = Default::default();
    for (key, list) in KEYS.into_iter().zip(lists.iter_mut()) {
        match book.get(key) {
            None => {}
            Some(Value::Array(entries)) => {
                *list =
                    entries.iter().map(|e| e.as_str().map(|e| text::lower(e).into_owned())).collect::<Option<_>>()?;
            }
            Some(_) => return None,
        }
    }
    let [keep_senders, block_senders, keep_domains, block_domains] = lists;
    Some(RuleSets::new(keep_senders, block_senders, keep_domains, block_domains))
}

/// (bucket, sender) of a stored message, as a live sweep would judge it. The store keeps no List header, which a sweep
/// reads live: what ingest saw of them shows in the bucket it gave, and the rules come first, so a rule made since
/// still wins.
pub fn stored_bucket(row: &Stored, rules: &RuleSets) -> (Bucket, String) {
    let addr = text::lower(row.from_addr.as_deref().unwrap_or_default()).into_owned();
    let listed = row.bucket.as_deref() == Some(Bucket::Marketing.name())
        || row.list_unsub_raw.as_deref().is_some_and(|raw| !raw.is_empty());
    let subject = row.subject.as_deref().unwrap_or_default();
    let name = row.from_name.as_deref().unwrap_or_default();
    (classifier::classify(&addr, subject, name, listed, rules).bucket, addr)
}

/// How an inbox falls, from each message's (bucket, sender): `in_inbox`, `by_bucket` (the buckets holding mail, in the
/// order they first came), `junk`, `keep`, and `top_senders`, which names each bucket holding mail and every junk
/// bucket too, with nobody in one that holds none.
pub fn tally(mail: impl IntoIterator<Item = (Bucket, String)>) -> Map<String, Value> {
    let mut by: Vec<(Bucket, Vec<String>)> = Vec::new();
    for (bucket, sender) in mail {
        match by.iter_mut().find(|(b, _)| *b == bucket) {
            Some((_, senders)) => senders.push(sender),
            None => by.push((bucket, vec![sender])),
        }
    }
    let in_inbox: usize = by.iter().map(|(_, senders)| senders.len()).sum();
    let junk: usize = by.iter().filter(|(b, _)| b.is_junk()).map(|(_, senders)| senders.len()).sum();
    let by_bucket: Map<String, Value> =
        by.iter().map(|(b, senders)| (b.name().to_owned(), json!(senders.len()))).collect();
    let mut top_senders: Map<String, Value> =
        by.iter().map(|(b, senders)| (b.name().to_owned(), json!(most_common(senders)))).collect();
    for bucket in Bucket::ALL.into_iter().filter(|b| b.is_junk()) {
        top_senders.entry(bucket.name()).or_insert_with(|| json!([]));
    }
    let mut out = Map::new();
    out.insert("in_inbox".into(), json!(in_inbox));
    out.insert("by_bucket".into(), Value::Object(by_bucket));
    out.insert("junk".into(), json!(junk));
    out.insert("keep".into(), json!(in_inbox - junk));
    out.insert("top_senders".into(), Value::Object(top_senders));
    out
}

/// The senders who sent most, at most `TOP_SENDERS` of them, the first to come ahead on a tie (`Counter.most_common`).
fn most_common(senders: &[String]) -> Vec<&str> {
    let mut counted: Vec<(&str, usize)> = Vec::new();
    for sender in senders {
        match counted.iter_mut().find(|(s, _)| s == sender) {
            Some((_, n)) => *n += 1,
            None => counted.push((sender, 1)),
        }
    }
    counted.sort_by_key(|&(_, n)| std::cmp::Reverse(n));
    counted.into_iter().take(TOP_SENDERS).map(|(s, _)| s).collect()
}

/// Every bucket, and whether it is junk, in the order the Curate drawer lists them.
pub fn buckets() -> Value {
    Value::Array(Bucket::ALL.into_iter().map(|b| json!({"name": b.name(), "is_junk": b.is_junk()})).collect())
}

/// The stored twin of a sweep's plan.
#[derive(Debug)]
pub struct StoredPlans {
    inbox: Arc<dyn StoredInbox>,
    rules: Arc<dyn RuleBook>,
}

impl StoredPlans {
    pub fn new(inbox: Arc<dyn StoredInbox>, rules: Arc<dyn RuleBook>) -> StoredPlans {
        StoredPlans { inbox, rules }
    }

    /// What a sweep of each (account, kind)'s inbox would find, from its stored mail classified again with the rules
    /// as they stand: `account`, `kind`, the tally and no `error`, in the order the accounts are named. The rules are
    /// read once for them all.
    pub fn plans_stored(&self, accounts: &[(&str, &str)]) -> Result<Vec<Map<String, Value>>, Fault> {
        let named: Vec<&str> = accounts.iter().map(|(account, _)| *account).collect();
        let inboxes = self.inbox.stored_inboxes(&named)?;
        let sets = match self.rules.rules()? {
            None => RuleSets::default(),
            Some(document) => sets_of(&document).ok_or(Fault::new("MalformedRules"))?,
        };
        let plan = |(account, kind): &(&str, &str), rows: &[Stored]| {
            let mut plan = Map::new();
            plan.insert("account".into(), json!(account));
            plan.insert("kind".into(), json!(kind));
            plan.extend(tally(rows.iter().map(|row| stored_bucket(row, &sets))));
            plan.insert("error".into(), Value::Null);
            plan
        };
        Ok(accounts.iter().zip(&inboxes).map(|(account, rows)| plan(account, rows)).collect())
    }
}

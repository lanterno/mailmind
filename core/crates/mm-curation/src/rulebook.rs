//! The person's keep and block rules, each sender's shelf life and the filters, as the `rules` document holds them
//! (`mailmind.curation.domain.rulebook` and `domain.filters`): what a rule covers and what a change leaves.
//!
//! Expiry is separate from keep and block: a kept sender can also expire. Keep decides what the classifier shows,
//! expiry how long it stays.

use serde_json::{Map, Value, json};

use crate::rules::{RuleSets, domain_listed};
use crate::text::{lower, strip};

/// The four lists, in the order the rule book names them.
pub const KEYS: [&str; 4] = ["keep_senders", "block_senders", "keep_domains", "block_domains"];
/// What a person may say of a sender or a domain.
pub const VERDICTS: [&str; 2] = ["keep", "block"];
/// The most filters the rule book keeps: each is a query against every new message.
pub const MOST_FILTERS: usize = 100;

/// What to do with new mail that fits a search: star it, mark it read, or both.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Filter {
    pub id: String,
    pub q: String,
    pub star: bool,
    pub read: bool,
}

impl Filter {
    pub fn to_json(&self) -> Value {
        json!({"id": self.id, "q": self.q, "star": self.star, "read": self.read})
    }
}

/// The rule book.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Rules {
    /// Each of `KEYS`, lowered and without repeats.
    pub lists: [Vec<String>; 4],
    /// Hours after which Inbox mail from a sender or a domain expires, each positive.
    pub expire_hours: Vec<(String, i64)>,
    pub filters: Vec<Filter>,
}

/// Python's `int()` of what JSON parsed, as a shelf life's hours are read.
fn hours(value: &Value) -> Option<i64> {
    match value {
        Value::Bool(b) => Some(i64::from(*b)),
        Value::Number(n) => n.as_i64().or_else(|| n.as_f64().filter(|f| f.is_finite()).map(|f| f.trunc() as i64)),
        Value::String(s) => strip(s).replace('_', "").parse().ok(),
        _ => None,
    }
}

/// Python's truth of what JSON parsed.
fn truthy(value: Option<&Value>) -> bool {
    match value {
        None | Some(Value::Null) => false,
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_f64().is_some_and(|f| f != 0.0),
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
    }
}

impl Rules {
    pub fn list(&self, key: &str) -> &[String] {
        KEYS.iter().position(|k| *k == key).map_or(&[], |i| &self.lists[i])
    }

    /// The rules a document held, lowered, without repeats, and only the shelf lives that are positive. None for a
    /// shape that isn't rules, where Python raised.
    pub fn normalized(raw: &Value) -> Option<Rules> {
        let raw = raw.as_object()?;
        let mut rules = Rules::default();
        for (i, key) in KEYS.iter().enumerate() {
            let Some(given) = raw.get(*key) else { continue };
            for entry in given.as_array()? {
                let lowered = lower(entry.as_str()?).into_owned();
                if !rules.lists[i].contains(&lowered) {
                    rules.lists[i].push(lowered);
                }
            }
        }
        if truthy(raw.get("expire_hours")) {
            for (token, given) in raw.get("expire_hours")?.as_object()? {
                let h = hours(given)?;
                if h > 0 {
                    let token = lower(token).into_owned();
                    match rules.expire_hours.iter_mut().find(|(t, _)| *t == token) {
                        Some(held) => held.1 = h,
                        None => rules.expire_hours.push((token, h)),
                    }
                }
            }
        }
        match raw.get("filters") {
            None | Some(Value::Null) => {}
            Some(Value::Array(filters)) => {
                for f in filters {
                    let f = f.as_object()?;
                    let (id, q) = (f.get("id")?.as_str()?, f.get("q")?.as_str()?);
                    rules.filters.push(Filter {
                        id: id.to_owned(),
                        q: q.to_owned(),
                        star: truthy(f.get("star")),
                        read: truthy(f.get("read")),
                    });
                }
            }
            Some(_) => return None,
        }
        Some(rules)
    }

    fn json(&self, lists: [Vec<String>; 4], expire: Vec<(String, i64)>) -> Value {
        let mut out = Map::new();
        for (key, list) in KEYS.iter().zip(lists) {
            out.insert((*key).into(), json!(list));
        }
        out.insert("expire_hours".into(), Value::Object(expire.into_iter().map(|(t, h)| (t, json!(h))).collect()));
        out.insert("filters".into(), Value::Array(self.filters.iter().map(Filter::to_json).collect()));
        Value::Object(out)
    }

    /// The rules as `GET /api/rules` gives them.
    pub fn to_json(&self) -> Value {
        self.json(self.lists.clone(), self.expire_hours.clone())
    }

    /// The rules as they are saved: each list sorted without repeats, the shelf lives by token.
    pub fn for_saving(&self) -> Value {
        let lists = self.lists.clone().map(|mut list| {
            list.sort();
            list.dedup();
            list
        });
        let mut expire = self.expire_hours.clone();
        expire.sort();
        self.json(lists, expire)
    }

    /// The keep and block lists as the classifier reads them.
    pub fn sets(&self) -> RuleSets {
        let [a, b, c, d] = self.lists.clone();
        RuleSets::new(a, b, c, d)
    }

    /// Put `token` under `verdict` (`keep` or `block`) and take it out of the opposite one, so a token is never in
    /// both. Returns the list it went in.
    pub fn add(&mut self, token: &str, verdict: &str) -> &'static str {
        let domain = is_domain(token);
        let keep = verdict == "keep";
        let (placed, other) = match (keep, domain) {
            (true, false) => (0, 1),
            (false, false) => (1, 0),
            (true, true) => (2, 3),
            (false, true) => (3, 2),
        };
        self.lists[placed].push(token.to_owned());
        self.lists[other].retain(|x| x != token);
        KEYS[placed]
    }

    /// Drop a keep or block entry. Any shelf life stays: a shelf life of 0 clears that.
    pub fn remove(&mut self, token: &str) {
        for list in &mut self.lists {
            list.retain(|x| x != token);
        }
    }
}

/// A sender or a domain as a rule keeps it.
pub fn token_of(raw: &str) -> String {
    lower(strip(raw)).into_owned()
}

/// Whether a rule could name this: an address or a domain, and a bare word is neither.
pub fn is_token(token: &str) -> bool {
    !token.is_empty() && (token.contains('.') || token.contains('@'))
}

pub fn is_domain(token: &str) -> bool {
    !token.contains('@') && token.contains('.')
}

pub fn domain_of(addr: &str) -> &str {
    addr.rsplit_once('@').map_or(addr, |(_, domain)| domain)
}

/// Whether a keep or block rule already covers this sender, by address or by domain.
pub fn decided(addr: &str, sets: &RuleSets) -> bool {
    let domain = domain_of(addr);
    sets.keeps_sender(addr) || sets.blocks_sender(addr) || sets.keeps_domain(domain) || sets.blocks_domain(domain)
}

/// Whether one of `listed` covers `domain`, the rule book's one matcher.
pub fn listed(domain: &str, listed: &[String]) -> bool {
    domain_listed(domain, &listed.iter().cloned().collect())
}

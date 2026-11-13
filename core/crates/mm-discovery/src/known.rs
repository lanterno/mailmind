//! Who counts as known, for the composer's "new to you" marks (`mailmind.discovery.domain.known` and
//! `application.contacts.KnownContacts`). Known: non-junk mail from them, synced non-draft mail you sent them, a
//! delivered outbox envelope (Bcc included), or a keep_senders rule. And who a new address reads as a slip of the
//! fingers for: someone you've written to, one edit away. The answer carries no subjects, counts or dates.

use std::collections::{BTreeSet, HashMap, HashSet};
use std::sync::Arc;

use mm_kernel::addresses::{self, MAX_RECIPIENTS};
use mm_kernel::errors::{Failure, Problem};
use mm_kernel::text::strip;
use serde_json::{Map, Value, json};

use crate::ports::{AccountNames, ContactMail, Facts, FolderRoles, KeptSenders, Recipient};
use crate::search::cut;
use crate::text::lower;

/// Fewer non-junk messages than this is too little history to call anyone new.
pub const MIN_HISTORY: i64 = 50;
/// The name before the @ this long at least: ed and al are two people, not one typo.
pub const MIN_NAME: usize = 3;

/// Python's `repr()` of what JSON parsed, as near as it matters here: what `str()` writes of a value that isn't text.
pub fn py_repr(value: &Value) -> String {
    match value {
        Value::Null => "None".to_owned(),
        Value::Bool(true) => "True".to_owned(),
        Value::Bool(false) => "False".to_owned(),
        Value::Number(n) => match (n.as_i64(), n.as_u64(), n.as_f64()) {
            (Some(i), _, _) => i.to_string(),
            (_, Some(u), _) => u.to_string(),
            (_, _, Some(f)) => {
                let written = format!("{f:?}");
                match written.split_once('e') {
                    Some((mantissa, exp)) if !exp.starts_with('-') => format!("{mantissa}e+{exp}"),
                    _ => written,
                }
            }
            _ => n.to_string(),
        },
        Value::String(s) => {
            let quote = if s.contains('\'') && !s.contains('"') { '"' } else { '\'' };
            let mut out = String::from(quote);
            for c in s.chars() {
                match c {
                    '\\' => out.push_str("\\\\"),
                    '\n' => out.push_str("\\n"),
                    '\r' => out.push_str("\\r"),
                    '\t' => out.push_str("\\t"),
                    c if c == quote => {
                        out.push('\\');
                        out.push(c);
                    }
                    c if (c as u32) < 0x20 || (0x7f..0xa0).contains(&(c as u32)) => {
                        out.push_str(&format!("\\x{:02x}", c as u32));
                    }
                    c => out.push(c),
                }
            }
            out.push(quote);
            out
        }
        Value::Array(items) => format!("[{}]", items.iter().map(py_repr).collect::<Vec<_>>().join(", ")),
        Value::Object(map) => format!(
            "{{{}}}",
            map.iter().map(|(k, v)| format!("{}: {}", py_repr(&json!(k)), py_repr(v))).collect::<Vec<_>>().join(", ")
        ),
    }
}

/// Python's `str()` of what JSON parsed.
pub fn py_str(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        other => py_repr(other),
    }
}

/// Python's truth of what JSON parsed.
fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// `str(raw or "")`.
fn written(raw: &Value) -> String {
    if truthy(raw) { py_str(raw) } else { String::new() }
}

/// The address mail to `raw` would actually go to, by compose's own parser, so a display name holding someone
/// else's address can't stand in for it. None unless exactly one.
pub fn normal(raw: &Value) -> Option<String> {
    let given = cut(&written(raw), 320);
    let pairs = addresses::parse_addr_list(&[&given])?;
    let [(_, spec)] = pairs.as_slice() else { return None };
    let a = lower(spec);
    (a.chars().count() <= 254).then_some(a)
}

/// `a` and its IDNA twin: the outbox envelope stores Punycode domains, received mail may not.
pub fn forms(a: &str) -> Vec<String> {
    let mut out = vec![a.to_owned()];
    let (local, domain) = a.split_once('@').unwrap_or((a, ""));
    let mut add = |form: String| {
        if !out.contains(&form) {
            out.push(form);
        }
    };
    if let Some(ascii) = addresses::idna_domain(Some(domain)) {
        add(format!("{local}@{}", lower(&ascii)));
    }
    if domain.contains("xn--")
        && let Some(decoded) = addresses::idna_decode(domain)
    {
        add(format!("{local}@{}", lower(&decoded)));
    }
    out
}

/// Addresses asked after, as they can be looked for.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Asked {
    /// Each one's own key in the answer.
    pub keys: Vec<String>,
    /// What no mail could go to.
    pub bad: Vec<String>,
    /// Each form to look for, in the order first asked, and the keys it answers.
    pub want: Vec<(String, BTreeSet<String>)>,
}

impl Asked {
    fn forms(&self) -> Vec<String> {
        self.want.iter().map(|(f, _)| f.clone()).collect()
    }
}

/// What a request asks after. A request that names no list is refused in these words.
pub fn asked(addrs: Option<&[Value]>) -> Result<Asked, Problem> {
    let addrs = addrs.ok_or_else(|| Problem::refused("addrs must be a list"))?;
    if addrs.len() > MAX_RECIPIENTS {
        return Err(Problem::refused("too many addresses"));
    }
    let mut question = Asked::default();
    for raw in addrs {
        let key = cut(&lower(strip(&written(raw))), 320);
        let Some(a) = normal(raw) else {
            question.bad.push(cut(&key, 200));
            continue;
        };
        question.keys.push(key.clone());
        for form in forms(&a) {
            match question.want.iter_mut().find(|(f, _)| *f == form) {
                Some((_, keys)) => {
                    keys.insert(key.clone());
                }
                None => question.want.push((form, BTreeSet::from([key.clone()]))),
            }
        }
    }
    Ok(question)
}

/// `you+tag@host` as `you@host`, the mailbox a plus-address lands in.
pub fn untagged(a: &str) -> String {
    match a.rsplit_once('@') {
        Some((local, domain)) if local.chars().skip(1).any(|c| c == '+') => {
            format!("{}@{domain}", local.split_once('+').map_or(local, |(head, _)| head))
        }
        _ => a.to_owned(),
    }
}

/// Your addresses: the accounts' and those that hold mail here, each with its plain mailbox if it is tagged.
pub fn own_addresses<'a>(groups: impl IntoIterator<Item = &'a String>) -> HashSet<String> {
    groups
        .into_iter()
        .flat_map(|e| {
            let lowered = lower(e);
            let plain = untagged(&lowered);
            [lowered, plain]
        })
        .collect()
}

/// Yours, however it is tagged.
pub fn is_own(a: &str, own: &HashSet<String>) -> bool {
    own.contains(a) || own.contains(&untagged(a))
}

/// (account, folder) of each synced folder that plays Sent.
fn sent_folders(facts: &Facts, roles: &dyn FolderRoles) -> HashSet<(String, String)> {
    facts.synced.iter().filter(|(_, folder)| roles.role_by_name(folder) == "sent").cloned().collect()
}

/// Whether a stored copy of a message says you wrote it.
fn written_by_you(
    r: &Recipient,
    facts: &Facts,
    sent: &HashSet<(String, String)>,
    own: &HashSet<String>,
    roles: &dyn FolderRoles,
) -> bool {
    // The autosaved copy of the message being written must not vouch for its own recipient.
    if facts.draft_folders.contains(&r.folder) {
        return false;
    }
    // Spam forging your From: vouches for no one.
    let yours = is_own(r.sender.as_deref().unwrap_or_default(), own)
        && !r.is_junk
        && !lower(&r.folder).contains("draft")
        && roles.role_by_name(&r.folder) != "junk";
    yours || sent.contains(&(r.account.clone(), r.folder.clone()))
}

/// What an outbox envelope names, lowered, as Python iterated what it parsed; None where it couldn't.
fn envelope(env: Option<&str>) -> Option<Vec<String>> {
    let text = env.filter(|e| !e.is_empty()).unwrap_or("[]");
    match serde_json::from_str::<Value>(text).ok()? {
        Value::Array(items) => Some(items.iter().map(|x| lower(&py_str(x))).collect()),
        Value::Object(map) => Some(map.keys().map(|k| lower(k)).collect()),
        Value::String(s) => Some(s.chars().map(|c| lower(&c.to_string())).collect()),
        _ => None,
    }
}

/// Which of the addresses `wanted` the stored mail vouches for.
fn vouched(facts: &Facts, wanted: &HashSet<String>, own: &HashSet<String>, roles: &dyn FolderRoles) -> HashSet<String> {
    // Junk is left out: one phishing mail can't vouch, whether we or the provider filed it.
    let mut hit: HashSet<String> = facts
        .senders
        .iter()
        .filter(|(_, folder)| roles.role_by_name(folder) != "junk")
        .map(|(a, _)| a.clone())
        .collect();
    let sent = sent_folders(facts, roles);
    hit.extend(facts.copies.iter().filter(|r| written_by_you(r, facts, &sent, own, roles)).map(|r| r.addr.clone()));
    for env in &facts.envelopes {
        if let Some(named) = envelope(env.as_deref()) {
            hit.extend(named.into_iter().filter(|a| wanted.contains(a)));
        }
    }
    hit
}

/// Everyone the stored mail and the outbox say you've written to, the most written-to first, you left out.
fn written_to(facts: &Facts, copies: &[Recipient], own: &HashSet<String>, roles: &dyn FolderRoles) -> Vec<String> {
    let sent = sent_folders(facts, roles);
    let mut counts: HashMap<String, i64> = HashMap::new();
    for r in copies.iter().filter(|r| written_by_you(r, facts, &sent, own, roles)) {
        *counts.entry(r.addr.clone()).or_default() += r.n;
    }
    for env in &facts.envelopes {
        for a in envelope(env.as_deref()).unwrap_or_default() {
            *counts.entry(a).or_default() += 1;
        }
    }
    let mut ranked: Vec<(String, i64)> = counts.into_iter().collect();
    ranked.sort_by(|(a, n), (b, m)| m.cmp(n).then_with(|| a.cmp(b)));
    ranked.into_iter().map(|(a, _)| a).filter(|a| !is_own(a, own)).collect()
}

fn is_digit(c: char) -> bool {
    c.is_numeric()
}

/// Two strings one slip of the fingers apart: a letter added, dropped or changed, or two neighbours swapped.
/// Numbers that differ are siblings (room3, room4), not slips.
pub fn one_slip(a: &str, b: &str) -> bool {
    let (a, b): (Vec<char>, Vec<char>) = (a.chars().collect(), b.chars().collect());
    if a == b || a.len().abs_diff(b.len()) > 1 {
        return false;
    }
    let i = a.iter().zip(&b).position(|(x, y)| x != y).unwrap_or(a.len().min(b.len()));
    if a.len() != b.len() {
        let (longer, shorter) = if a.len() > b.len() { (&a, &b) } else { (&b, &a) };
        return longer.get(i + 1..).unwrap_or_default() == shorter.get(i..).unwrap_or_default();
    }
    if is_digit(a[i]) && is_digit(b[i]) {
        return false;
    }
    let from = |s: &[char], at: usize| s.get(at..).unwrap_or_default().to_vec();
    from(&a, i + 1) == from(&b, i + 1)
        || (b.get(i + 1) == Some(&a[i]) && a.get(i + 1) == Some(&b[i]) && from(&a, i + 2) == from(&b, i + 2))
}

/// Whether `a` reads as `person` mistyped: one slip in the name or in the domain, never in both.
pub fn reads_as_slip(a: &str, person: &str) -> bool {
    let (a, person) = (untagged(a), untagged(person));
    let (name, domain) = a.rsplit_once('@').unwrap_or(("", &a));
    let (their_name, their_domain) = person.rsplit_once('@').unwrap_or(("", &person));
    if domain == their_domain {
        return name.chars().count().min(their_name.chars().count()) >= MIN_NAME && one_slip(name, their_name);
    }
    name == their_name && one_slip(domain, their_domain)
}

#[derive(Clone, Debug, Default)]
struct Verdict {
    known: bool,
    own: bool,
    near: Option<String>,
}

#[derive(Debug)]
pub struct KnownContacts {
    mail: Arc<dyn ContactMail>,
    accounts: Arc<dyn AccountNames>,
    roles: Arc<dyn FolderRoles>,
    rules: Arc<dyn KeptSenders>,
}

impl KnownContacts {
    pub fn new(
        mail: Arc<dyn ContactMail>,
        accounts: Arc<dyn AccountNames>,
        roles: Arc<dyn FolderRoles>,
        rules: Arc<dyn KeptSenders>,
    ) -> KnownContacts {
        KnownContacts { mail, accounts, roles, rules }
    }

    /// Which of these addresses you've exchanged mail with, and for one you haven't, `near` if it reads as a slip of
    /// someone you've written to. `thin` means too little history to call anyone new.
    pub fn ask(&self, addrs: Option<&[Value]>) -> Result<Value, Failure> {
        let question = asked(addrs)?;
        let roles = &*self.roles;
        let facts = self.mail.known_facts(&question.forms(), MIN_HISTORY)?;
        let emails = self.accounts.emails()?;
        let aliases: Vec<String> = self.accounts.aliases()?.into_keys().collect();
        let mut yours = own_addresses(emails.iter().chain(&aliases).chain(&facts.accounts));
        yours.extend(facts.senders.iter().filter(|(_, f)| roles.role_by_name(f) == "sent").map(|(a, _)| a.clone()));
        let wanted: HashSet<String> = question.forms().into_iter().collect();
        let mut hit = vouched(&facts, &wanted, &yours, roles);
        hit.extend(self.rules.kept()?.into_iter().filter(|a| wanted.contains(a)));

        let mut items: Vec<(String, Verdict)> = Vec::new();
        for key in &question.keys {
            if !items.iter().any(|(k, _)| k == key) {
                items.push((key.clone(), Verdict::default()));
            }
        }
        let item =
            |items: &[(String, Verdict)], key: &str| -> Option<usize> { items.iter().position(|(k, _)| k == key) };
        for (form, keys) in &question.want {
            for key in keys {
                let Some(at) = item(&items, key) else { continue };
                if is_own(form, &yours) {
                    items[at].1.own = true;
                    items[at].1.known = true;
                } else if hit.contains(form) {
                    items[at].1.known = true;
                }
            }
        }
        if !items.iter().all(|(_, v)| v.known) {
            // Only someone new can be a slip of someone else.
            let sent: Vec<String> = sent_folders(&facts, roles)
                .into_iter()
                .map(|(_, folder)| folder)
                .collect::<BTreeSet<_>>()
                .into_iter()
                .collect();
            let own: Vec<String> = yours.iter().cloned().collect::<BTreeSet<_>>().into_iter().collect();
            let copies = self.mail.written_copies(&own, &sent)?;
            let people = written_to(&facts, &copies, &yours, roles);
            for (form, keys) in &question.want {
                let new: Vec<usize> =
                    keys.iter().filter_map(|k| item(&items, k)).filter(|&at| !items[at].1.known).collect();
                if new.is_empty() {
                    continue;
                }
                if let Some(who) = people.iter().find(|p| reads_as_slip(form, p)) {
                    for at in new {
                        items[at].1.near.get_or_insert_with(|| who.clone());
                    }
                }
            }
        }
        let mut said = Map::new();
        for (key, v) in items {
            let mut row = Map::new();
            row.insert("known".into(), json!(v.known));
            row.insert("self".into(), json!(v.own));
            if let Some(near) = v.near {
                row.insert("near".into(), json!(near));
            }
            said.insert(key, Value::Object(row));
        }
        Ok(json!({"ok": true, "items": said, "bad": question.bad, "thin": facts.thin}))
    }
}

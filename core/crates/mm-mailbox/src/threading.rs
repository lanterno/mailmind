//! Conservative, account-scoped conversation threading.
//!
//! A [`Threader`] gives each message of an account its thread id, in precedence order:
//!   1. Gmail's X-GM-THRID: the same account and the same thrid are one thread, the most reliable sign.
//!   2. A union-find over the Message-ID, In-Reply-To and References graph.
//!   3. The message's own id, as a thread of one.
//!
//! It never threads on the subject, never spans accounts, and gives every copy a thread id. A minted `rf:` id comes
//! from the root of its union-find component: the smallest id in it, counting the ids its copies only refer to. So
//! a thread keeps its id as replies arrive, and mints the same one again when it is synced whole after every copy
//! went, whichever copies came first.
//!
//! Two ways in, and for mail that only arrives they end with the same threads. [`Threader::conversations`] and
//! [`Threader::place`] take what just arrived and the ids of the threads it touches, which is all a sync has to
//! read. [`Threader::regroup`] takes every copy the account holds: it is the repair.
//!
//! An id is sticky once given, and goes only when a merge absorbs it. The survivor is a Gmail id over an `rf:` id,
//! the smallest of several `rf:` ids, and of two Gmail threads joined by headers the one that comes later in store
//! order.
//!
//! The Python app wrote these ids into the database before this did (`mailmind.mailbox.domain.threading`), so this
//! answers exactly as it did: its whitespace, its regular expressions, its string order, its SHA-1, and the order
//! of its dicts.

use std::borrow::Cow;
use std::collections::{BTreeSet, HashMap, HashSet};
use std::hash::Hash;
use std::sync::LazyLock;

use regex::Regex;

mod sha1;

/// How many ids of a References header count, nearest first: a forged one can be as long as it likes.
pub const REFERENCES_MAX: usize = 1000;

/// How a copy names its Gmail thread among the ids it names. No Message-ID starts so.
const GMAIL_NAME: &str = "gm:";

/// What marks a thread among the ids in the union-find. No id starts so, and nothing mints from one.
const THREAD_NODE: &str = "\0";

/// The digits of a minted id, lowercase as `hexdigest()` writes them.
const HEX: &[u8; 16] = b"0123456789abcdef";

// Python's `\s` in a str pattern is what `str.isspace()` holds: Unicode's White_Space, which is the regex crate's
// `\s`, and the four information separators U+001C to U+001F as well.
static ID: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"<[^<>\s\x1C-\x1F]+>").expect("a valid pattern"));

// Python's `$` also matches before a final newline. The id it checks is stripped first, so none is ever there.
static WELL: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^<[^<>\s\x1C-\x1F]+@[^<>\s\x1C-\x1F]+>$").expect("a valid pattern"));

/// Whether Python's `str.strip()` strips `c`: White_Space, and the information separators U+001C to U+001F.
fn is_python_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// `text.strip()`, as Python strips it.
fn strip(text: &str) -> &str {
    text.trim_matches(is_python_space)
}

/// The ids written in a header, in order: `re.findall(r"<[^<>\s]+>", blob or "")`.
fn ids(blob: Option<&str>) -> impl Iterator<Item = &str> {
    ID.find_iter(blob.unwrap_or("")).map(|found| found.as_str())
}

/// What threading reads of one stored copy of a message: the Python app's `Copy`.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct StoredCopy {
    pub pk: i64,
    pub message_id: Option<String>,
    pub gmail_thread: Option<String>,
    pub in_reply_to: Option<String>,
    pub refs: Option<String>,
    pub thread_id: Option<String>,
    pub folder: String,
    pub ts: Option<i64>,
}

impl StoredCopy {
    /// Gmail's thread id for it, stripped, or empty.
    pub fn thrid(&self) -> &str {
        strip(self.gmail_thread.as_deref().unwrap_or(""))
    }

    /// Its own id, or a garbage or blank one's stand-in, which no other copy can name.
    pub fn key(&self) -> Cow<'_, str> {
        let mid = strip(self.message_id.as_deref().unwrap_or(""));
        if WELL.is_match(mid) { Cow::Borrowed(mid) } else { Cow::Owned(format!("pk:{}", self.pk)) }
    }

    /// The ids it says it follows: all of In-Reply-To, and the last [`REFERENCES_MAX`] of References.
    pub fn references(&self) -> Vec<&str> {
        let mut out: Vec<&str> = ids(self.in_reply_to.as_deref()).collect();
        let refs: Vec<&str> = ids(self.refs.as_deref()).collect();
        out.extend_from_slice(&refs[refs.len().saturating_sub(REFERENCES_MAX)..]);
        out
    }

    /// Everything another copy can meet it by: two copies that share a name are one thread.
    pub fn names(&self) -> BTreeSet<Cow<'_, str>> {
        let mut names: BTreeSet<Cow<'_, str>> = self.references().into_iter().map(Cow::Borrowed).collect();
        let key = self.key();
        if key.starts_with('<') {
            names.insert(key);
        }
        let thrid = self.thrid();
        if !thrid.is_empty() {
            names.insert(Cow::Owned(format!("{GMAIL_NAME}{thrid}")));
        }
        names
    }

    /// Its thread id, when it has one: Python skipped an empty one as it skipped none.
    fn held(&self) -> Option<&str> {
        self.thread_id.as_deref().filter(|tid| !tid.is_empty())
    }
}

/// Sorts copies by folder, date and row: which of two Gmail threads comes later is decided in this order. A copy
/// with no date comes before one dated 0. The sort is stable, as Python's is, so equal copies keep their order.
fn in_store_order(copies: &mut [&StoredCopy]) {
    fn place(m: &StoredCopy) -> (&str, bool, i64, i64) {
        (&m.folder, m.ts.is_some(), m.ts.unwrap_or(0), m.pk)
    }
    copies.sort_by(|a, b| place(a).cmp(&place(b)));
}

/// A disjoint set over names whose root is always the smallest name in it, compared as Python compares strings:
/// by code point, which is the order of their UTF-8 bytes. The root is what a brand-new thread's id is minted from,
/// so it must not depend on the order of the unions.
#[derive(Debug, Default)]
struct UnionFind<'a> {
    at: HashMap<Cow<'a, str>, usize>,
    name: Vec<Cow<'a, str>>,
    parent: Vec<usize>,
}

impl<'a> UnionFind<'a> {
    fn node(&mut self, name: Cow<'a, str>) -> usize {
        if let Some(&at) = self.at.get(name.as_ref()) {
            return at;
        }
        let at = self.parent.len();
        self.name.push(name.clone());
        self.at.insert(name, at);
        self.parent.push(at);
        at
    }

    fn find(&mut self, mut x: usize) -> usize {
        while self.parent[x] != x {
            self.parent[x] = self.parent[self.parent[x]];
            x = self.parent[x];
        }
        x
    }

    fn union(&mut self, a: usize, b: usize) {
        let (ra, rb) = (self.find(a), self.find(b));
        if ra == rb {
            return;
        }
        let (ra, rb) = if self.name[rb] < self.name[ra] { (rb, ra) } else { (ra, rb) };
        self.parent[rb] = ra;
    }
}

/// Keys in the order they were first inserted, each with the value it was given last: a Python dict filled by
/// `update` and `setdefault`.
#[derive(Debug)]
struct Ordered<K, V> {
    at: HashMap<K, usize>,
    pairs: Vec<(K, V)>,
}

impl<K: Clone + Eq + Hash, V> Ordered<K, V> {
    fn new() -> Self {
        Ordered { at: HashMap::new(), pairs: Vec::new() }
    }

    /// `dict[key] = value`: a key already there keeps its place.
    fn set(&mut self, key: K, value: V) {
        match self.at.get(&key) {
            Some(&at) => self.pairs[at].1 = value,
            None => self.insert_new(key, value),
        }
    }

    /// `dict.setdefault(key, value)`: a key already there keeps its value too.
    fn set_default(&mut self, key: K, value: V) {
        if !self.at.contains_key(&key) {
            self.insert_new(key, value);
        }
    }

    fn insert_new(&mut self, key: K, value: V) {
        self.at.insert(key.clone(), self.pairs.len());
        self.pairs.push((key, value));
    }
}

/// Copies placed together: `arrivals`, and `held`, the ids of the threads they touch.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Conversation<'c> {
    /// The smallest name in its component, which a brand-new thread's id is minted from.
    pub root: String,
    /// Its copies, in the order they arrived.
    pub arrivals: Vec<&'c StoredCopy>,
    /// The ids of the threads it holds or touches, each once, in the order they were met. Python kept a set, so
    /// nothing it decides depends on this order but the order of [`Placement::merged`].
    pub held: Vec<String>,
}

/// Where a conversation's copies go.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Placement {
    /// Each copy's thread id now, by pk, each pk once, in the order a Python dict would hold them.
    pub given: Vec<(i64, String)>,
    /// Each id absorbed, and the id that absorbed it, each absorbed id once, in the order a Python dict would hold
    /// them. Whoever keeps state under an absorbed id moves it to its survivor.
    pub merged: Vec<(String, String)>,
}

/// Decides the thread ids of one account's mail, and holds no state of its own.
#[derive(Clone, Debug)]
pub struct Threader {
    account: String,
}

impl Threader {
    pub fn new(account: impl Into<String>) -> Self {
        Threader { account: account.into() }
    }

    fn gmail_id(&self, thrid: &str) -> String {
        format!("gm:{}:{thrid}", self.account)
    }

    /// A brand-new thread's id: the first 16 hex digits of the SHA-1 of its root's UTF-8.
    fn minted(&self, root: &str) -> String {
        let digest = sha1::digest(root.as_bytes());
        let mut hex = String::with_capacity(16);
        for byte in &digest[..8] {
            hex.push(HEX[usize::from(byte >> 4)] as char);
            hex.push(HEX[usize::from(byte & 0xf)] as char);
        }
        format!("rf:{}:{hex}", self.account)
    }

    /// Copies that belong together, with the threads they touch: `touching` says, for each name the arrivals name,
    /// the ids of the threads that hold a copy naming it. Two threads touched by one conversation are one thread
    /// from now on. An arrival's own id counts as touched, so one whose references changed stays in its thread,
    /// with or without a Message-ID for its links to find it by.
    ///
    /// The conversations come in the order their first copies arrived.
    pub fn conversations<'c>(
        &self,
        arrivals: &'c [StoredCopy],
        touching: &[(String, Vec<String>)],
    ) -> Vec<Conversation<'c>> {
        group(arrivals.iter(), touching)
    }

    fn gmail_ids(&self, conversation: &Conversation<'_>) -> BTreeSet<String> {
        let mut gmail: BTreeSet<String> = conversation
            .arrivals
            .iter()
            .map(|m| m.thrid())
            .filter(|thrid| !thrid.is_empty())
            .map(|thrid| self.gmail_id(thrid))
            .collect();
        gmail.extend(conversation.held.iter().filter(|tid| tid.starts_with("gm:")).cloned());
        gmail
    }

    /// The Gmail threads whose copies [`Threader::place`] must see, since it has to choose between them.
    pub fn contested(&self, conversations: &[Conversation<'_>]) -> BTreeSet<String> {
        conversations
            .iter()
            .filter(|conversation| self.gmail_ids(conversation).len() > 1)
            .flat_map(|conversation| conversation.held.iter().filter(|tid| tid.starts_with("gm:")).cloned())
            .collect()
    }

    fn survivor(&self, conversation: &Conversation<'_>, members: &[StoredCopy]) -> String {
        let gmail = self.gmail_ids(conversation);
        if gmail.len() > 1 {
            // The later one, as a whole recompute of them would say: the thread whose first copy in store order
            // comes last.
            let held: HashSet<&str> = conversation.held.iter().map(String::as_str).collect();
            let mut copies: Vec<&StoredCopy> = conversation.arrivals.clone();
            copies.extend(members.iter().filter(|m| m.thread_id.as_deref().is_some_and(|tid| held.contains(tid))));
            in_store_order(&mut copies);
            let mut seen = HashSet::new();
            let mut latest = None;
            for thrid in copies.iter().map(|m| m.thrid()).filter(|thrid| !thrid.is_empty()) {
                if seen.insert(thrid) {
                    latest = Some(thrid);
                }
            }
            return match latest {
                Some(thrid) => self.gmail_id(thrid),
                None => gmail.into_iter().next().unwrap_or_default(),
            };
        }
        if let Some(only) = gmail.into_iter().next() {
            return only;
        }
        // Reuse the sticky id, the smallest when a merge joined two.
        if let Some(smallest) = conversation.held.iter().min() {
            return smallest.clone();
        }
        // Brand-new: hash its root once.
        self.minted(&conversation.root)
    }

    /// Where each conversation's arrivals go, and which ids that absorbs: every copy holding one takes the
    /// survivor. `members`: the copies of the threads [`Threader::contested`] named.
    pub fn place(&self, conversations: &[Conversation<'_>], members: &[StoredCopy]) -> Placement {
        let mut given = Ordered::new();
        let mut merged = Ordered::new();
        for conversation in conversations {
            let survivor = self.survivor(conversation, members);
            for m in &conversation.arrivals {
                given.set(m.pk, survivor.clone());
            }
            for tid in conversation.held.iter().filter(|tid| **tid != survivor) {
                merged.set(tid.clone(), survivor.clone());
            }
        }
        Placement { given: given.pairs, merged: merged.pairs }
    }

    /// Every copy the account holds, regrouped from what each names. It trusts no id it finds but an `rf:` one, so
    /// a `gm:` id whose every Gmail copy has gone gives way. An id absorbed here is one no copy holds any more: one
    /// a split left on some copies still has them, and isn't in `merged`.
    pub fn regroup(&self, copies: &[StoredCopy]) -> Placement {
        let mut rows: Vec<&StoredCopy> = copies.iter().collect();
        in_store_order(&mut rows);
        let mut given = Ordered::new();
        for mut conversation in group(rows.iter().copied(), &[]) {
            conversation.held.retain(|tid| tid.starts_with("rf:"));
            let survivor = self.survivor(&conversation, &[]);
            for m in &conversation.arrivals {
                given.set(m.pk, survivor.clone());
            }
        }
        let held: HashSet<&str> = given.pairs.iter().map(|(_, tid)| tid.as_str()).collect();
        let mut merged = Ordered::new();
        for m in &rows {
            if let Some(tid) = m.held().filter(|tid| !held.contains(tid)) {
                let survivor = given.at.get(&m.pk).map(|&at| given.pairs[at].1.clone()).unwrap_or_default();
                merged.set_default(tid.to_owned(), survivor);
            }
        }
        Placement { given: given.pairs, merged: merged.pairs }
    }
}

/// The union-find over what the arrivals name and the threads that touches, cut into conversations.
fn group<'c>(
    arrivals: impl Iterator<Item = &'c StoredCopy>,
    touching: &[(String, Vec<String>)],
) -> Vec<Conversation<'c>> {
    let arrivals: Vec<&'c StoredCopy> = arrivals.collect();
    let mut uf = UnionFind::default();
    let mut keys = Vec::with_capacity(arrivals.len());
    for &m in &arrivals {
        // Its names, without collecting them: a name met twice, or its own id, joins nothing more.
        let key = uf.node(m.key());
        keys.push(key);
        for name in m.references() {
            let name = uf.node(Cow::Borrowed(name));
            uf.union(key, name);
        }
        let thrid = m.thrid();
        if !thrid.is_empty() {
            let name = uf.node(Cow::Owned(format!("{GMAIL_NAME}{thrid}")));
            uf.union(key, name);
        }
    }
    for (name, tids) in touching {
        let name = uf.node(Cow::Borrowed(name));
        for tid in tids {
            let thread = uf.node(Cow::Owned(format!("{THREAD_NODE}{tid}")));
            uf.union(name, thread);
        }
    }
    let mut found: HashMap<usize, usize> = HashMap::new();
    let mut out: Vec<Conversation<'c>> = Vec::new();
    let mut held: Vec<HashSet<&str>> = Vec::new();
    for (m, key) in arrivals.iter().copied().zip(keys) {
        let root = uf.find(key);
        let at = *found.entry(root).or_insert_with(|| {
            out.push(Conversation { root: uf.name[root].to_string(), arrivals: Vec::new(), held: Vec::new() });
            held.push(HashSet::new());
            out.len() - 1
        });
        out[at].arrivals.push(m);
        if let Some(tid) = m.held()
            && held[at].insert(tid)
        {
            out[at].held.push(tid.to_owned());
        }
    }
    for (name, tids) in touching {
        let Some(&name) = uf.at.get(name.as_str()) else { continue };
        let root = uf.find(name);
        if let Some(&at) = found.get(&root) {
            for tid in tids {
                if held[at].insert(tid) {
                    out[at].held.push(tid.clone());
                }
            }
        }
    }
    out
}

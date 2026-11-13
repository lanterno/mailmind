//! Finding mail by what a person types (`mailmind.discovery.application.search` and `domain.search`): what a request
//! asks, one result a message, and a page of them as the list shows each.

use std::collections::BTreeMap;
use std::sync::Arc;

use mm_kernel::errors::{Failure, Problem};
use mm_kernel::isoformat::Wall;
use mm_kernel::seams::{Clock, Moment};
use mm_kernel::zone::{self, Zone};
use mm_kernel::{relative, verdict, when};
use serde_json::{Map, Value, json};

use crate::ports::{AccountNames, FolderRoles, Found, SearchIndex};
use crate::query::QuerySpec;

pub const NO_SUBJECT: &str = "(no subject)";

/// What a request asks once something is typed (`search.Asked`).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Asked {
    pub q: String,
    pub scope: String,
    /// The one account searched: None under every account, whichever the request named.
    pub account: Option<String>,
    pub limit: i64,
    pub offset: i64,
    /// Only this conversation of `account`.
    pub thread: Option<String>,
}

impl Asked {
    /// What a request asks, its limit and offset as given (80 and 0 where it gives none): a limit is held to 1 to
    /// 200 and an offset to 0 and up.
    pub fn new(
        q: &str,
        scope: Option<&str>,
        account: Option<&str>,
        limit: i64,
        offset: i64,
        thread: Option<&str>,
    ) -> Asked {
        let scope = scope.unwrap_or("account").to_owned();
        Asked {
            q: q.to_owned(),
            account: if scope == "all" { None } else { account.map(str::to_owned) },
            scope,
            limit: limit.clamp(1, 200),
            offset: offset.max(0),
            thread: thread.filter(|t| !t.is_empty()).map(str::to_owned),
        }
    }
}

/// The wall-clock time `wall` in `zone`, in seconds since the epoch: how a search places a date written with no zone.
pub fn placed(zone: &dyn Zone, wall: Wall) -> Option<i64> {
    let days = when::days_from_civil(i64::from(wall.year), wall.month, wall.day);
    let local = days * 86_400 + i64::from(wall.hour * 3600 + wall.minute * 60 + wall.second);
    Some(zone::place(zone, local))
}

#[derive(Debug)]
pub struct Search {
    index: Arc<dyn SearchIndex>,
    accounts: Arc<dyn AccountNames>,
    roles: Arc<dyn FolderRoles>,
    clock: Arc<dyn Clock>,
    zone: Arc<dyn Zone>,
}

impl Search {
    pub fn new(
        index: Arc<dyn SearchIndex>,
        accounts: Arc<dyn AccountNames>,
        roles: Arc<dyn FolderRoles>,
        clock: Arc<dyn Clock>,
        zone: Arc<dyn Zone>,
    ) -> Search {
        Search { index, accounts, roles, clock, zone }
    }

    /// The answer with nothing typed.
    pub fn nothing_asked() -> Value {
        json!({"ok": true, "query": "", "items": [], "total": 0, "has_more": false})
    }

    /// A page of what the query finds. A query the index won't take is refused.
    pub fn answer(&self, want: &Asked) -> Result<Value, Failure> {
        let spec = QuerySpec::parse(&want.q, |wall| placed(&*self.zone, wall));
        let aliases = self.accounts.aliases()?;
        let rows = self
            .index
            .search(&spec, want.account.as_deref(), want.limit, want.offset, want.thread.as_deref(), &*self.roles)?
            .ok_or_else(|| Problem::refused("bad query"))?;
        let now = self.clock.now();
        let limit = usize::try_from(want.limit).unwrap_or(0);
        let items: Vec<Value> = rows.iter().take(limit).map(|r| hit(r, &aliases, now)).collect();
        Ok(json!({
            "ok": true,
            "query": want.q,
            "scope": want.scope,
            "items": items,
            "offset": want.offset,
            "has_more": rows.len() > limit,
            "generated": when::iso(now),
        }))
    }
}

/// The first `n` characters, as Python's `[:n]` cuts a string.
pub(crate) fn cut(text: &str, n: usize) -> String {
    text.chars().take(n).collect()
}

/// A result as the list shows it.
pub fn hit(r: &Found, aliases: &BTreeMap<String, Option<String>>, now: Moment) -> Value {
    fn text(v: &Option<String>) -> Option<&str> {
        v.as_deref().filter(|s| !s.is_empty())
    }
    let mut row = Map::new();
    row.insert("key".into(), json!(r.key));
    row.insert("account".into(), json!(r.account));
    row.insert("folder".into(), json!(r.folder));
    row.insert("uid".into(), json!(r.uid));
    row.insert("alias".into(), json!(aliases.get(&r.account).cloned().flatten()));
    row.insert("from_name".into(), json!(text(&r.from_name).or(r.from_addr.as_deref())));
    row.insert("from_addr".into(), json!(r.from_addr));
    row.insert("subject".into(), json!(text(&r.subject).unwrap_or(NO_SUBJECT)));
    row.insert("when".into(), json!(relative::rel_time(r.date_iso.as_deref(), now)));
    row.insert("date".into(), json!(cut(r.date_iso.as_deref().unwrap_or_default(), 10)));
    row.insert("snippet".into(), json!(cut(r.snippet.as_deref().unwrap_or_default(), 200)));
    row.insert("seen".into(), json!(r.seen));
    row.insert("flagged".into(), json!(r.flagged));
    row.insert("has_attach".into(), json!(r.has_attach));
    row.insert("is_junk".into(), json!(r.is_junk));
    row.insert("bucket".into(), json!(r.bucket));
    row.insert("thread_id".into(), json!(r.thread_id));
    row.insert("importance".into(), json!(r.importance));
    row.insert("action".into(), json!(r.action));
    verdict::answers(&mut row, r.importance, r.category.as_deref(), r.action.as_deref());
    Value::Object(row)
}

/// One result a message, out of rows in the order to show with the copy to show first: after `offset` of them, up
/// to one more than `limit`. A row with no key is a message of its own.
pub fn first_copies(rows: impl IntoIterator<Item = Found>, limit: i64, offset: i64) -> Vec<Found> {
    let mut out = Vec::new();
    let mut seen = std::collections::HashSet::new();
    for r in rows {
        let key = r.key.filter(|k| *k != 0).unwrap_or(-r.pk);
        if seen.insert(key) {
            if seen.len() as i64 > offset {
                out.push(r);
            }
            if out.len() as i64 > limit {
                break;
            }
        }
    }
    out
}

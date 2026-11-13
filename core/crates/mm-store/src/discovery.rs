//! What Discovery reads of the database (`mailmind.discovery.adapters`): a query as SQLite's full-text search and
//! SQL (`fts`), the stored mail as a search, a card and the composer's marks read it (`sqlite`), and the saved views
//! (`views_store`). It writes nothing but the views document.
//!
//! Injection-safe: the full-text column specs are fixed literals, every value is quoted for FTS5 (an embedded `"`
//! doubled) and the whole MATCH string is one bound parameter; every other value is a bound parameter too.

use std::collections::HashSet;
use std::fmt;
use std::sync::Arc;

use mm_discovery::ports::{
    ContactMail, Facts, FolderRoles, Found, Recent, Recipient, SearchIndex, Sender, Tally, ViewsStore,
};
use mm_discovery::query::{Clause, Kind, QuerySpec, Value as Said};
use mm_discovery::search::first_copies;
use mm_kernel::errors::Fault;
use mm_kernel::seams::Clock;
use rusqlite::types::Value as Sql;
use rusqlite::{Connection, Row, params_from_iter};
use serde_json::Value;

use crate::blocks;
use crate::db::Database;
use crate::placeholders;

/// How much a hit counts in each msg_fts column: subject, from_name, from_addr, recipients, snippet, body_text.
pub const BM25: &str = "bm25(msg_fts,8.0,4.0,4.0,2.0,1.0,3.0)";

/// A stored row of `messages` named `t` is snoozed (`kernel.snoozed`).
pub fn snoozed_sql(t: &str) -> String {
    format!("({t}.message_key, {t}.folder) IN (SELECT message_key, folder FROM snoozes)")
}

/// It isn't. One with no key isn't snoozed, where a NOT IN would leave it out of every list.
pub fn not_snoozed_sql(t: &str) -> String {
    format!("NOT EXISTS (SELECT 1 FROM snoozes z WHERE z.message_key={t}.message_key AND z.folder={t}.folder)")
}

fn quoted(value: &str) -> String {
    format!("\"{}\"", value.replace('"', "\"\""))
}

fn text(c: &Clause) -> String {
    match &c.value {
        Some(Said::Text(t)) => t.clone(),
        Some(Said::Seconds(s)) => s.to_string(),
        None => String::new(),
    }
}

/// A query as (the MATCH string, or None when it is purely structured; the WHERE fragments; their parameters). Mail
/// with no value for a negated field isn't left out.
pub fn to_sql(spec: &QuerySpec) -> (Option<String>, Vec<String>, Vec<Sql>) {
    let (mut positive, mut negative, mut wheres, mut params) = (Vec::new(), Vec::new(), Vec::new(), Vec::new());
    for c in &spec.clauses {
        let side = if c.negated { &mut negative } else { &mut positive };
        match c.kind {
            Kind::Text => side.push(quoted(&text(c))),
            Kind::Field => {
                let column = match c.field.unwrap_or_default() {
                    "from" => "{from_name from_addr}",
                    "subject" => "subject",
                    "body" => "body_text",
                    _ => "recipients",
                };
                side.push(format!("{column} : {}", quoted(&text(c))));
            }
            Kind::Flag => {
                let (column, on) = match c.field.unwrap_or_default() {
                    "unread" => ("seen", 0),
                    "read" => ("seen", 1),
                    "flagged" => ("flagged", 1),
                    "answered" => ("answered", 1),
                    _ => ("is_junk", 1),
                };
                wheres.push(format!("m.{column} = ?"));
                params.push(Sql::Integer(if c.negated { 1 - on } else { on }));
            }
            Kind::Snoozed => wheres.push(if c.negated { not_snoozed_sql("m") } else { snoozed_sql("m") }),
            Kind::Bucket | Kind::Category | Kind::Folder | Kind::Account => {
                let (column, unlike) = match c.kind {
                    Kind::Bucket => ("m.bucket", "IS NOT"),
                    Kind::Category => ("s.category", "IS NOT"),
                    Kind::Folder => ("m.folder", "<>"),
                    _ => ("m.account", "<>"),
                };
                wheres.push(format!("{column} {} ?", if c.negated { unlike } else { "=" }));
                params.push(Sql::Text(text(c)));
            }
            Kind::Label => {
                wheres.push(format!(
                    "{}EXISTS (SELECT 1 FROM message_labels l WHERE l.message_pk = m.pk AND l.label COLLATE NOCASE IN (?, ?))",
                    if c.negated { "NOT " } else { "" }
                ));
                params.push(Sql::Text(text(c)));
                params.push(Sql::Text(format!("\\{}", text(c))));
            }
            Kind::Attachment => {
                wheres.push("m.has_attach = ?".to_owned());
                params.push(Sql::Integer(if c.negated { 0 } else { 1 }));
            }
            Kind::Before | Kind::After => {
                let before = (c.kind == Kind::Before) != c.negated;
                wheres.push(if before { "m.ts < ?" } else { "m.ts >= ?" }.to_owned());
                params.push(match &c.value {
                    Some(Said::Seconds(s)) => Sql::Integer(*s),
                    _ => Sql::Text(text(c)),
                });
            }
        }
    }
    let matched = match (positive.is_empty(), negative.is_empty()) {
        (false, false) => Some(format!("{} NOT ({})", positive.join(" "), negative.join(" OR "))),
        (false, true) => Some(positive.join(" ")),
        (true, _) => {
            // FTS5 has no bare NOT: what MATCH finds is left out instead.
            if !negative.is_empty() {
                wheres.push("m.pk NOT IN (SELECT rowid FROM msg_fts WHERE msg_fts MATCH ?)".to_owned());
                params.push(Sql::Text(negative.join(" OR ")));
            }
            None
        }
    };
    (matched, wheres, params)
}

/// `wheres`, leaving out snoozed mail unless the query asks about it.
pub fn visible(mut wheres: Vec<String>) -> Vec<String> {
    let (snoozed, not) = (snoozed_sql("m"), not_snoozed_sql("m"));
    if !wheres.iter().any(|w| *w == snoozed || *w == not) {
        wheres.push(not);
    }
    wheres
}

/// SQL and parameters for: a row's folder plays one of `roles`.
fn in_folders_playing(
    c: &Connection,
    roles: &dyn FolderRoles,
    playing: &[&str],
) -> rusqlite::Result<(String, Vec<Sql>)> {
    let pairs: Vec<(String, String)> = c
        .prepare_cached("SELECT DISTINCT account, folder FROM messages")?
        .query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?
        .collect::<Result<Vec<_>, _>>()?
        .into_iter()
        .filter(|(account, folder): &(String, String)| playing.contains(&roles.role(account, folder).as_str()))
        .collect();
    if pairs.is_empty() {
        // A bare 0 in an ORDER BY would name a column.
        return Ok(("0=1".to_owned(), Vec::new()));
    }
    let values = vec!["(?,?)"; pairs.len()].join(",");
    let params = pairs.into_iter().flat_map(|(a, f)| [Sql::Text(a), Sql::Text(f)]).collect();
    Ok((format!("(m.account, m.folder) IN (VALUES {values})"), params))
}

fn truth(r: &Row, column: &str) -> rusqlite::Result<bool> {
    Ok(r.get::<_, Option<i64>>(column)?.unwrap_or(0) != 0)
}

fn found(r: &Row) -> rusqlite::Result<Found> {
    Ok(Found {
        pk: r.get("pk")?,
        key: r.get("message_key")?,
        uid: r.get("uid")?,
        account: r.get("account")?,
        folder: r.get("folder")?,
        from_addr: r.get("from_addr")?,
        from_name: r.get("from_name")?,
        subject: r.get("subject")?,
        date_iso: r.get("date_iso")?,
        snippet: r.get("snippet")?,
        seen: truth(r, "seen")?,
        flagged: truth(r, "flagged")?,
        has_attach: truth(r, "has_attach")?,
        is_junk: truth(r, "is_junk")?,
        bucket: r.get("bucket")?,
        thread_id: r.get("thread_id")?,
        importance: r.get("importance")?,
        category: r.get("category")?,
        action: r.get("action")?,
    })
}

const COLUMNS: &str = "m.pk,m.message_key,m.uid,m.account,m.folder,m.from_addr,m.from_name,m.subject,m.date_iso,m.ts,\
                       m.snippet,m.seen,m.flagged,m.has_attach,m.is_junk,m.bucket,m.thread_id,s.importance,s.category,\
                       s.action";

/// What the search asked runs as, run: an error SQLite gives is the query's, which the index rejects.
fn search_in(
    c: &Connection,
    spec: &QuerySpec,
    account: Option<&str>,
    limit: i64,
    offset: i64,
    thread: Option<&str>,
    roles: &dyn FolderRoles,
) -> rusqlite::Result<Vec<Found>> {
    let (matched, wheres, mut params) = to_sql(spec);
    let mut wheres = visible(wheres);
    // On its way out by a queued move.
    wheres.push("m.pending_removal IS NULL".to_owned());
    if let Some(account) = account.filter(|a| !a.is_empty()) {
        wheres.push("m.account = ?".to_owned());
        params.push(Sql::Text(account.to_owned()));
    }
    if let Some(thread) = thread {
        wheres.push("m.thread_id = ?".to_owned());
        params.push(Sql::Text(thread.to_owned()));
    }
    let where_sql: String = wheres.iter().map(|w| format!(" AND {w}")).collect();
    let (in_all, in_all_params) = in_folders_playing(c, roles, &["all"])?;
    match matched {
        Some(matched) => {
            let sql = format!(
                "SELECT * FROM (SELECT {COLUMNS}, h.score, \
                 ROW_NUMBER() OVER (PARTITION BY COALESCE(m.message_key, -m.pk) \
                 ORDER BY m.folder='INBOX' DESC, {in_all}, m.pk DESC) AS nth \
                 FROM (SELECT rowid AS pk, {BM25} AS score FROM msg_fts WHERE msg_fts MATCH ?) h \
                 JOIN messages m ON m.pk=h.pk \
                 LEFT JOIN assessments s ON s.message_key=m.message_key \
                 WHERE 1=1{where_sql}) \
                 WHERE nth=1 ORDER BY score ASC, ts DESC LIMIT ? OFFSET ?"
            );
            let all: Vec<Sql> = in_all_params
                .into_iter()
                .chain([Sql::Text(matched)])
                .chain(params)
                .chain([Sql::Integer(limit + 1), Sql::Integer(offset)])
                .collect();
            c.prepare(&sql)?.query_map(params_from_iter(all), found)?.collect()
        }
        None => {
            // Newest first stops early, which sorting every copy wouldn't.
            let sql = format!(
                "SELECT {COLUMNS} FROM messages m LEFT JOIN assessments s ON s.message_key=m.message_key \
                 WHERE 1=1{where_sql} ORDER BY m.ts DESC, m.folder='INBOX' DESC, {in_all}, m.pk DESC"
            );
            let all: Vec<Sql> = params.into_iter().chain(in_all_params).collect();
            let mut statement = c.prepare(&sql)?;
            let mut rows = statement.query(params_from_iter(all))?;
            let mut read = Vec::new();
            let mut keys = HashSet::new();
            // `first_copies` reads no further than it needs: so is this read.
            while let Some(row) = rows.next()? {
                let one = found(row)?;
                keys.insert(one.key.filter(|k| *k != 0).unwrap_or(-one.pk));
                read.push(one);
                if keys.len() as i64 > offset + limit + 1 {
                    break;
                }
            }
            Ok(first_copies(read, limit, offset))
        }
    }
}

/// Discovery's view of the database.
pub struct DiscoveryStore {
    db: Arc<Database>,
    clock: Arc<dyn Clock>,
}

impl fmt::Debug for DiscoveryStore {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("DiscoveryStore(..)")
    }
}

impl DiscoveryStore {
    pub fn new(db: Arc<Database>, clock: Arc<dyn Clock>) -> DiscoveryStore {
        DiscoveryStore { db, clock }
    }
}

impl SearchIndex for DiscoveryStore {
    fn search(
        &self,
        spec: &QuerySpec,
        account: Option<&str>,
        limit: i64,
        offset: i64,
        thread: Option<&str>,
        roles: &dyn FolderRoles,
    ) -> Result<Option<Vec<Found>>, Fault> {
        self.db.read(|c| Ok(search_in(c, spec, account, limit, offset, thread, roles).ok()))
    }
}

/// `MAX(from_name)` and the rest, a message counted once however many folders hold it.
const TALLY: &str = "SELECT COUNT(DISTINCT message_key) n, MAX(ts) last, MIN(ts) first, MAX(from_name) name, \
                     COUNT(DISTINCT CASE WHEN answered=1 THEN message_key END) replied \
                     FROM messages WHERE lower(from_addr)=?";

fn strings(c: &Connection, sql: &str, params: impl rusqlite::Params) -> rusqlite::Result<Vec<String>> {
    c.prepare_cached(sql)?.query_map(params, |r| r.get::<_, Option<String>>(0))?.filter_map(Result::transpose).collect()
}

fn pairs(c: &Connection, sql: &str, params: impl rusqlite::Params) -> rusqlite::Result<Vec<(String, String)>> {
    c.prepare_cached(sql)?.query_map(params, |r| Ok((r.get(0)?, r.get(1)?)))?.collect()
}

fn recipient(r: &Row, counted: bool) -> rusqlite::Result<Recipient> {
    Ok(Recipient {
        addr: r.get("addr")?,
        account: r.get("account")?,
        folder: r.get("folder")?,
        is_junk: truth(r, "is_junk")?,
        sender: r.get("sender")?,
        n: if counted { r.get("n")? } else { 1 },
    })
}

impl ContactMail for DiscoveryStore {
    fn senders_like(&self, needle: &str) -> Result<Vec<Sender>, Fault> {
        self.db.read(|c| {
            let mut sql = "SELECT from_addr AS addr, MAX(from_name) AS name, COUNT(*) AS n, MAX(ts) AS last \
                           FROM messages WHERE from_addr IS NOT NULL AND from_addr != '' "
                .to_owned();
            let mut params: Vec<String> = Vec::new();
            if !needle.is_empty() {
                sql.push_str("AND (lower(from_addr) LIKE ? OR lower(from_name) LIKE ?) ");
                params = vec![format!("%{needle}%"), format!("%{needle}%")];
            }
            sql.push_str("GROUP BY lower(from_addr) ORDER BY n DESC, last DESC LIMIT 8");
            c.prepare_cached(&sql)?
                .query_map(params_from_iter(params), |r| Ok(Sender { addr: r.get(0)?, name: r.get(1)?, n: r.get(2)? }))?
                .collect()
        })
    }

    fn sender_domains_like(&self, needle: &str) -> Result<Vec<(Option<String>, i64)>, Fault> {
        let domain = "lower(substr(from_addr, instr(from_addr, '@') + 1))";
        self.db.read(|c| {
            c.prepare_cached(&format!(
                "SELECT {domain} AS domain, COUNT(*) AS n FROM messages WHERE instr(from_addr, '@') > 0 AND \
                 {domain} LIKE ? GROUP BY domain ORDER BY n DESC, domain LIMIT 3"
            ))?
            .query_map([format!("%{needle}%")], |r| Ok((r.get(0)?, r.get(1)?)))?
            .collect()
        })
    }

    fn card(&self, addr: &str) -> Result<(Tally, Vec<String>, Vec<Recent>), Fault> {
        self.db.read(|c| {
            let tally = c.query_row(TALLY, [addr], |r| {
                Ok(Tally {
                    n: r.get::<_, Option<i64>>("n")?.unwrap_or(0),
                    last: r.get("last")?,
                    first: r.get("first")?,
                    name: r.get("name")?,
                    replied: r.get::<_, Option<i64>>("replied")?.unwrap_or(0),
                })
            })?;
            let accounts = strings(
                c,
                "SELECT account, COUNT(*) k FROM messages WHERE lower(from_addr)=? GROUP BY account ORDER BY k DESC",
                [addr],
            )?;
            let recent = c
                .prepare_cached(
                    "SELECT pk, message_key, uid, folder, account, subject, thread_id, ts, date_iso, seen FROM messages \
                     WHERE lower(from_addr)=? ORDER BY ts DESC LIMIT 6",
                )?
                .query_map([addr], |r| {
                    Ok(Recent {
                        key: r.get("message_key")?,
                        uid: r.get("uid")?,
                        folder: r.get("folder")?,
                        account: r.get("account")?,
                        subject: r.get("subject")?,
                        thread_id: r.get("thread_id")?,
                        date_iso: r.get("date_iso")?,
                        seen: truth(r, "seen")?,
                    })
                })?
                .collect::<Result<Vec<_>, _>>()?;
            Ok((tally, accounts, recent))
        })
    }

    fn known_facts(&self, forms: &[String], min_history: i64) -> Result<Facts, Fault> {
        self.db.read(|c| {
            let mut facts =
                Facts { accounts: strings(c, "SELECT DISTINCT account FROM messages", [])?, ..Facts::default() };
            facts.accounts.retain(|a| !a.is_empty());
            if !forms.is_empty() {
                let ph = placeholders(forms.len());
                facts.senders = pairs(
                    c,
                    &format!(
                        "SELECT DISTINCT lower(from_addr), folder FROM messages WHERE lower(from_addr) IN ({ph}) \
                         AND is_junk=0"
                    ),
                    params_from_iter(forms),
                )?;
                facts.synced = pairs(c, "SELECT account, folder FROM sync_state", [])?;
                facts.draft_folders = strings(c, "SELECT DISTINCT folder FROM drafts WHERE folder IS NOT NULL", [])?
                    .into_iter()
                    .collect();
                facts.copies = c
                    .prepare_cached(&format!(
                        "SELECT r.addr, m.account, m.folder, m.is_junk, lower(m.from_addr) sender FROM \
                         message_recipients r JOIN messages m ON m.pk=r.message_pk WHERE r.addr IN ({ph}) AND \
                         COALESCE(m.draft,0)=0"
                    ))?
                    .query_map(params_from_iter(forms), |r| recipient(r, false))?
                    .collect::<Result<Vec<_>, _>>()?;
                facts.envelopes = c
                    .prepare_cached("SELECT envelope FROM outbox WHERE delivered=1")?
                    .query_map([], |r| r.get(0))?
                    .collect::<Result<Vec<_>, _>>()?;
            }
            let held: i64 = c.query_row(
                "SELECT COUNT(*) FROM (SELECT 1 FROM messages WHERE is_junk=0 LIMIT ?)",
                [min_history],
                |r| r.get(0),
            )?;
            facts.thin = held < min_history;
            Ok(facts)
        })
    }

    fn written_copies(&self, own: &[String], sent: &[String]) -> Result<Vec<Recipient>, Fault> {
        let mut wheres = Vec::new();
        if !own.is_empty() {
            wheres.push(format!("lower(m.from_addr) IN ({})", placeholders(own.len())));
        }
        if !sent.is_empty() {
            wheres.push(format!("m.folder IN ({})", placeholders(sent.len())));
        }
        if wheres.is_empty() {
            return Ok(Vec::new());
        }
        self.db.read(|c| {
            c.prepare_cached(&format!(
                "SELECT r.addr, m.account, m.folder, m.is_junk, lower(m.from_addr) sender, COUNT(*) n \
                 FROM messages m JOIN message_recipients r ON r.message_pk=m.pk \
                 WHERE COALESCE(m.draft,0)=0 AND ({}) GROUP BY 1,2,3,4,5",
                wheres.join(" OR ")
            ))?
            .query_map(params_from_iter(own.iter().chain(sent)), |r| recipient(r, true))?
            .collect()
        })
    }
}

/// The saved views, as the `views` document.
const VIEWS: &str = "views";

impl ViewsStore for DiscoveryStore {
    fn read(&self) -> Result<Value, Fault> {
        self.db.read(|c| blocks::document(c, VIEWS))
    }

    fn change(&self, change: &mut dyn FnMut(Value) -> Option<Value>) -> Result<(), Fault> {
        let now = mm_kernel::when::iso(self.clock.now());
        self.db.write(|c| {
            if let Some(changed) = change(blocks::document(c, VIEWS)?) {
                blocks::write_document(c, VIEWS, &changed, &now)?;
            }
            Ok(())
        })
    }
}

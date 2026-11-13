//! What the page lists (`mailbox.adapters.sqlite.lists`, Attention's lists in `attention.adapters.sqlite`, and the
//! tray's in `command_log`): none holds a row on its way out (`pending_removal`), and a folder's or the Inboxes'
//! none snoozed or muted. Each read is one connection, its rows and its total read together.

use mm_kernel::errors::Fault;
use mm_mailbox::ports::{OpenCommands, Page, Row, StoredCommands, StoredLists};
use rusqlite::types::ValueRef;
use rusqlite::{Connection, Params, ToSql, params, params_from_iter};
use serde_json::Value;

use crate::db::Database;
use crate::placeholders;

/// Each row a statement gives, every column by its name, its value as SQLite held it: an integer as one, a real as a
/// float, text as text. A blob reads as null: no list shows one.
pub(crate) fn rows_of(c: &Connection, sql: &str, values: impl Params) -> rusqlite::Result<Vec<Row>> {
    let mut statement = c.prepare_cached(sql)?;
    let names: Vec<String> = statement.column_names().map(str::to_owned).collect();
    let mut found = statement.query(values)?;
    let mut out = Vec::new();
    while let Some(r) = found.next()? {
        let mut row = Row::new();
        for (i, name) in names.iter().enumerate() {
            row.insert(name.clone(), value_of(r.get_ref(i)?));
        }
        out.push(row);
    }
    Ok(out)
}

pub(crate) fn value_of(v: ValueRef<'_>) -> Value {
    match v {
        ValueRef::Null | ValueRef::Blob(_) => Value::Null,
        ValueRef::Integer(i) => Value::from(i),
        ValueRef::Real(f) => serde_json::Number::from_f64(f).map_or(Value::Null, Value::Number),
        ValueRef::Text(t) => Value::String(String::from_utf8_lossy(t).into_owned()),
    }
}

fn one_number(c: &Connection, sql: &str, values: impl Params) -> rusqlite::Result<i64> {
    c.prepare_cached(sql)?.query_row(values, |r| r.get::<_, Option<i64>>(0)).map(Option::unwrap_or_default)
}

/// Not snoozed: one with no key isn't, where a NOT IN would leave it out of every list (`kernel.snoozed`).
const NOT_SNOOZED: &str =
    "NOT EXISTS (SELECT 1 FROM snoozes z WHERE z.message_key=m.message_key AND z.folder=m.folder)";
const NOT_MUTED: &str = "NOT EXISTS (SELECT 1 FROM muted mu WHERE mu.account=m.account AND mu.thread_id=m.thread_id)";

/// How many messages the conversation row `t` is in holds, as opening it shows them: in every folder of its account,
/// each Message-ID once, a copy with none alone, none on its way out (`kernel.conversation.count_sql`).
fn count_sql(t: &str) -> String {
    format!(
        "(SELECT COUNT(DISTINCT COALESCE(NULLIF(TRIM(tm.message_id), ''), 'pk:' || tm.pk)) FROM messages tm \
         WHERE tm.account={t}.account AND tm.thread_id={t}.thread_id AND tm.pending_removal IS NULL)"
    )
}

/// What a list shows of the rows `picked` picks, on `m`: none on its way out, snoozed or muted.
fn listed(picked: &str) -> String {
    format!("{picked} AND m.pending_removal IS NULL AND {NOT_SNOOZED} AND {NOT_MUTED}")
}

const IN_FOLDER: &str = "m.account=? AND m.folder=?";

fn in_inboxes(accounts: usize) -> String {
    format!("m.folder='INBOX' AND m.account IN ({})", placeholders(accounts))
}

fn message_rows_sql(picked: &str) -> String {
    format!(
        "SELECT m.pk, m.message_key, m.uid, m.account, m.folder, m.from_addr, m.from_name, m.subject, m.date_iso, m.ts, \
         m.snippet, m.seen, m.flagged, m.has_attach, m.is_junk, m.bucket, m.thread_id, s.importance, s.summary, \
         s.action, s.category \
         FROM messages m LEFT JOIN assessments s ON s.message_key=m.message_key \
         WHERE {} ORDER BY m.ts DESC, m.pk DESC LIMIT ? OFFSET ?",
        listed(picked)
    )
}

/// One row a conversation (its latest message) with what the rest of it adds up to, newest first. `partition` is
/// what names a conversation here, and `cnt` how many of it the list holds; `thread_count` is all of it, as opening
/// it shows, asked of this page's rows only.
fn conversation_rows_sql(picked: &str, partition: &str) -> String {
    format!(
        "WITH fm AS (
           SELECT m.*, s.importance, s.summary, s.action, s.category,
             ROW_NUMBER() OVER (PARTITION BY {partition} ORDER BY m.ts DESC, m.pk DESC) rn,
             COUNT(*)        OVER (PARTITION BY {partition}) cnt,
             SUM(CASE WHEN m.seen=0 THEN 1 ELSE 0 END) OVER (PARTITION BY {partition}) unread,
             MAX(m.flagged)  OVER (PARTITION BY {partition}) any_flagged,
             MAX(m.has_attach) OVER (PARTITION BY {partition}) any_attach
           FROM messages m LEFT JOIN assessments s ON s.message_key=m.message_key
           WHERE {}),
         page AS (SELECT * FROM fm WHERE rn=1 ORDER BY ts DESC, pk DESC LIMIT ? OFFSET ?)
         SELECT page.*, {} AS thread_count FROM page ORDER BY ts DESC, pk DESC",
        listed(picked),
        count_sql("page")
    )
}

/// The latest row of each thread `f` names, joined to `f`'s table (Attention's views).
fn latest_of_thread() -> &'static str {
    "m.pk=(SELECT pk FROM messages m2 WHERE m2.account=m.account AND m2.thread_id=m.thread_id \
     ORDER BY m2.ts DESC, m2.pk DESC LIMIT 1)"
}

fn with_page<'a>(named: &'a [&'a dyn ToSql], limit: &'a i64, offset: &'a i64) -> Vec<&'a dyn ToSql> {
    let mut values = named.to_vec();
    values.push(limit);
    values.push(offset);
    values
}

impl StoredLists for Database {
    fn folder_page(&self, account: &str, folder: &str, grouped: bool, limit: i64, offset: i64) -> Result<Page, Fault> {
        self.read(|c| {
            let named: [&dyn ToSql; 2] = [&account, &folder];
            let values = with_page(&named, &limit, &offset);
            if grouped {
                Ok(Page {
                    rows: rows_of(c, &conversation_rows_sql(IN_FOLDER, "m.thread_id"), values.as_slice())?,
                    total: one_number(
                        c,
                        &format!("SELECT COUNT(DISTINCT m.thread_id) FROM messages m WHERE {}", listed(IN_FOLDER)),
                        named.as_slice(),
                    )?,
                })
            } else {
                Ok(Page {
                    rows: rows_of(c, &message_rows_sql(IN_FOLDER), values.as_slice())?,
                    total: one_number(
                        c,
                        &format!("SELECT COUNT(*) FROM messages m WHERE {}", listed(IN_FOLDER)),
                        named.as_slice(),
                    )?,
                })
            }
        })
    }

    fn inboxes_page(&self, accounts: &[String], grouped: bool, limit: i64, offset: i64) -> Result<Page, Fault> {
        let picked = in_inboxes(accounts.len());
        self.read(|c| {
            let named: Vec<&dyn ToSql> = accounts.iter().map(|a| a as &dyn ToSql).collect();
            let values = with_page(&named, &limit, &offset);
            if grouped {
                Ok(Page {
                    rows: rows_of(c, &conversation_rows_sql(&picked, "m.account, m.thread_id"), values.as_slice())?,
                    total: one_number(
                        c,
                        &format!(
                            "SELECT COUNT(*) FROM (SELECT DISTINCT m.account, m.thread_id FROM messages m WHERE {})",
                            listed(&picked)
                        ),
                        named.as_slice(),
                    )?,
                })
            } else {
                Ok(Page {
                    rows: rows_of(c, &message_rows_sql(&picked), values.as_slice())?,
                    total: one_number(
                        c,
                        &format!("SELECT COUNT(*) FROM messages m WHERE {}", listed(&picked)),
                        named.as_slice(),
                    )?,
                })
            }
        })
    }

    fn snoozed_page(&self, accounts: &[String], limit: i64, offset: i64) -> Result<Page, Fault> {
        let among = format!("m.account IN ({})", placeholders(accounts.len()));
        self.read(|c| {
            let named: Vec<&dyn ToSql> = accounts.iter().map(|a| a as &dyn ToSql).collect();
            let values = with_page(&named, &limit, &offset);
            Ok(Page {
                rows: rows_of(
                    c,
                    &format!(
                        "SELECT m.pk, m.message_key, m.uid, m.account, m.folder, m.from_addr, m.from_name, m.subject, \
                         m.date_iso, m.snippet, m.seen, m.flagged, m.has_attach, m.is_junk, m.bucket, m.thread_id, \
                         z.until AS snoozed_until, s.importance, s.category, s.action \
                         FROM snoozes z CROSS JOIN messages m ON m.message_key=z.message_key AND m.folder=z.folder \
                         LEFT JOIN assessments s ON s.message_key=m.message_key \
                         WHERE {among} AND m.pending_removal IS NULL \
                         ORDER BY z.until ASC, m.folder, m.ts, m.pk LIMIT ? OFFSET ?"
                    ),
                    values.as_slice(),
                )?,
                total: one_number(
                    c,
                    &format!(
                        "SELECT COUNT(*) FROM snoozes z CROSS JOIN messages m ON m.message_key=z.message_key \
                         AND m.folder=z.folder WHERE {among} AND m.pending_removal IS NULL"
                    ),
                    named.as_slice(),
                )?,
            })
        })
    }

    fn muted(&self, accounts: &[String]) -> Result<Vec<Row>, Fault> {
        let sql = format!(
            "SELECT m.*, {} AS thread_count \
             FROM messages m JOIN muted mu ON mu.account=m.account AND mu.thread_id=m.thread_id \
             WHERE m.account IN ({}) AND {} ORDER BY m.ts DESC",
            count_sql("m"),
            placeholders(accounts.len()),
            latest_of_thread()
        );
        self.read(|c| rows_of(c, &sql, params_from_iter(accounts.iter())))
    }

    fn waiting(&self, accounts: &[String]) -> Result<Vec<Row>, Fault> {
        let sql = format!(
            "SELECT m.*, f.kind AS f_kind, f.first_sent_ts AS f_first_sent, f.due_ts AS f_due, {} AS thread_count \
             FROM followups f JOIN messages m ON m.account=f.account AND m.thread_id=f.thread_id \
             WHERE f.account IN ({}) AND f.state='due' AND f.kind!='replylater' AND {} ORDER BY f.due_ts DESC",
            count_sql("m"),
            placeholders(accounts.len()),
            latest_of_thread()
        );
        self.read(|c| rows_of(c, &sql, params_from_iter(accounts.iter())))
    }

    fn reply_later(&self, accounts: &[String]) -> Result<Vec<Row>, Fault> {
        let sql = format!(
            "SELECT m.*, f.created_at AS f_created, {} AS thread_count \
             FROM followups f JOIN messages m ON m.account=f.account AND m.thread_id=f.thread_id \
             WHERE f.account IN ({}) AND f.kind='replylater' AND f.state='due' AND {} ORDER BY f.created_at DESC",
            count_sql("m"),
            placeholders(accounts.len()),
            latest_of_thread()
        );
        self.read(|c| rows_of(c, &sql, params_from_iter(accounts.iter())))
    }

    fn thread(&self, account: &str, thread_id: &str) -> Result<Vec<Row>, Fault> {
        self.read(|c| {
            rows_of(
                c,
                "SELECT pk, message_key, uid, folder, from_addr, from_name, subject, date_iso, ts, snippet, \
                 seen, flagged, answered, has_attach, is_junk, message_id \
                 FROM messages WHERE account=? AND thread_id=? AND pending_removal IS NULL \
                 ORDER BY ts ASC, pk ASC",
                params![account, thread_id],
            )
        })
    }

    fn scanned_whole(&self, account: &str, folder: &str) -> Result<bool, Fault> {
        self.read(|c| {
            let state: Option<String> = c
                .prepare_cached("SELECT scan_state FROM sync_state WHERE account=? AND folder=?")?
                .query_map(params![account, folder], |r| r.get(0))?
                .next()
                .transpose()?;
            Ok(state.as_deref() == Some("synced"))
        })
    }
}

impl StoredCommands for Database {
    fn open_commands(&self, limit: i64) -> Result<OpenCommands, Fault> {
        self.read(|c| {
            let rows = rows_of(
                c,
                "SELECT * FROM mail_commands WHERE applied=0 AND state IN ('queued', 'running', 'error') \
                 ORDER BY id DESC LIMIT ?",
                [limit],
            )?;
            let counts = c
                .prepare_cached(
                    "SELECT state, COUNT(*) n FROM mail_commands WHERE applied=0 \
                     AND state IN ('queued', 'running', 'error') GROUP BY state",
                )?
                .query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?
                .collect::<Result<_, _>>()?;
            Ok((rows, counts))
        })
    }

    fn command_subject(
        &self,
        account: &str,
        folder: &str,
        message_id: Option<&str>,
        pk: Option<i64>,
    ) -> Result<Option<Value>, Fault> {
        self.read(|c| {
            let first = |sql: &str, values: &[&dyn ToSql]| -> rusqlite::Result<Option<Value>> {
                Ok(rows_of(c, sql, values)?.into_iter().next().and_then(|mut r| r.remove("subject")))
            };
            match (message_id, pk) {
                (Some(id), _) => {
                    let there = first(
                        "SELECT subject FROM messages WHERE account=? AND folder=? AND message_id=? \
                         ORDER BY pk DESC LIMIT 1",
                        &[&account, &folder, &id],
                    )?;
                    match there {
                        Some(subject) => Ok(Some(subject)),
                        None => first(
                            "SELECT subject FROM messages WHERE account=? AND message_id=? ORDER BY pk DESC LIMIT 1",
                            &[&account, &id],
                        ),
                    }
                }
                (None, Some(pk)) => first("SELECT subject FROM messages WHERE pk=?", &[&pk]),
                (None, None) => Ok(None),
            }
        })
    }
}

//! What the Assistant keeps in the database (`assistant.adapters.sqlite_ledger` and `assistant.adapters.sqlite`): the
//! usage ledger, what each day's calls cost; what the Assistant reads of the mail (its `Mail`); and what it keeps of its
//! answers: thread summaries, replies drafted ahead, and the briefs, deltas and reviews; and how a sender stands.

use mm_assistant::ports::{
    Answers, Awaiting, Cards, ConversationBy, CopyNow, FeatureDay, Glance, Mail, Message, Needing, NewDraft, Overview,
    Said, SenderRecent, SenderStats, Senders, Spend, Stamp, StoredCard, StoredDraft, StoredReview, StoredSummary,
    UsageDays, UsageLedger, WeekRows,
};
use mm_kernel::errors::Fault;
use rusqlite::types::Value as Sql;
use rusqlite::{Connection, OptionalExtension, params_from_iter};

use crate::db::Database;
use crate::discovery::not_snoozed_sql;
use crate::placeholders;

const ADD: &str = "INSERT INTO ai_usage(day, feature, model, served, outcome, calls, input_tokens, output_tokens, \
                   cache_write_tokens, cache_read_tokens, cost_micros, last_ts) \
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) \
                   ON CONFLICT(day, feature, model, served, outcome) DO UPDATE SET \
                   calls = calls + excluded.calls, \
                   input_tokens = input_tokens + excluded.input_tokens, \
                   output_tokens = output_tokens + excluded.output_tokens, \
                   cache_write_tokens = cache_write_tokens + excluded.cache_write_tokens, \
                   cache_read_tokens = cache_read_tokens + excluded.cache_read_tokens, \
                   cost_micros = cost_micros + excluded.cost_micros, \
                   last_ts = MAX(last_ts, excluded.last_ts)";

impl UsageLedger for Database {
    fn record(&self, spend: &Spend) -> Result<(), Fault> {
        let u = spend.usage;
        self.write(|c| {
            c.prepare_cached(ADD)?
                .execute(rusqlite::params![
                    spend.day,
                    spend.feature,
                    spend.model,
                    spend.served,
                    spend.outcome,
                    spend.calls,
                    u.input_tokens,
                    u.output_tokens,
                    u.cache_write_tokens,
                    u.cache_read_tokens,
                    spend.cost_micros,
                    spend.ts
                ])
                .map(drop)
        })
    }

    fn spent(&self, day: &str) -> Result<i64, Fault> {
        self.read(|c| {
            c.prepare_cached("SELECT COALESCE(SUM(cost_micros), 0) FROM ai_usage WHERE day = ?")?
                .query_row([day], |r| r.get(0))
        })
    }

    fn reserved(&self) -> Result<i64, Fault> {
        self.read(|c| {
            c.prepare_cached(
                "SELECT COALESCE(SUM(estimate_micros), 0) FROM ai_batches WHERE state IN ('submitting', 'running')",
            )?
            .query_row([], |r| r.get(0))
        })
    }

    fn in_batches(&self) -> Result<i64, Fault> {
        self.read(|c| c.prepare_cached("SELECT COUNT(*) FROM ai_batch_items")?.query_row([], |r| r.get(0)))
    }
}

impl UsageDays for Database {
    fn days(&self, since: &str) -> Result<Vec<FeatureDay>, Fault> {
        self.read(|c| {
            c.prepare_cached(
                "SELECT day, feature, SUM(calls), SUM(cost_micros) FROM ai_usage WHERE day >= ? \
                 GROUP BY day, feature ORDER BY day DESC, feature",
            )?
            .query_map([since], |r| {
                Ok(FeatureDay { day: r.get(0)?, feature: r.get(1)?, calls: r.get(2)?, cost_micros: r.get(3)? })
            })?
            .collect()
        })
    }
}

// What the Assistant reads of the mail (`mailbox.adapters.sqlite.assistant_reads`), and what it keeps of its answers.

const SAID: &str = "SELECT pk, account, from_name, from_addr, date_iso, subject, snippet, body_text, message_id, \
                    COALESCE(body_fetched, 0) != 0, ts FROM messages";

fn said(r: &rusqlite::Row<'_>) -> rusqlite::Result<Said> {
    Ok(Said {
        pk: r.get(0)?,
        account: r.get(1)?,
        from_name: r.get(2)?,
        from_addr: r.get(3)?,
        date_iso: r.get(4)?,
        subject: r.get(5)?,
        snippet: r.get(6)?,
        body_text: r.get(7)?,
        message_id: r.get(8)?,
        body_fetched: r.get(9)?,
        ts: r.get(10)?,
    })
}

/// A column Python reads for its truth: NULL, 0 and "" are false.
fn truth(column: &str) -> String {
    format!("CASE WHEN {column} IS NULL OR {column} = 0 OR {column} = '' THEN 0 ELSE 1 END")
}

impl Mail for Database {
    fn message(&self, pk: i64) -> Result<Option<Message>, Fault> {
        let sql = format!(
            "SELECT m.pk, m.account, m.thread_id, m.message_id, m.message_key, m.from_addr, m.folder, {}, {}, {} \
             FROM messages m LEFT JOIN snoozes z ON z.message_key = m.message_key AND z.folder = m.folder \
             WHERE m.pk = ?",
            truth("m.is_junk"),
            truth("m.answered"),
            truth("z.until")
        );
        self.read(|c| {
            c.prepare_cached(&sql)?
                .query_row([pk], |r| {
                    Ok(Message {
                        pk: r.get(0)?,
                        account: r.get(1)?,
                        thread_id: r.get(2)?,
                        message_id: r.get(3)?,
                        message_key: r.get(4)?,
                        from_addr: r.get(5)?,
                        folder: r.get(6)?,
                        is_junk: r.get(7)?,
                        answered: r.get(8)?,
                        snoozed: r.get(9)?,
                    })
                })
                .optional()
        })
    }

    fn conversation(&self, account: &str, by: ConversationBy, value: &str) -> Result<Vec<Said>, Fault> {
        let column = match by {
            ConversationBy::Thread => "thread_id",
            ConversationBy::MessageId => "message_id",
            ConversationBy::Pk => "pk",
        };
        let sql = format!("{SAID} WHERE account = ? AND {column} = ? ORDER BY ts ASC, pk ASC");
        self.read(|c| c.prepare_cached(&sql)?.query_map([account, value], said)?.collect())
    }

    fn to_reply_to(&self, pk: i64) -> Result<Vec<Said>, Fault> {
        self.read(|c| c.prepare_cached(&format!("{SAID} WHERE pk = ?"))?.query_map([pk], said)?.collect())
    }

    /// A body that hasn't come down is read from its server, which the store doesn't reach: the program's `Mail` does.
    fn fetch_text(&self, _pk: i64) -> Result<Option<String>, Fault> {
        Err(mm_kernel::errors::NOT_YET)
    }

    fn thread_stamps(&self, account: &str, thread_id: &str) -> Result<Vec<Stamp>, Fault> {
        self.read(|c| {
            c.prepare_cached("SELECT message_id, ts FROM messages WHERE account = ? AND thread_id = ?")?
                .query_map([account, thread_id], |r| Ok((r.get(0)?, r.get(1)?)))?
                .collect()
        })
    }

    fn message_stamp(&self, pk: i64) -> Result<Option<Stamp>, Fault> {
        self.read(|c| {
            c.prepare_cached("SELECT message_id, body_fetched FROM messages WHERE pk = ?")?
                .query_row([pk], |r| Ok((r.get(0)?, r.get(1)?)))
                .optional()
        })
    }

    fn thread_anchor(&self, account: &str, thread_id: &str) -> Result<Option<i64>, Fault> {
        self.read(|c| {
            c.prepare_cached(
                "SELECT pk FROM messages WHERE account = ? AND thread_id = ? ORDER BY ts DESC, pk DESC LIMIT 1",
            )?
            .query_row([account, thread_id], |r| r.get(0))
            .optional()
        })
    }
}

const SAVE_SUMMARY: &str = "INSERT INTO thread_summaries(account, thread_id, scope, fingerprint, tldr, points, action, \
                            participants, message_count, model, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) \
                            ON CONFLICT(account, thread_id, scope) DO UPDATE SET \
                            fingerprint = excluded.fingerprint, tldr = excluded.tldr, points = excluded.points, \
                            action = excluded.action, participants = excluded.participants, \
                            message_count = excluded.message_count, model = excluded.model, \
                            created_at = excluded.created_at";

const PUT_DRAFT: &str = "INSERT INTO draft_ahead(account, ref_key, fp, body, tone, model, source, src_message_id, \
                         created_ts, last_hit_ts, fail_ts, fail_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, 0) \
                         ON CONFLICT(account, ref_key) DO UPDATE SET \
                         fp = excluded.fp, body = excluded.body, tone = excluded.tone, model = excluded.model, \
                         source = excluded.source, src_message_id = excluded.src_message_id, \
                         created_ts = excluded.created_ts, fail_ts = 0, fail_count = 0";

/// Drafting ahead's hygiene (`draft_ahead_gc`): correctness never rests on it, as every serve checks again.
const GC_AGED: &str = "DELETE FROM draft_ahead WHERE MAX(created_ts, fail_ts) < ?";
const GC_ORPHANED_THREADS: &str = "DELETE FROM draft_ahead WHERE created_ts < ? AND ref_key LIKE 't:%' AND NOT EXISTS (\
                                   SELECT 1 FROM messages m WHERE m.account = draft_ahead.account \
                                   AND m.thread_id = substr(draft_ahead.ref_key, 3))";
const GC_ORPHANED_MESSAGES: &str = "DELETE FROM draft_ahead WHERE created_ts < ? AND ref_key LIKE 'm:%' AND NOT EXISTS (\
                                    SELECT 1 FROM messages m WHERE m.account = draft_ahead.account \
                                    AND m.message_id = substr(draft_ahead.ref_key, 3))";
const GC_ENDED_THREADS: &str = "DELETE FROM draft_ahead WHERE body IS NOT NULL AND ref_key LIKE 't:%' AND EXISTS (\
                                SELECT 1 FROM messages m WHERE m.account = draft_ahead.account \
                                AND m.thread_id = substr(draft_ahead.ref_key, 3) \
                                AND (m.answered = 1 OR lower(m.folder) LIKE '%sent%') \
                                AND m.pk = (SELECT m2.pk FROM messages m2 WHERE m2.account = m.account \
                                AND m2.thread_id = m.thread_id ORDER BY m2.ts DESC, m2.pk DESC LIMIT 1))";
const GC_ENDED_MESSAGES: &str = "DELETE FROM draft_ahead WHERE body IS NOT NULL AND ref_key LIKE 'm:%' AND EXISTS (\
                                 SELECT 1 FROM messages m WHERE m.account = draft_ahead.account \
                                 AND m.message_id = substr(draft_ahead.ref_key, 3) \
                                 AND (m.answered = 1 OR m.is_junk = 1))";
const GC_CAP: &str = "DELETE FROM draft_ahead WHERE (account, ref_key) IN (\
                      SELECT account, ref_key FROM draft_ahead \
                      ORDER BY MAX(created_ts, COALESCE(last_hit_ts, 0)) DESC LIMIT -1 OFFSET 500)";

impl Answers for Database {
    fn thread_summary(&self, account: &str, thread_id: &str, scope: &str) -> Result<Option<StoredSummary>, Fault> {
        self.read(|c| {
            c.prepare_cached(
                "SELECT account, thread_id, scope, fingerprint, tldr, points, action, participants, message_count, \
                 model, created_at FROM thread_summaries WHERE account = ? AND thread_id = ? AND scope = ?",
            )?
            .query_row([account, thread_id, scope], |r| {
                Ok(StoredSummary {
                    account: r.get(0)?,
                    thread_id: r.get(1)?,
                    scope: r.get(2)?,
                    fingerprint: r.get(3)?,
                    tldr: r.get(4)?,
                    points: r.get(5)?,
                    action: r.get(6)?,
                    participants: r.get(7)?,
                    message_count: r.get(8)?,
                    model: r.get(9)?,
                    created_at: r.get(10)?,
                })
            })
            .optional()
        })
    }

    fn save_thread_summary(&self, s: &StoredSummary) -> Result<(), Fault> {
        self.write(|c| {
            c.prepare_cached(SAVE_SUMMARY)?
                .execute(rusqlite::params![
                    s.account,
                    s.thread_id,
                    s.scope,
                    s.fingerprint,
                    s.tldr,
                    s.points,
                    s.action,
                    s.participants,
                    s.message_count,
                    s.model,
                    s.created_at
                ])
                .map(drop)
        })
    }

    fn draft_ahead(&self, account: &str, ref_key: &str) -> Result<Option<StoredDraft>, Fault> {
        self.read(|c| {
            c.prepare_cached(
                "SELECT fp, body, tone, model, COALESCE(created_ts, 0), COALESCE(fail_ts, 0), COALESCE(fail_count, 0) \
                 FROM draft_ahead WHERE account = ? AND ref_key = ?",
            )?
            .query_row([account, ref_key], |r| {
                Ok(StoredDraft {
                    fp: r.get(0)?,
                    body: r.get(1)?,
                    tone: r.get(2)?,
                    model: r.get(3)?,
                    created_ts: r.get(4)?,
                    fail_ts: r.get(5)?,
                    fail_count: r.get(6)?,
                })
            })
            .optional()
        })
    }

    fn put_draft_ahead(&self, account: &str, ref_key: &str, d: &NewDraft<'_>, now: i64) -> Result<(), Fault> {
        self.write(|c| {
            c.prepare_cached(PUT_DRAFT)?
                .execute(rusqlite::params![
                    account,
                    ref_key,
                    d.fp,
                    d.body,
                    d.tone,
                    d.model,
                    d.source,
                    d.src_message_id,
                    now
                ])
                .map(drop)
        })
    }

    fn draft_ahead_failed(&self, account: &str, ref_key: &str, now: i64) -> Result<(), Fault> {
        self.write(|c| {
            c.prepare_cached(
                "INSERT INTO draft_ahead(account, ref_key, created_ts, fail_ts, fail_count) VALUES (?, ?, ?, ?, 1) \
                 ON CONFLICT(account, ref_key) DO UPDATE SET fail_ts = ?, fail_count = fail_count + 1",
            )?
            .execute(rusqlite::params![account, ref_key, now, now, now])
            .map(drop)
        })
    }

    fn draft_ahead_touched(&self, account: &str, ref_key: &str, now: i64) -> Result<(), Fault> {
        self.write(|c| {
            c.prepare_cached("UPDATE draft_ahead SET last_hit_ts = ? WHERE account = ? AND ref_key = ?")?
                .execute(rusqlite::params![now, account, ref_key])
                .map(drop)
        })
    }

    fn draft_ahead_gc(&self, now: i64, orphans: bool) -> Result<(), Fault> {
        self.write(|c| {
            c.execute(GC_AGED, [now - 7 * 86_400])?;
            if orphans {
                c.execute(GC_ORPHANED_THREADS, [now - 3600])?;
                c.execute(GC_ORPHANED_MESSAGES, [now - 3600])?;
            }
            c.execute(GC_ENDED_THREADS, [])?;
            c.execute(GC_ENDED_MESSAGES, [])?;
            c.execute(GC_CAP, []).map(drop)
        })
    }

    fn reply_worthy(&self, pk: i64) -> Result<bool, Fault> {
        self.read(|c| {
            c.prepare_cached(
                "SELECT 1 FROM messages m JOIN assessments s ON s.message_key = m.message_key \
                 WHERE m.pk = ? AND (s.importance>=4 OR s.category='action-needed')",
            )?
            .query_row([pk], |_| Ok(()))
            .optional()
            .map(|found| found.is_some())
        })
    }
}

// The briefs, the deltas and the reviews: what each reads of the mail (`mailbox.adapters.sqlite.assistant_reads`,
// with the follow-ups Attention keeps and the unsubscribes Curation logs), and how each is kept.

/// A clause confining a query to `accounts` by `column`, and its parameters: nothing when none are named.
fn among(accounts: &[String], column: &str) -> (String, Vec<Sql>) {
    if accounts.is_empty() {
        return (String::new(), Vec::new());
    }
    let clause = format!(" AND {column} IN ({}) ", placeholders(accounts.len()));
    (clause, accounts.iter().map(|a| Sql::Text(a.clone())).collect())
}

/// Muted threads are left out of every card.
const NOT_MUTED: &str = "NOT EXISTS (SELECT 1 FROM muted mu WHERE mu.account=m.account AND mu.thread_id=m.thread_id)";

fn glance(r: &rusqlite::Row) -> rusqlite::Result<Glance> {
    Ok(Glance {
        pk: r.get("pk")?,
        message_key: r.get("message_key")?,
        folder: r.get("folder")?,
        from_name: r.get("from_name")?,
        from_addr: r.get("from_addr")?,
        subject: r.get("subject")?,
        message_id: r.get("message_id")?,
        summary: r.get("summary")?,
        importance: r.get("importance")?,
        category: r.get("category")?,
        action: r.get("action")?,
    })
}

fn glances(c: &Connection, sql: &str, params: Vec<Sql>) -> rusqlite::Result<Vec<Glance>> {
    c.prepare(sql)?.query_map(params_from_iter(params), glance)?.collect()
}

fn counted(c: &Connection, sql: &str, params: Vec<Sql>) -> rusqlite::Result<i64> {
    c.prepare(sql)?.query_row(params_from_iter(params), |r| r.get(0))
}

fn week_in(
    c: &Connection,
    accounts: &[String],
    since_ts: f64,
    since_iso: &str,
    top: i64,
) -> rusqlite::Result<WeekRows> {
    let since = || Sql::Real(since_ts);
    let with = |first: Sql, more: &[Sql]| [first].into_iter().chain(more.iter().cloned()).collect::<Vec<_>>();
    let (ac, ap) = among(accounts, "account");
    let sent = counted(
        c,
        &format!("SELECT COUNT(*) FROM messages WHERE lower(folder) LIKE '%sent%' AND ts>=?{ac}"),
        with(since(), &ap),
    )?;
    let received =
        counted(c, &format!("SELECT COUNT(*) FROM messages WHERE folder='INBOX' AND ts>=?{ac}"), with(since(), &ap))?;
    let by_bucket = c
        .prepare(&format!(
            "SELECT bucket, COUNT(*) n FROM messages WHERE folder='INBOX' AND ts>=?{ac} GROUP BY bucket"
        ))?
        .query_map(params_from_iter(with(since(), &ap)), |r| Ok((r.get(0)?, r.get(1)?)))?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    let (mac, map) = among(accounts, "m.account");
    let needs_sql = format!(
        "SELECT m.pk, m.message_key, m.uid, m.folder, m.account, m.thread_id, m.from_name, m.from_addr, m.subject, m.ts, \
         s.importance, s.action, s.category \
         FROM messages m JOIN assessments s ON s.message_key=m.message_key \
         WHERE m.folder='INBOX' AND m.is_junk=0 AND {} AND m.answered=0 AND m.pending_removal IS NULL \
         AND (s.importance>=4 OR s.category='action-needed' OR TRIM(COALESCE(s.action,''))!='') AND {NOT_MUTED}{mac} \
         ORDER BY s.importance DESC, m.ts DESC LIMIT ?",
        not_snoozed_sql("m")
    );
    let needs = c
        .prepare(&needs_sql)?
        .query_map(params_from_iter(map.iter().cloned().chain([Sql::Integer(top)])), |r| {
            Ok(Needing {
                message_key: r.get("message_key")?,
                uid: r.get("uid")?,
                folder: r.get("folder")?,
                account: r.get::<_, Option<String>>("account")?.unwrap_or_default(),
                thread_id: r.get("thread_id")?,
                from_name: r.get("from_name")?,
                from_addr: r.get("from_addr")?,
                subject: r.get("subject")?,
                importance: r.get("importance")?,
                action: r.get("action")?,
            })
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    // Attention's: whom the person waits on, the threads they wrote in last with no answer yet.
    let (fac, fap) = among(accounts, "f.account");
    let waiting = c
        .prepare(&format!(
            "SELECT f.account, f.thread_id, m.pk, m.message_key, m.uid, m.folder, f.first_sent_ts, m.subject, m.recipients \
             FROM followups f LEFT JOIN messages m ON m.account=f.account AND m.thread_id=f.thread_id \
             AND m.pk=(SELECT pk FROM messages m2 WHERE m2.account=f.account AND m2.thread_id=f.thread_id \
             ORDER BY m2.ts DESC, m2.pk DESC LIMIT 1) \
             WHERE f.state='due' AND f.kind='sent'{fac} ORDER BY f.first_sent_ts ASC LIMIT ?"
        ))?
        .query_map(params_from_iter(fap.iter().cloned().chain([Sql::Integer(top)])), |r| {
            Ok(Awaiting {
                account: r.get("account")?,
                thread_id: r.get("thread_id")?,
                message_key: r.get("message_key")?,
                uid: r.get("uid")?,
                folder: r.get("folder")?,
                first_sent_ts: r.get("first_sent_ts")?,
                subject: r.get("subject")?,
                recipients: r.get("recipients")?,
            })
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    let reply_later = counted(
        c,
        &format!("SELECT COUNT(*) FROM followups f WHERE f.state='due' AND f.kind='replylater'{fac}"),
        fap.clone(),
    )?;
    // Curation's: the senders unsubscribed from since then, in any of the accounts.
    let (jac, jap) = if accounts.is_empty() {
        (String::new(), Vec::new())
    } else {
        (format!(" AND value IN ({}) ", placeholders(accounts.len())), ap.clone())
    };
    let cleaned = counted(
        c,
        &format!(
            "SELECT COUNT(*) FROM unsub_log WHERE ts>=? AND (accounts IS NULL OR EXISTS \
             (SELECT 1 FROM json_each(unsub_log.accounts) WHERE 1=1{jac}))"
        ),
        with(Sql::Text(since_iso.to_owned()), &jap),
    )
    .ok();
    // What a person filed to Archive, by the command log; not one an Undo took back, nor what Tidy filed.
    let handled = counted(
        c,
        &format!(
            "SELECT COUNT(*) FROM mail_commands WHERE kind='archive' AND applied=1 AND done_at>=? \
             AND json_extract(params, '$.undone') IS NULL \
             AND idempotency_key NOT GLOB '*:tidy-[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'{ac}"
        ),
        with(Sql::Text(since_iso.to_owned()), &ap),
    )?;
    Ok(WeekRows { sent, received, by_bucket, needs, waiting, reply_later, cleaned, handled })
}

impl Overview for Database {
    fn brief_rows(&self, accounts: &[String], n: i64) -> Result<Vec<Glance>, Fault> {
        let (ac, ap) = among(accounts, "m.account");
        let sql = format!(
            "SELECT m.pk, m.message_key, m.uid, m.account, m.folder, m.from_name, m.from_addr, m.subject, m.message_id, \
             s.summary, s.importance, s.category, s.action \
             FROM messages m JOIN assessments s ON s.message_key=m.message_key \
             WHERE m.is_junk=0 AND {} AND m.pending_removal IS NULL AND s.importance>=2 AND {NOT_MUTED}{ac} \
             ORDER BY s.importance DESC, m.ts DESC LIMIT ?",
            not_snoozed_sql("m")
        );
        self.read(|c| glances(c, &sql, ap.into_iter().chain([Sql::Integer(n)]).collect()))
    }

    fn delta_rows(&self, accounts: &[String], since_ts: i64, n: i64) -> Result<Vec<Glance>, Fault> {
        let (ac, ap) = among(accounts, "m.account");
        // A delta is what changed: newsletters and notifications count, so it matches the pill; only spam, the
        // blocked, the snoozed and the muted are left out.
        let sql = format!(
            "SELECT m.pk, NULL AS message_key, NULL AS folder, m.from_name, m.from_addr, m.subject, m.message_id, \
             COALESCE(s.summary,'') AS summary, COALESCE(s.importance,2) AS importance, \
             COALESCE(s.category,'other') AS category, NULL AS action \
             FROM messages m LEFT JOIN assessments s ON s.message_key=m.message_key \
             WHERE (m.bucket IS NULL OR m.bucket NOT IN ('spam/scam','blocked')) \
             AND {} AND m.pending_removal IS NULL AND m.ts > ? AND {NOT_MUTED}{ac} ORDER BY m.ts DESC LIMIT ?",
            not_snoozed_sql("m")
        );
        let params = [Sql::Integer(since_ts)].into_iter().chain(ap).chain([Sql::Integer(n)]).collect();
        self.read(|c| glances(c, &sql, params))
    }

    fn copies_now(&self, copies: &[(i64, Option<String>)]) -> Result<Vec<CopyNow>, Fault> {
        if copies.is_empty() {
            return Ok(Vec::new());
        }
        self.read(|c| {
            let mut now = c.prepare_cached(
                "SELECT folder, uid FROM messages WHERE message_key=? AND pending_removal IS NULL \
                 ORDER BY folder=? DESC, pk DESC LIMIT 1",
            )?;
            copies
                .iter()
                .map(|(key, folder)| {
                    now.query_row(rusqlite::params![key, folder], |r| Ok((r.get(0)?, r.get(1)?))).optional()
                })
                .collect()
        })
    }

    fn week(&self, accounts: &[String], since_ts: f64, since_iso: &str, top: i64) -> Result<WeekRows, Fault> {
        self.read(|c| week_in(c, accounts, since_ts, since_iso, top))
    }
}

fn card(r: &rusqlite::Row) -> rusqlite::Result<StoredCard> {
    Ok(StoredCard { fingerprint: r.get(0)?, text: r.get(1)?, body: r.get(2)?, model: r.get(3)?, created_at: r.get(4)? })
}

/// What is kept of a kind is a week's: each saved forgets those made before then.
fn forget(c: &Connection, table: &str, before: &str) -> rusqlite::Result<()> {
    c.execute(&format!("DELETE FROM {table} WHERE created_at < ?"), [before]).map(drop)
}

impl Cards for Database {
    fn brief(&self, accounts: &str, day: &str) -> Result<Option<StoredCard>, Fault> {
        self.read(|c| {
            c.prepare_cached(
                "SELECT fingerprint, narrative, groups_json, model, created_at FROM briefs WHERE accounts=? AND day=?",
            )?
            .query_row([accounts, day], card)
            .optional()
        })
    }

    fn save_brief(&self, accounts: &str, day: &str, b: &StoredCard, forget_before: &str) -> Result<(), Fault> {
        self.write(|c| {
            c.execute(
                "INSERT OR REPLACE INTO briefs(accounts, day, fingerprint, narrative, groups_json, model, created_at) \
                 VALUES(?,?,?,?,?,?,?)",
                rusqlite::params![accounts, day, b.fingerprint, b.text, b.body, b.model, b.created_at],
            )?;
            forget(c, "briefs", forget_before)
        })
    }

    fn delta(&self, accounts: &str, since_ts: i64, fingerprint: &str) -> Result<Option<StoredCard>, Fault> {
        self.read(|c| {
            c.prepare_cached(
                "SELECT fingerprint, headline, items_json, model, created_at FROM deltas \
                 WHERE accounts=? AND since_ts=? AND fingerprint=?",
            )?
            .query_row(rusqlite::params![accounts, since_ts, fingerprint], card)
            .optional()
        })
    }

    fn save_delta(&self, accounts: &str, since_ts: i64, d: &StoredCard, forget_before: &str) -> Result<(), Fault> {
        self.write(|c| {
            c.execute(
                "INSERT OR REPLACE INTO deltas(accounts, since_ts, fingerprint, headline, items_json, model, created_at) \
                 VALUES(?,?,?,?,?,?,?)",
                rusqlite::params![accounts, since_ts, d.fingerprint, d.text, d.body, d.model, d.created_at],
            )?;
            forget(c, "deltas", forget_before)
        })
    }

    fn review(&self, accounts: &str, day: &str) -> Result<Option<StoredReview>, Fault> {
        self.read(|c| {
            c.prepare_cached(
                "SELECT fingerprint, narrative, next_json, stats_json, needs_json, waiting_json, model, created_at \
                 FROM reviews WHERE accounts=? AND day=?",
            )?
            .query_row([accounts, day], |r| {
                Ok(StoredReview {
                    fingerprint: r.get(0)?,
                    narrative: r.get(1)?,
                    next: r.get(2)?,
                    stats: r.get(3)?,
                    needs: r.get(4)?,
                    waiting: r.get(5)?,
                    model: r.get(6)?,
                    created_at: r.get(7)?,
                })
            })
            .optional()
        })
    }

    fn save_review(&self, accounts: &str, day: &str, v: &StoredReview, forget_before: &str) -> Result<(), Fault> {
        self.write(|c| {
            c.execute(
                "INSERT OR REPLACE INTO reviews(accounts, day, fingerprint, narrative, next_json, stats_json, \
                 needs_json, waiting_json, model, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                rusqlite::params![
                    accounts,
                    day,
                    v.fingerprint,
                    v.narrative,
                    v.next,
                    v.stats,
                    v.needs,
                    v.waiting,
                    v.model,
                    v.created_at
                ],
            )?;
            forget(c, "reviews", forget_before)
        })
    }
}

impl Senders for Database {
    fn sender_stats(&self, addrs: &[String]) -> Result<Vec<SenderStats>, Fault> {
        let sql = format!(
            "SELECT addr, MAX(name) name, COUNT(*) n, SUM(seen=0) unseen, SUM(answered) replied, MIN(ts) first_ts, \
             MAX(ts) last_ts \
             FROM (SELECT lower(from_addr) addr, from_name name, MAX(seen) seen, MAX(COALESCE(answered, 0)) answered, \
                   MAX(ts) ts \
                   FROM messages WHERE lower(from_addr) IN ({}) AND pending_removal IS NULL \
                   GROUP BY lower(from_addr), COALESCE(message_key, -pk)) \
             GROUP BY addr ORDER BY n DESC",
            placeholders(addrs.len())
        );
        self.read(|c| {
            c.prepare(&sql)?
                .query_map(params_from_iter(addrs), |r| {
                    Ok(SenderStats {
                        addr: r.get(0)?,
                        name: r.get(1)?,
                        n: r.get(2)?,
                        unseen: r.get(3)?,
                        replied: r.get(4)?,
                        first_ts: r.get(5)?,
                        last_ts: r.get(6)?,
                    })
                })?
                .collect()
        })
    }

    fn senders_recent(&self, addrs: &[&str], n: i64) -> Result<Vec<Vec<SenderRecent>>, Fault> {
        self.read(|c| {
            let mut recent = c.prepare_cached(
                "SELECT m.account, m.thread_id, m.from_name, m.subject, m.date_iso, m.ts, m.seen, m.answered, \
                 m.snippet, s.summary, s.category \
                 FROM messages m LEFT JOIN assessments s ON s.message_key=m.message_key \
                 WHERE lower(m.from_addr)=? AND m.pending_removal IS NULL \
                 GROUP BY m.message_key ORDER BY m.ts DESC LIMIT ?",
            )?;
            addrs
                .iter()
                .map(|addr| {
                    recent
                        .query_map(rusqlite::params![addr, n], |r| {
                            Ok(SenderRecent {
                                account: r.get(0)?,
                                thread_id: r.get(1)?,
                                from_name: r.get(2)?,
                                subject: r.get(3)?,
                                date_iso: r.get(4)?,
                                snippet: r.get(8)?,
                                summary: r.get(9)?,
                            })
                        })?
                        .collect()
                })
                .collect()
        })
    }
}

//! What the Assistant keeps in the database (`assistant.adapters.sqlite_ledger` and `assistant.adapters.sqlite`): the
//! usage ledger, what each day's calls cost; what the Assistant reads of the mail (its `Mail`); and what it keeps of its
//! answers: thread summaries and replies drafted ahead.

use mm_assistant::ports::{
    Answers, ConversationBy, Mail, Message, NewDraft, Said, Spend, Stamp, StoredDraft, StoredSummary, UsageLedger,
};
use mm_kernel::errors::Fault;
use rusqlite::OptionalExtension;

use crate::db::Database;

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

/// A copy's account, its folder and its uid there.
pub type CopyAt = (String, Option<String>, Option<i64>);

/// Where the copy `pk` is.
pub fn copy_at(db: &Database, pk: i64) -> Result<Option<CopyAt>, Fault> {
    db.read(|c| {
        c.prepare_cached("SELECT account, folder, uid FROM messages WHERE pk = ?")?
            .query_row([pk], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))
            .optional()
    })
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

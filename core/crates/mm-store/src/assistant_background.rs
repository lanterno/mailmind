//! What the Assistant's background work reads and keeps (`assistant.adapters.sqlite`'s `needs_summary`, `batch_open`
//! and `save_summary`, and `mailbox.adapters.sqlite.assistant_reads.draft_candidates`): the mail triage hasn't
//! summarized, what it says kept under each message's key, and the mail a reply may be drafted ahead for.

use mm_assistant::background_ports::{Assessment, DraftCandidates, FinishedBatches, Pending, Triage};
use mm_assistant::ports::Message;
use mm_kernel::errors::Fault;
use rusqlite::{OptionalExtension, params};

use crate::db::Database;
use crate::discovery::not_snoozed_sql;

/// One copy of each message, as the Python app reads it (`SELECT m.*`, grouped by the key), so the copy it picks is
/// the same.
const NEEDS_SUMMARY: &str = "SELECT m.* FROM messages m \
     LEFT JOIN assessments s ON s.message_key = m.message_key \
     WHERE m.is_junk = 0 AND s.message_key IS NULL AND m.pending_removal IS NULL \
       AND m.message_key IS NOT NULL \
       AND COALESCE(m.message_key, 0) NOT IN (SELECT message_key FROM ai_batch_items) \
       AND COALESCE(m.ts, 0) > ? \
     GROUP BY COALESCE(m.message_key, -m.pk) \
     ORDER BY m.ts DESC LIMIT ?";

const BATCH_CUTOFF: &str =
    "SELECT cutoff_ts FROM ai_batches WHERE state IN ('submitting', 'running') ORDER BY id DESC LIMIT 1";

const SAVE_ASSESSMENT: &str = "INSERT INTO assessments (message_key, summary, importance, category, action, model, \
     created_at, served_by, prompt_version, outcome) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) \
     ON CONFLICT(message_key) DO UPDATE SET \
     summary = excluded.summary, importance = excluded.importance, \
     category = excluded.category, action = excluded.action, \
     model = excluded.model, created_at = excluded.created_at, \
     served_by = excluded.served_by, prompt_version = excluded.prompt_version, outcome = excluded.outcome";

impl Triage for Database {
    fn batch_cutoff(&self) -> Result<Option<i64>, Fault> {
        self.read(|c| {
            c.prepare_cached(BATCH_CUTOFF)?
                .query_row([], |r| r.get::<_, Option<i64>>(0))
                .optional()
                .map(Option::flatten)
        })
    }

    fn needs_summary(&self, limit: i64, newer_than: Option<i64>) -> Result<Vec<Pending>, Fault> {
        self.read(|c| {
            c.prepare_cached(NEEDS_SUMMARY)?
                .query_map(params![newer_than.unwrap_or(-1), limit], |r| {
                    Ok(Pending {
                        pk: r.get("pk")?,
                        message_key: r.get("message_key")?,
                        from_name: r.get("from_name")?,
                        from_addr: r.get("from_addr")?,
                        subject: r.get("subject")?,
                        date_iso: r.get("date_iso")?,
                        snippet: r.get("snippet")?,
                        ts: r.get("ts")?,
                    })
                })?
                .collect()
        })
    }

    fn save_summary(&self, pk: i64, said: &Assessment, when: &str) -> Result<(), Fault> {
        self.write(|c| {
            let key: Option<i64> = c
                .prepare_cached("SELECT message_key FROM messages WHERE pk = ?")?
                .query_row([pk], |r| r.get(0))
                .optional()?
                .flatten();
            let Some(key) = key else { return Ok(()) };
            c.prepare_cached(SAVE_ASSESSMENT)?
                .execute(params![
                    key,
                    said.summary,
                    said.importance,
                    said.category,
                    said.action,
                    said.model,
                    when,
                    said.served_by,
                    said.prompt_version,
                    said.outcome.map(|o| o.name()),
                ])
                .map(drop)
        })
    }
}

/// Reply-worthy Inbox mail since then, each thread by its latest message, most important first.
fn draft_candidates_sql() -> String {
    format!(
        "SELECT m.pk, m.account, m.thread_id, m.message_id, m.message_key, m.from_addr, m.folder, \
                CASE WHEN m.is_junk IS NULL OR m.is_junk = 0 OR m.is_junk = '' THEN 0 ELSE 1 END, \
                CASE WHEN m.answered IS NULL OR m.answered = 0 OR m.answered = '' THEN 0 ELSE 1 END \
         FROM messages m \
         JOIN assessments s ON s.message_key = m.message_key \
         WHERE m.folder = 'INBOX' \
           AND m.is_junk = 0 AND m.answered = 0 AND m.pending_removal IS NULL \
           AND {} \
           AND (s.importance >= 4 OR s.category = 'action-needed') \
           AND m.ts >= ? \
           AND (COALESCE(m.thread_id, '') <> '' OR COALESCE(m.message_id, '') <> '') \
           AND (COALESCE(m.thread_id, '') = '' OR m.pk = ( \
                 SELECT m2.pk FROM messages m2 WHERE m2.account = m.account \
                   AND m2.thread_id = m.thread_id ORDER BY m2.ts DESC, m2.pk DESC LIMIT 1)) \
           AND NOT EXISTS (SELECT 1 FROM muted mu WHERE mu.account = m.account AND mu.thread_id = m.thread_id) \
         ORDER BY s.importance DESC, m.ts DESC \
         LIMIT 40",
        not_snoozed_sql("m")
    )
}

impl DraftCandidates for Database {
    fn draft_candidates(&self, since_ts: i64) -> Result<Vec<Message>, Fault> {
        let sql = draft_candidates_sql();
        self.read(|c| {
            c.prepare_cached(&sql)?
                .query_map([since_ts], |r| {
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
                        // Not snoozed, as the query asks.
                        snoozed: false,
                    })
                })?
                .collect()
        })
    }
}

/// `answers.batch_forget_finished`: a batch done before then goes, and its items by the foreign key's cascade.
impl FinishedBatches for Database {
    fn forget_finished(&self, ended_before: i64) -> Result<usize, Fault> {
        self.write(|c| c.execute("DELETE FROM ai_batches WHERE state='done' AND ended_at < ?", [ended_before]))
    }
}

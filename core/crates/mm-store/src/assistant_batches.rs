//! The batches sent to the Claude API (`assistant.adapters.sqlite`'s `batch_*`, migration 36's `ai_batches` and
//! `ai_batch_items`): one out at a time, and the messages each holds by key, so nothing triages those meanwhile. The
//! Python app keeps the same rows, so a batch either sent is brought in by either.

use std::collections::HashSet;

use mm_assistant::background_ports::{Assessment, Batch, BatchBook, Begun, Judge, Outcome};
use mm_assistant::ports::Reply;
use mm_kernel::errors::Fault;
use rusqlite::{Connection, OptionalExtension, params};

use crate::assistant_background::{SAVE_ASSESSMENT, pending};
use crate::db::Database;

/// The states of a batch that is out.
const OUT: &str = "('submitting', 'running')";

const COLUMNS: &str = "id, remote_id, feature, model, state, requests, estimate_micros, cutoff_ts, created_at, \
                       polled_at, ended_at, prompt_version";

fn batch(r: &rusqlite::Row<'_>) -> rusqlite::Result<Batch> {
    Ok(Batch {
        id: r.get(0)?,
        remote_id: r.get(1)?,
        feature: r.get(2)?,
        model: r.get(3)?,
        state: r.get(4)?,
        requests: r.get(5)?,
        estimate_micros: r.get(6)?,
        cutoff_ts: r.get(7)?,
        created_at: r.get(8)?,
        polled_at: r.get(9)?,
        ended_at: r.get(10)?,
        prompt_version: r.get(11)?,
    })
}

/// What the AI said of a message, under its key (`save_assessment`).
fn keep(c: &Connection, key: i64, said: &Assessment, when: &str) -> rusqlite::Result<()> {
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
}

impl BatchBook for Database {
    fn open(&self) -> Result<Option<Batch>, Fault> {
        let sql = format!("SELECT {COLUMNS} FROM ai_batches WHERE state IN {OUT} ORDER BY id DESC LIMIT 1");
        self.read(|c| c.prepare_cached(&sql)?.query_row([], batch).optional())
    }

    fn in_state(&self, state: &str) -> Result<Vec<Batch>, Fault> {
        let sql = format!("SELECT {COLUMNS} FROM ai_batches WHERE state = ? ORDER BY id");
        self.read(|c| c.prepare_cached(&sql)?.query_map([state], batch)?.collect())
    }

    fn begin(&self, begun: &Begun<'_>) -> Result<Option<i64>, Fault> {
        let sql = format!(
            "INSERT INTO ai_batches(feature, model, state, requests, estimate_micros, cutoff_ts, created_at, \
             prompt_version) SELECT ?, ?, 'submitting', ?, ?, ?, ?, ? \
             WHERE NOT EXISTS(SELECT 1 FROM ai_batches WHERE state IN {OUT})"
        );
        self.write(|c| {
            let requests = i64::try_from(begun.keys.len()).unwrap_or(i64::MAX);
            let added = c.prepare_cached(&sql)?.execute(params![
                begun.feature,
                begun.model,
                requests,
                begun.estimate_micros,
                begun.cutoff_ts,
                begun.now,
                begun.prompt_version,
            ])?;
            if added != 1 {
                return Ok(None);
            }
            let id = c.last_insert_rowid();
            let mut hold = c.prepare_cached("INSERT INTO ai_batch_items(message_key, batch_id) VALUES (?, ?)")?;
            for key in begun.keys {
                hold.execute(params![key, id])?;
            }
            Ok(Some(id))
        })
    }

    fn sent(&self, id: i64, remote_id: &str, now: i64) -> Result<(), Fault> {
        self.write(|c| {
            c.prepare_cached("UPDATE ai_batches SET state='running', remote_id=?, polled_at=? WHERE id=?")?
                .execute(params![remote_id, now, id])
                .map(drop)
        })
    }

    fn drop_batch(&self, id: i64) -> Result<(), Fault> {
        // Its items go with it, by the foreign key's cascade.
        self.write(|c| c.prepare_cached("DELETE FROM ai_batches WHERE id=?")?.execute([id]).map(drop))
    }

    fn polled(&self, id: i64, now: i64) -> Result<(), Fault> {
        self.write(|c| c.prepare_cached("UPDATE ai_batches SET polled_at=? WHERE id=?")?.execute([now, id]).map(drop))
    }

    fn collected(
        &self,
        id: i64,
        now: i64,
        when: &str,
        answers: &[(i64, Reply)],
        judge: Judge<'_>,
    ) -> Result<usize, Fault> {
        self.write(|c| {
            let held: HashSet<i64> = c
                .prepare_cached("SELECT message_key FROM ai_batch_items WHERE batch_id=?")?
                .query_map([id], |r| r.get(0))?
                .collect::<rusqlite::Result<_>>()?;
            let mut any_copy = c.prepare_cached("SELECT * FROM messages WHERE message_key=? LIMIT 1")?;
            let mut stored = 0;
            for (key, reply) in answers {
                if !held.contains(key) {
                    continue;
                }
                let copy = any_copy.query_row([key], pending).optional()?;
                let said = judge(copy.as_ref(), reply);
                // Every copy has gone: a message that may come back keeps only an answer that was assessed.
                if copy.is_some() || said.outcome == Some(Outcome::Assessed) {
                    keep(c, *key, &said, when)?;
                }
                stored += 1;
            }
            // A message without an answer is waiting again; the API still holds a copy until it is discarded.
            c.prepare_cached("DELETE FROM ai_batch_items WHERE batch_id=?")?.execute([id])?;
            c.prepare_cached("UPDATE ai_batches SET state='collected', ended_at=? WHERE id=?")?.execute([now, id])?;
            Ok(stored)
        })
    }

    fn discarded(&self, id: i64) -> Result<(), Fault> {
        self.write(|c| c.prepare_cached("UPDATE ai_batches SET state='done' WHERE id=?")?.execute([id]).map(drop))
    }

    fn waiting(&self) -> Result<i64, Fault> {
        self.read(|c| c.prepare_cached("SELECT COUNT(*) FROM ai_batch_items")?.query_row([], |r| r.get(0)))
    }
}

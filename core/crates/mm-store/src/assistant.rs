//! What the Assistant keeps in the database (`assistant.adapters.sqlite_ledger` and `assistant.adapters.sqlite`): the
//! usage ledger, what each day's calls cost.

use mm_assistant::ports::{Spend, UsageLedger};
use mm_kernel::errors::Fault;

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

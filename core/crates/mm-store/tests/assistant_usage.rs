//! What `/api/debug/ai` lists of the usage ledger, on a database the core makes: each day's AI calls by feature, as
//! `sqlite_ledger.days` adds them up.

#![allow(clippy::unwrap_used)]

use std::path::Path;
use std::sync::Arc;

use mm_assistant::ports::{FeatureDay, Spend, Usage, UsageDays, UsageLedger};
use mm_kernel::seams::{Clock, Moment, Outside, Tripped};
use mm_store::{Database, DbKey};

#[derive(Debug)]
struct Frozen;

impl Clock for Frozen {
    fn now(&self) -> Moment {
        Moment::from_micros(1_793_620_800_000_000)
    }
}

#[derive(Debug)]
struct Open;

impl Outside for Open {
    fn open(&self, _path: &Path) -> Result<(), Tripped> {
        Ok(())
    }

    fn reach(&self, _host: &str) -> Result<(), Tripped> {
        Ok(())
    }

    fn run(&self, _program: &str) -> Result<(), Tripped> {
        Ok(())
    }
}

/// The ledger's rows: one feature's under two models and two outcomes on a day add up.
const LEDGER: &str = "
INSERT INTO ai_usage (day, feature, model, served, outcome, calls, input_tokens, cost_micros, last_ts) VALUES
  ('2026-11-03', 'triage', 'claude-opus-5-5', 'claude-opus-5-5', 'ok', 7, 70, 70000, 1),
  ('2026-11-02', 'triage', 'claude-opus-5-5', 'claude-opus-5-5', 'ok', 3, 30, 9000, 1),
  ('2026-11-02', 'triage', 'claude-opus-5-5', '', 'failed', 1, 0, 0, 1),
  ('2026-11-02', 'triage', 'claude-sonnet-5-5', 'claude-sonnet-5-5', 'ok', 2, 20, 1500, 1),
  ('2026-11-02', 'ask', 'claude-opus-5-5', 'claude-opus-5-5', 'ok', 1, 10, 4000, 1),
  ('2026-11-01', 'brief', 'claude-opus-5-5', 'claude-opus-5-5', 'ok', 1, 10, 20000, 1),
  ('2026-10-30', 'thread', 'claude-opus-5-5', 'claude-opus-5-5', 'ok', 5, 50, 15000, 1);
";

fn database(dir: &Path) -> Database {
    let database = Database::new(dir.join("mailmind.db"), DbKey::from_bytes([0x5e; 32]), Arc::new(Open));
    database.ready(&Frozen).unwrap();
    database.write(|c| c.execute_batch(LEDGER)).unwrap();
    database
}

fn day(day: &str, feature: &str, calls: i64, cost_micros: i64) -> FeatureDay {
    FeatureDay { day: day.into(), feature: feature.into(), calls, cost_micros }
}

#[test]
fn each_day_from_the_one_asked_adds_up_its_features_newest_day_first() {
    let dir = tempfile::tempdir().unwrap();
    let database = database(dir.path());
    assert_eq!(
        database.days("2026-11-01").unwrap(),
        vec![
            day("2026-11-03", "triage", 7, 70000),
            day("2026-11-02", "ask", 1, 4000),
            day("2026-11-02", "triage", 6, 10500),
            day("2026-11-01", "brief", 1, 20000),
        ]
    );
    assert_eq!(database.days("2026-11-04").unwrap(), vec![]);
    assert_eq!(database.days("2026-10-30").unwrap().last(), Some(&day("2026-10-30", "thread", 5, 15000)));
}

fn spend(feature: &str, tokens: i64, cost_micros: i64) -> Spend {
    Spend {
        day: "2026-11-04".into(),
        feature: feature.into(),
        model: "gpt-6.1-sol".into(),
        served: "gpt-6.1-sol".into(),
        outcome: "ok",
        usage: Usage {
            input_tokens: tokens,
            output_tokens: tokens,
            cache_write_tokens: tokens,
            cache_read_tokens: tokens,
        },
        cost_micros,
        ts: 2,
        calls: 1,
    }
}

#[test]
fn a_call_added_past_the_most_a_row_holds_is_held_there_a_whole_number_still_read() {
    let dir = tempfile::tempdir().unwrap();
    let database = database(dir.path());
    let most = i64::MAX;
    for (tokens, cost) in [(most, most), (1, 1), (most, most)] {
        database.record(&spend("triage", tokens, cost)).unwrap();
    }
    let row = database
        .read(|c| {
            c.query_row(
                "SELECT calls, input_tokens, output_tokens, cache_write_tokens, cache_read_tokens, cost_micros, \
                 typeof(input_tokens) || typeof(output_tokens) || typeof(cache_write_tokens) \
                 || typeof(cache_read_tokens) || typeof(cost_micros) FROM ai_usage WHERE day = '2026-11-04'",
                [],
                |r| Ok(((0..6).map(|i| r.get(i)).collect::<rusqlite::Result<Vec<i64>>>()?, r.get::<_, String>(6)?)),
            )
        })
        .unwrap();
    assert_eq!(row, (vec![3, most, most, most, most, most], "integer".repeat(5)));
    // The day's spending and its report read it, so the limit still holds.
    assert_eq!(database.spent("2026-11-04"), Ok(most));
    assert_eq!(database.days("2026-11-04").unwrap(), vec![day("2026-11-04", "triage", 3, most)]);
    // Below the most, a sum is the sum.
    database.record(&spend("ask", most - 5, most - 5)).unwrap();
    database.record(&spend("ask", 4, 4)).unwrap();
    assert_eq!(database.days("2026-11-04").unwrap()[0], day("2026-11-04", "ask", 2, most - 1));
    // Two features, and one of them under two models, each near or at the most: the day and each feature are held
    // there too, not refused as SQLite's SUM refuses whole numbers past it.
    let other = Spend { model: "gpt-6.1-luna".into(), ..spend("triage", 1, most) };
    database.record(&other).unwrap();
    assert_eq!(database.spent("2026-11-04"), Ok(most));
    assert_eq!(
        database.days("2026-11-04").unwrap(),
        vec![day("2026-11-04", "ask", 2, most - 1), day("2026-11-04", "triage", 4, most)]
    );
}

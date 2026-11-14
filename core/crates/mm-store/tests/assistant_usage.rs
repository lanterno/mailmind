//! What `/api/debug/ai` lists of the usage ledger, on a database the core makes: each day's AI calls by feature, as
//! `sqlite_ledger.days` adds them up.

#![allow(clippy::unwrap_used)]

use std::path::Path;
use std::sync::Arc;

use mm_assistant::ports::{FeatureDay, UsageDays};
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

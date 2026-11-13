//! What the Assistant reads of the database (`assistant.adapters.sqlite_ledger`): what each day's calls cost.

use mm_assistant::ports::UsageLedger;
use mm_kernel::errors::Fault;

use crate::db::Database;

impl UsageLedger for Database {
    fn spent(&self, day: &str) -> Result<i64, Fault> {
        self.read(|c| {
            c.prepare_cached("SELECT COALESCE(SUM(cost_micros), 0) FROM ai_usage WHERE day = ?")?
                .query_row([day], |r| r.get(0))
        })
    }
}

//! What a run of Tidy reads of the change queue's log (`mailbox.adapters.sqlite.command_log`'s `command_ran_under` and
//! `archives_undone`): whether today's run has filed anything, and what a person took back.

use mm_kernel::errors::Fault;
use mm_mailbox::tidy::{TakenBack, TidyLog};
use rusqlite::OptionalExtension;

use crate::db::Database;

impl TidyLog for Database {
    fn ran_under(&self, mutation_id: &str) -> Result<bool, Fault> {
        self.read(|c| {
            c.prepare_cached("SELECT 1 FROM mail_commands WHERE idempotency_key GLOB ? LIMIT 1")?
                .query_row([format!("*:{mutation_id}")], |_| Ok(()))
                .optional()
                .map(|found| found.is_some())
        })
    }

    fn archives_undone(&self) -> Result<TakenBack, Fault> {
        self.read(|c| {
            c.prepare_cached(
                "SELECT account, message_id FROM mail_commands \
                 WHERE kind = 'archive' AND json_extract(params, '$.undone') IS NOT NULL",
            )?
            .query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?
            .collect()
        })
    }
}

//! What the body backfill reads (`mm_mailbox::backfill_ports::BodiesWanted`): the Python app's `bodies_to_fetch` and
//! `still_without_body` (`mailbox.adapters.sqlite.contents`), the first reaching back only as far as it is asked and
//! saying where each message is.

use mm_kernel::errors::Fault;
use mm_mailbox::backfill_ports::{BodiesWanted, Wanted};
use rusqlite::params;

use crate::db::Database;

/// The pks as the JSON list `json_each` reads.
fn listed(pks: &[i64]) -> String {
    serde_json::Value::from(pks.to_vec()).to_string()
}

impl BodiesWanted for Database {
    fn bodies_to_fetch(&self, account: &str, skip: &[i64], since: i64, limit: usize) -> Result<Vec<Wanted>, Fault> {
        let limit = i64::try_from(limit).unwrap_or(i64::MAX);
        self.read(|c| {
            c.prepare_cached(
                "SELECT pk, folder, uid FROM messages WHERE account=? AND body_text IS NULL AND pending_removal IS NULL \
                 AND ts >= ? AND uid > 0 AND pk NOT IN (SELECT value FROM json_each(?)) ORDER BY ts DESC LIMIT ?",
            )?
            .query_map(params![account, since, listed(skip), limit], |r| {
                Ok(Wanted { pk: r.get(0)?, folder: r.get(1)?, uid: r.get(2)? })
            })?
            .collect()
        })
    }

    fn still_without_body(&self, pks: &[i64]) -> Result<Vec<i64>, Fault> {
        if pks.is_empty() {
            return Ok(Vec::new());
        }
        self.read(|c| {
            c.prepare_cached(
                "SELECT pk FROM messages WHERE body_text IS NULL AND pk IN (SELECT value FROM json_each(?))",
            )?
            .query_map([listed(pks)], |r| r.get(0))?
            .collect()
        })
    }
}

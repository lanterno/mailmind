//! What the Mailbox reads of the database (`mm_mailbox::ports::StoredMail`): the Python app's
//! `mailbox.adapters.sqlite.lists`, and Attention's counts (`attention.adapters.sqlite`) the rail shows beside them.

use mm_kernel::errors::Fault;
use mm_mailbox::ports::{StoredMail, ViewCounts};
use rusqlite::{Connection, params_from_iter};

use crate::db::Database;
use crate::placeholders;

// The lists the page shows and a conversation; the digest.
mod bodies;
mod digest;
pub(crate) mod lists;
// A body kept as opening its message keeps one, which a sync keeps of an invite too.
pub(crate) use bodies::keep_body;

impl StoredMail for Database {
    fn stored_folder_counts(&self, account: &str) -> Result<Vec<(String, Option<i64>, i64)>, Fault> {
        self.read(|c| {
            let mut counts: Vec<(String, Option<i64>, i64)> = c
                .prepare_cached(
                    "SELECT folder, SUM(seen=0) AS unseen, COUNT(*) AS total FROM messages WHERE account=? \
                     AND pending_removal IS NULL GROUP BY folder",
                )?
                .query_map([account], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?
                .collect::<Result<_, _>>()?;
            let synced: Vec<String> = c
                .prepare_cached("SELECT folder FROM sync_state WHERE account=?")?
                .query_map([account], |r| r.get(0))?
                .collect::<Result<_, _>>()?;
            for folder in synced {
                if !counts.iter().any(|(f, _, _)| *f == folder) {
                    counts.push((folder, Some(0), 0));
                }
            }
            Ok(counts)
        })
    }

    fn view_counts(&self, accounts: &[String]) -> Result<ViewCounts, Fault> {
        self.read(|c| {
            let among = |alias: &str| format!("{alias}.account IN ({})", placeholders(accounts.len()));
            let has_mail = |alias: &str| {
                format!(
                    "EXISTS(SELECT 1 FROM messages m WHERE m.account={alias}.account AND m.thread_id={alias}.thread_id \
                     AND m.pending_removal IS NULL)"
                )
            };
            Ok(ViewCounts {
                snoozed: count(
                    c,
                    &format!(
                        "SELECT COUNT(*) FROM snoozes z CROSS JOIN messages m ON m.message_key=z.message_key \
                         AND m.folder=z.folder WHERE {} AND m.pending_removal IS NULL",
                        among("m")
                    ),
                    accounts,
                )?,
                muted: count(
                    c,
                    &format!("SELECT COUNT(*) FROM muted mu WHERE {} AND {}", among("mu"), has_mail("mu")),
                    accounts,
                )?,
                waiting: count(
                    c,
                    &format!(
                        "SELECT COUNT(*) FROM followups f WHERE {} AND f.state='due' AND f.kind!='replylater' AND {}",
                        among("f"),
                        has_mail("f")
                    ),
                    accounts,
                )?,
                replylater: count(
                    c,
                    &format!(
                        "SELECT COUNT(*) FROM followups f WHERE {} AND f.kind='replylater' AND f.state='due' AND {}",
                        among("f"),
                        has_mail("f")
                    ),
                    accounts,
                )?,
            })
        })
    }
}

fn count(c: &Connection, sql: &str, accounts: &[String]) -> rusqlite::Result<i64> {
    c.prepare_cached(sql)?.query_row(params_from_iter(accounts.iter()), |r| r.get(0))
}

//! What the Mailbox reads of each folder's syncs (`mm_mailbox::ports::StoredSyncs`): the health columns of
//! `sync_state` (`mailbox.adapters.sqlite.sync_state.account_health`).

use mm_kernel::errors::Fault;
use mm_mailbox::ports::{StoredSyncs, SyncHealth};

use crate::db::Database;

impl StoredSyncs for Database {
    fn sync_health(&self) -> Result<Vec<SyncHealth>, Fault> {
        self.read(|c| {
            c.prepare_cached(
                "SELECT account, folder, last_ok_ts, last_error, last_error_ts, last_error_kind, \
                 COALESCE(sync_count,0) AS sync_count FROM sync_state ORDER BY account, folder",
            )?
            .query_map([], |r| {
                Ok(SyncHealth {
                    account: r.get(0)?,
                    folder: r.get(1)?,
                    last_ok_ts: r.get(2)?,
                    last_error: r.get(3)?,
                    last_error_ts: r.get(4)?,
                    last_error_kind: r.get(5)?,
                    sync_count: r.get(6)?,
                })
            })?
            .collect()
        })
    }
}

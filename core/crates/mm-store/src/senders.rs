//! What Discovery reads of one sender as a message opens (`discovery.adapters.sqlite`'s `tally`).

use mm_discovery::ports::{Row, StoredTally};
use mm_kernel::errors::Fault;

use crate::db::Database;
use crate::mailbox::lists::rows_of;

const TALLY: &str = "SELECT COUNT(DISTINCT message_key) n, MAX(ts) last, MIN(ts) first, MAX(from_name) name, \
                     COUNT(DISTINCT CASE WHEN answered=1 THEN message_key END) replied \
                     FROM messages WHERE lower(from_addr)=?";

impl StoredTally for Database {
    fn tally(&self, address: &str) -> Result<Row, Fault> {
        self.read(|c| Ok(rows_of(c, TALLY, [address])?.into_iter().next().unwrap_or_default()))
    }
}

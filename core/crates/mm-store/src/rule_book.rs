//! The rule book as the classifier reads it (`curation.adapters.rules_document`): the `rules` document, read only
//! when its version moved since the last read.

use mm_kernel::errors::Fault;
use rusqlite::OptionalExtension;

use crate::db::Database;

impl Database {
    /// The rules document's version, and its body when that isn't `known`; None when there is no rule book.
    pub fn rules_document(&self, known: Option<i64>) -> Result<Option<(i64, Option<String>)>, Fault> {
        self.read(|c| {
            let version: Option<i64> =
                c.query_row("SELECT version FROM documents WHERE name='rules'", [], |r| r.get(0)).optional()?;
            let Some(version) = version else { return Ok(None) };
            if known == Some(version) {
                return Ok(Some((version, None)));
            }
            let body: String = c.query_row("SELECT body FROM documents WHERE name='rules'", [], |r| r.get(0))?;
            Ok(Some((version, Some(body))))
        })
    }
}

//! What Curation reads and writes of the database (`mailmind.curation.adapters`): the rule book, kept as the `rules`
//! document (`rules_document`), stored mail by sender (`sqlite`), how stored mail is classified (the Mailbox's
//! `classified_from_address`, `classified_from_domain` and `set_classification`) and the audit trails
//! (`platform.db.audit`).

use std::fmt;
use std::sync::Arc;

use mm_curation::ports::{AuditTrail, Classified, InboxSender, Reclassified, RuleBook, SenderMail};
use mm_curation::rulebook::Rules;
use mm_kernel::errors::{Failure, Fault};
use mm_kernel::seams::Clock;
use rusqlite::{Connection, Row};
use serde_json::Value;

use crate::blocks;
use crate::db::Database;

/// The rule book's name among the documents.
const RULES: &str = "rules";
const MALFORMED: Fault = Fault::new("MalformedRules");

/// Curation's view of the database.
pub struct CurationStore {
    db: Arc<Database>,
    clock: Arc<dyn Clock>,
}

impl fmt::Debug for CurationStore {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("CurationStore(..)")
    }
}

impl CurationStore {
    pub fn new(db: Arc<Database>, clock: Arc<dyn Clock>) -> CurationStore {
        CurationStore { db, clock }
    }
}

/// The rules a document's text holds, the empty rule book when there is none, or None for text that isn't rules.
fn parsed(text: Option<String>) -> Option<Rules> {
    match text {
        None => Some(Rules::default()),
        Some(text) => Rules::normalized(&serde_json::from_str(&text).ok()?),
    }
}

impl RuleBook for CurationStore {
    fn load(&self) -> Result<Rules, Fault> {
        parsed(self.db.read(|c| blocks::document_text(c, RULES))?).ok_or(MALFORMED)
    }

    fn change(&self, apply: &mut dyn FnMut(&mut Rules) -> Result<(), Failure>) -> Result<(), Failure> {
        let now = mm_kernel::when::iso(self.clock.now());
        let mut failed: Option<Failure> = None;
        let written = blocks::write(&self.db, |c| {
            let Some(mut rules) = parsed(blocks::document_text(c, RULES)?) else {
                failed = Some(MALFORMED.into());
                // Any error ends the block unwritten.
                return Err(rusqlite::Error::InvalidQuery);
            };
            if let Err(failure) = apply(&mut rules) {
                failed = Some(failure);
                return Err(rusqlite::Error::InvalidQuery);
            }
            blocks::write_document(c, RULES, &rules.for_saving(), &now)
        });
        match failed {
            Some(failure) => Err(failure),
            None => written.map_err(Failure::from),
        }
    }
}

const CLASSIFIED: &str = "pk, from_addr, from_name, subject, bucket, reason, list_unsub_method";

fn classified(r: &Row) -> rusqlite::Result<Classified> {
    Ok(Classified {
        pk: r.get("pk")?,
        from_addr: r.get("from_addr")?,
        from_name: r.get("from_name")?,
        subject: r.get("subject")?,
        bucket: r.get("bucket")?,
        reason: r.get("reason")?,
        list_unsub_method: r.get("list_unsub_method")?,
    })
}

fn classified_from(c: &Connection, token: &str) -> rusqlite::Result<Vec<Classified>> {
    if token.contains('@') {
        c.prepare_cached(&format!("SELECT {CLASSIFIED} FROM messages WHERE lower(from_addr)=?"))?
            .query_map([token], classified)?
            .collect()
    } else {
        // The domain and its sub-domains; the classifier decides.
        c.prepare_cached(&format!(
            "SELECT {CLASSIFIED} FROM messages WHERE lower(from_addr) LIKE ? OR lower(from_addr) LIKE ?"
        ))?
        .query_map([format!("%@{token}"), format!("%.{token}")], classified)?
        .collect()
    }
}

impl SenderMail for CurationStore {
    fn inbox_senders(&self, account: &str) -> Result<Vec<InboxSender>, Fault> {
        self.db.read(|c| {
            c.prepare_cached(
                "SELECT lower(from_addr) addr, MAX(from_name) name, COUNT(*) n, MAX(ts) last, MAX(date_iso) iso \
                 FROM messages WHERE account=? AND folder='INBOX' AND from_addr!='' AND is_junk=0 \
                 AND pending_removal IS NULL GROUP BY lower(from_addr) ORDER BY last DESC",
            )?
            .query_map([account], |r| {
                Ok(InboxSender { addr: r.get("addr")?, name: r.get("name")?, n: r.get("n")?, iso: r.get("iso")? })
            })?
            .collect()
        })
    }

    fn classified_from(&self, token: &str) -> Result<Vec<Classified>, Fault> {
        self.db.read(|c| classified_from(c, token))
    }

    fn set_classifications(&self, changes: &[Reclassified]) -> Result<(), Fault> {
        blocks::write(&self.db, |c| {
            let mut update =
                c.prepare_cached("UPDATE messages SET bucket=?, confidence=?, reason=?, is_junk=? WHERE pk=?")?;
            for ch in changes {
                update.execute(rusqlite::params![ch.bucket, ch.confidence, ch.reason, i64::from(ch.is_junk), ch.pk])?;
            }
            Ok(())
        })
    }
}

impl AuditTrail for CurationStore {
    fn newest(&self, n: i64) -> Result<(Vec<Value>, bool), Fault> {
        let events: Vec<Value> = self.db.read(|c| {
            c.prepare_cached("SELECT body FROM audit_events ORDER BY ts DESC, id LIMIT ?")?
                .query_map([n.saturating_add(1)], |r| r.get::<_, String>(0))?
                .map(|body| body.map(|b| serde_json::from_str(&b).unwrap_or(Value::Null)))
                .collect()
        })?;
        let more = events.len() as i64 > n;
        Ok((events.into_iter().take(usize::try_from(n).unwrap_or(0)).collect(), more))
    }
}

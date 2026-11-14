//! The sync's ports bound to their adapters (`bootstrap.mailbox`'s ingest): sessions over mm-imap logged in with
//! what the Directory keeps (`login_wiring`), read-only ones in a read-only process; the accounts' kinds; and
//! Curation's classifier over the rule book. What a sync says happened is heard in `heard`.

use std::sync::{Arc, Mutex};
use std::time::Duration;

use mm_accounts::Directory;
use mm_curation::classifier;
use mm_curation::rules::RuleSets;
use mm_kernel::errors::Fault;
use mm_mailbox::session::{MailServer, MailServers, ReadOnly, ServerError};
use mm_mailbox::sync_ports::{Classifier, Envelope, SyncAccount, SyncAccounts, Verdict};
use mm_store::Database;
use serde_json::Value;

use crate::login_wiring::ImapLogin;

/// Sessions on each account's server (`open_session`), logged in as `login` logs in, which a login its server refused
/// waits out. In a read-only process each opens folders read-only and sends no write.
#[derive(Debug)]
pub(crate) struct ImapServers {
    pub(crate) login: Arc<ImapLogin>,
    pub(crate) read_only: bool,
}

impl MailServers for ImapServers {
    fn open(&self, account: &str, timeout_seconds: u64) -> Result<Box<dyn MailServer>, ServerError> {
        let failed = ServerError::Failed;
        let account = self.login.directory.get(account).map_err(failed)?.ok_or(failed(Fault::new("UnknownAccount")))?;
        let session = self.login.open(&account, Duration::from_secs(timeout_seconds))?;
        let session: Box<dyn MailServer> = Box::new(session);
        Ok(if self.read_only { Box::new(ReadOnly(session)) } else { session })
    }
}

/// What a sync knows of an account, from the Directory.
#[derive(Debug)]
pub(crate) struct DirectoryAccounts(pub(crate) Arc<Directory>);

impl SyncAccounts for DirectoryAccounts {
    fn sync_account(&self, account: &str) -> Result<Option<SyncAccount>, Fault> {
        Ok(self.0.get(account)?.map(|a| SyncAccount { gmail_threads: a.kind == "gmail", kind: a.kind, email: a.email }))
    }
}

/// Curation's classifier over the rule book in the database, its lists read again only when its version moved.
#[derive(Debug)]
pub(crate) struct RuleBookClassifier {
    pub(crate) database: Arc<Database>,
    pub(crate) last: Mutex<Option<(i64, Arc<RuleSets>)>>,
}

const KEYS: [&str; 4] = ["keep_senders", "block_senders", "keep_domains", "block_domains"];

/// The four lists of a rules document, lower-cased (`rulebook.normalized`); none when it doesn't read as rules.
fn rule_sets(body: &str) -> RuleSets {
    let parsed: Value = serde_json::from_str(body).unwrap_or(Value::Null);
    let [keep_senders, block_senders, keep_domains, block_domains] = KEYS.map(|key| {
        parsed
            .get(key)
            .and_then(Value::as_array)
            .map(|list| list.iter().filter_map(Value::as_str).map(str::to_lowercase).collect::<Vec<_>>())
            .unwrap_or_default()
    });
    RuleSets::new(keep_senders, block_senders, keep_domains, block_domains)
}

impl RuleBookClassifier {
    fn sets(&self) -> Result<Arc<RuleSets>, Fault> {
        let poisoned = Fault::new("PoisonedLock");
        let known = self.last.lock().map_err(|_| poisoned)?.as_ref().map(|(v, _)| *v);
        let Some((version, body)) = self.database.rules_document(known)? else {
            return Ok(Arc::new(RuleSets::default()));
        };
        let mut last = self.last.lock().map_err(|_| poisoned)?;
        match (body, last.as_ref()) {
            (None, Some((v, sets))) if *v == version => Ok(sets.clone()),
            (Some(body), _) => {
                let sets = Arc::new(rule_sets(&body));
                *last = Some((version, sets.clone()));
                Ok(sets)
            }
            (None, _) => Ok(Arc::new(RuleSets::default())),
        }
    }
}

impl Classifier for RuleBookClassifier {
    fn classify(&self, messages: &[Envelope<'_>]) -> Result<Vec<Verdict>, Fault> {
        if messages.is_empty() {
            return Ok(Vec::new());
        }
        let sets = self.sets()?;
        Ok(messages
            .iter()
            .map(|m| {
                let said = classifier::classify(m.from_addr, m.subject, m.from_name, m.has_list_unsub, &sets);
                Verdict {
                    bucket: said.bucket.name().to_owned(),
                    confidence: said.confidence.name().to_owned(),
                    reason: said.reason.into_owned(),
                    is_junk: classifier::is_junk(Some(said.bucket.name())),
                }
            })
            .collect())
    }
}

/// Threads a request sets going and answers before they end, counted as work being done so a stop and a test's
/// `settle` wait for them (`serve::Work`): a folder's sync as it opens.
#[derive(Debug)]
pub(crate) struct WorkThreads(pub(crate) Arc<crate::serve::Work>);

impl mm_mailbox::opening_ports::Background for WorkThreads {
    fn spawn(&self, job: Box<dyn FnOnce() + Send>) {
        self.0.spawn(job);
    }
}

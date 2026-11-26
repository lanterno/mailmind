//! Where one context's port is another's use case, or a network crate's connection: the glue `app` binds. A context
//! never names another, so what the Mailbox asks of the accounts is answered here, from the Directory.

use std::sync::{Arc, OnceLock};
use std::time::Duration;

use mm_accounts::Directory;
use mm_kernel::errors::Fault;
use mm_mailbox::digest::NotSyncing;
use mm_mailbox::ingest::SyncError;
use mm_mailbox::opening_ports::InboxSync;
use mm_mailbox::ports::{Accounts, MailSession, MailSessions, SessionError};
use mm_mailbox::session::ServerError;
use mm_mailbox::syncing::{AccountSync, Syncing};
use serde_json::{Value, json};

/// What a session waits for each answer when opened with none named (`pool.KEPT_TIMEOUT`).
const TIMEOUT: Duration = Duration::from_secs(60);

/// The Mailbox's accounts: those the Directory may use.
#[derive(Debug)]
pub(crate) struct KnownAccounts(pub(crate) Arc<Directory>);

impl Accounts for KnownAccounts {
    fn known(&self, account: &str) -> Result<bool, Fault> {
        Ok(self.0.get(account)?.is_some())
    }

    fn trash(&self, account: &str) -> Result<Option<String>, Fault> {
        Ok(self.0.get(account)?.map(|a| a.trash))
    }
}

/// The accounts the Mailbox's lists read over: those the Directory may use; and what the page names each by.
impl mm_mailbox::ports::AccountBook for KnownAccounts {
    fn emails(&self) -> Result<Vec<String>, Fault> {
        Ok(self.0.accounts()?.into_iter().map(|a| a.email).collect())
    }

    fn faces(&self) -> Result<mm_mailbox::ports::Faces, Fault> {
        let faces = self.0.faces()?;
        Ok(mm_mailbox::ports::Faces {
            aliases: faces.aliases,
            webmail: faces.webmail,
            signatures: faces.signatures,
            signature_default: faces.signature_default,
            colours: faces.colours,
        })
    }
}

/// AI's settings: the config file's, and whether the environment gives a key.
#[derive(Debug)]
pub(crate) struct AiConfig {
    pub(crate) file: Arc<dyn mm_accounts::ports::ConfigFile>,
    pub(crate) environment_key: bool,
}

impl mm_assistant::ports::AiSettings for AiConfig {
    fn key_named(&self) -> Result<bool, Fault> {
        Ok(self.environment_key || self.file.read()?.get("anthropic_api_key").is_some_and(mm_accounts::account::truthy))
    }

    fn daily_limit(&self) -> Result<Option<serde_json::Value>, Fault> {
        Ok(self.file.read()?.remove("ai_daily_limit_usd"))
    }
}

/// What Compose, Discovery and the calendar add to a message as it opens: a draft's id, what is said of its sender,
/// and the invite it carries.
#[derive(Debug)]
pub(crate) struct MessageOpening {
    pub(crate) drafts: mm_compose::drafts::Drafts,
    pub(crate) senders: mm_discovery::senders::SenderFacts,
    pub(crate) invites: mm_calendar::invites::Invites,
}

impl mm_mailbox::ports::Opening for MessageOpening {
    fn invite_of(
        &self,
        _pk: i64,
        parts: &[mm_content::mime::Attachment],
        raw: &[u8],
    ) -> Result<(serde_json::Value, serde_json::Value), Fault> {
        Ok(self.invites.of(parts, raw))
    }

    fn draft_info(&self, row: &mm_mailbox::ports::Row) -> Result<mm_mailbox::ports::Row, Fault> {
        self.drafts.info(row)
    }

    fn sender_facts(&self, addr: Option<&str>) -> Result<serde_json::Value, Fault> {
        self.senders.facts(addr)
    }
}

/// Which folder is an account's Drafts, as the Mailbox's folder rail last listed them.
#[derive(Debug)]
pub(crate) struct DraftsFolder(pub(crate) Arc<mm_mailbox::folders::Folders>);

impl mm_compose::ports::DraftFolders for DraftsFolder {
    fn plays_drafts(&self, account: &str, folder: &str) -> Result<bool, Fault> {
        self.0.plays_drafts(account, folder)
    }
}

/// The accounts the Document Hub reads over: those the Directory may use, and each one's alias.
impl mm_discovery::ports::AccountNames for KnownAccounts {
    fn emails(&self) -> Result<Vec<String>, Fault> {
        Ok(self.0.accounts()?.into_iter().map(|a| a.email).collect())
    }

    fn aliases(&self) -> Result<std::collections::BTreeMap<String, Option<String>>, Fault> {
        self.0.alias_map()
    }
}

/// Sessions over mm-imap, logged in with what the Directory keeps for each account as `login` logs in
/// (`open_session`), which a login its server refused waits out.
#[derive(Debug)]
pub(crate) struct ImapSessions {
    pub(crate) login: Arc<crate::login_wiring::ImapLogin>,
}

impl MailSessions for ImapSessions {
    fn open(&self, account: &str) -> Result<Box<dyn MailSession>, SessionError> {
        let failed = SessionError::Failed;
        let account = self.login.directory.get(account).map_err(failed)?.ok_or(failed(Fault::new("UnknownAccount")))?;
        #[cfg(target_os = "macos")]
        if matches!(account.kind.as_str(), "gentlemail") {
            return Ok(Box::new(crate::gentlemail_session::Session::open(
                &account,
                &self.login.directory,
                self.login.outside.clone(),
                TIMEOUT,
            )?));
        }
        Ok(Box::new(self.login.open(&account, TIMEOUT)?))
    }
}

/// How the sync stands now (`Syncing.status` and `Syncing.sync`), for the digest and each account's health. The
/// Mailbox's `Syncing` is assembled after both, since its rounds wake Attention's snoozes, so it is bound here once it
/// exists; until then no sync has run, as at any start.
#[derive(Debug, Default)]
pub(crate) struct LiveSync(OnceLock<Arc<Syncing>>);

impl LiveSync {
    pub(crate) fn bind(&self, syncing: Arc<Syncing>) {
        // Bound once, as the app is assembled; a second binding would be a wiring mistake, and the first stands.
        let _ = self.0.set(syncing);
    }

    /// The account was removed: nothing is kept of how its syncs went (`Syncing::removed`). Before the binding no sync
    /// has run, so there is nothing to forget.
    pub(crate) fn removed(&self, account: &str) {
        if let Some(syncing) = self.0.get() {
            syncing.removed(account);
        }
    }

    /// A connection to the account's server couldn't be made outside its sync (`Syncing::unreached`). Before the
    /// binding nothing has synced, and nothing is said.
    pub(crate) fn unreached(&self, account: &str, error: ServerError) {
        if let Some(syncing) = self.0.get() {
            syncing.unreached(account, SyncError::Server(error));
        }
    }

    /// An account's sync now: "ok", "syncing", "error", or "" when none has run since the start.
    fn health(&self, account: &str) -> &'static str {
        let status = self.0.get().and_then(|syncing| syncing.status().ok());
        status.and_then(|status| status.get(account).map(|a| a.health)).unwrap_or("")
    }
}

/// A first look at an Inbox joins the sync of it, once there is one.
impl InboxSync for LiveSync {
    fn sync_inbox(&self, account: &str) -> bool {
        self.0.get().is_some_and(|syncing| syncing.sync_inbox(account))
    }
}

/// An account's sync as the status says it: its health and what it said, while its Inbox's first scan is still
/// reading older mail how many of the Inbox's messages it has read (`done`) of how many (`total`), and when its last
/// sync failed by trouble that doesn't show yet, that the next tries again (`trying_again`).
fn said(a: &AccountSync) -> Value {
    let mut said = json!({"health": a.health, "msg": a.msg});
    if let Some(reading) = a.reading {
        said["done"] = json!(reading.done);
        said["total"] = json!(reading.total);
    }
    if a.trying_again {
        said["trying_again"] = json!(true);
    }
    said
}

impl mm_mailbox::ports::SyncStatus for LiveSync {
    fn status(&self) -> Value {
        match self.0.get().map(|syncing| syncing.status()) {
            Some(Ok(status)) => Value::Object(status.into_iter().map(|(account, a)| (account, said(&a))).collect()),
            _ => NotSyncing.status(),
        }
    }

    fn sync(&self) -> Value {
        match self.0.get().map(|syncing| syncing.sync()) {
            Some(Ok(sync)) => json!({"running": sync.running, "last": sync.last, "result": sync.result}),
            _ => NotSyncing.sync(),
        }
    }
}

/// The Accounts' record of each account's syncs: the Mailbox's (`bootstrap.accounts._sync_record`), with how each
/// account's sync stands now.
#[derive(Debug)]
pub(crate) struct MailboxSyncs(pub(crate) mm_mailbox::sync_record::SyncRecords, pub(crate) Arc<LiveSync>);

impl mm_accounts::ports::SyncRecord for MailboxSyncs {
    fn sync_record(&self) -> Result<Vec<mm_accounts::ports::Synced>, Fault> {
        Ok(self
            .0
            .sync_record()?
            .into_iter()
            .map(|r| mm_accounts::ports::Synced {
                live: self.1.health(&r.health.account).to_owned(),
                account: r.health.account,
                last_ok_ts: r.health.last_ok_ts,
                last_error_ts: r.health.last_error_ts,
                last_error_kind: r.health.last_error_kind,
                last_error: r.health.last_error,
                sync_count: r.health.sync_count,
            })
            .collect())
    }
}

/// The Mailbox's list of accounts, for its counts: the Directory's (`bootstrap.mailbox`'s `accounts`).
#[derive(Debug)]
pub(crate) struct ListedAccounts(pub(crate) Arc<Directory>);

impl mm_mailbox::ports::AccountList for ListedAccounts {
    fn accounts(&self) -> Result<Vec<mm_mailbox::ports::Listed>, Fault> {
        Ok(self
            .0
            .accounts()?
            .into_iter()
            .map(|a| mm_mailbox::ports::Listed { email: a.email, kind: a.kind, alias: a.alias })
            .collect())
    }
}

/// What the Mailbox's counts ask of the classifier: Curation's stored plans (`bootstrap.mailbox`'s `plan_stored`
/// and `buckets`).
#[derive(Debug)]
pub(crate) struct CurationPlans(pub(crate) mm_curation::sweep_plan::StoredPlans);

impl mm_mailbox::ports::InboxPlans for CurationPlans {
    fn plans_stored(
        &self,
        accounts: &[mm_mailbox::ports::Listed],
    ) -> Result<Vec<serde_json::Map<String, serde_json::Value>>, Fault> {
        let named: Vec<(&str, &str)> = accounts.iter().map(|a| (a.email.as_str(), a.kind.as_str())).collect();
        self.0.plans_stored(&named)
    }

    fn buckets(&self) -> serde_json::Value {
        mm_curation::sweep_plan::buckets()
    }
}

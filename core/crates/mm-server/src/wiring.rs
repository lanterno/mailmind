//! Where one context's port is another's use case, or a network crate's connection: the glue `app` binds. A context
//! never names another, so what the Mailbox asks of the accounts is answered here, from the Directory.

use std::sync::Arc;
use std::time::Duration;

use mm_accounts::directory::Pin;
use mm_accounts::{Directory, tls_pin};
use mm_kernel::errors::Fault;
use mm_kernel::seams::Outside;
use mm_mailbox::ports::{Accounts, MailSession, MailSessions, SessionError};

/// What a session waits for each answer when opened with none named (`pool.KEPT_TIMEOUT`).
const TIMEOUT: Duration = Duration::from_secs(60);

/// The Mailbox's accounts: those the Directory may use.
#[derive(Debug)]
pub(crate) struct KnownAccounts(pub(crate) Arc<Directory>);

impl Accounts for KnownAccounts {
    fn known(&self, account: &str) -> Result<bool, Fault> {
        Ok(self.0.get(account)?.is_some())
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

/// How AI stands, as the Assistant reports it, for the digest.
#[derive(Debug)]
pub(crate) struct AssistantStatus(pub(crate) mm_assistant::status::Status);

impl mm_mailbox::ports::AiStatus for AssistantStatus {
    fn report(&self) -> Result<serde_json::Value, Fault> {
        self.0.report()
    }
}

/// What Compose and Discovery add to a message as it opens: a draft's id, and what is said of its sender. An invite
/// it carries isn't read on the Rust server yet.
#[derive(Debug)]
pub(crate) struct MessageOpening {
    pub(crate) drafts: mm_compose::drafts::Drafts,
    pub(crate) senders: mm_discovery::senders::SenderFacts,
}

impl mm_mailbox::ports::Opening for MessageOpening {
    fn invite_of(
        &self,
        _pk: i64,
        parts: &[mm_content::mime::Attachment],
        raw: &[u8],
    ) -> Result<(serde_json::Value, serde_json::Value), Fault> {
        let invite = parts
            .iter()
            .any(|a| a.content_type.to_lowercase().contains("calendar") || a.filename.to_lowercase().ends_with(".ics"));
        if invite && !raw.is_empty() {
            return Err(mm_mailbox::ports::NOT_YET);
        }
        Ok((serde_json::Value::Null, serde_json::Value::Null))
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

/// Sessions over mm-imap, logged in with what the Directory keeps for each account (`imap_connection.connect`): a
/// loopback server's certificate is checked against its pin before the password is read or sent.
#[derive(Debug)]
pub(crate) struct ImapSessions {
    pub(crate) directory: Arc<Directory>,
    pub(crate) outside: Arc<dyn Outside>,
}

impl MailSessions for ImapSessions {
    fn open(&self, account: &str) -> Result<Box<dyn MailSession>, SessionError> {
        let failed = SessionError::Failed;
        let account = self.directory.get(account).map_err(failed)?.ok_or(failed(Fault::new("UnknownAccount")))?;
        let (host, port) = account.imap_server().ok_or(failed(Fault::new("NoImapServer")))?;
        let loopback = tls_pin::is_loopback(&host);
        let endpoint = mm_imap::Endpoint { host: &host, port, starttls: account.starttls, loopback };
        let server = mm_imap::Server::connect(&*self.outside, endpoint, TIMEOUT)?;
        if loopback {
            let seen = tls_pin::fingerprint(server.certificate());
            let pin = self.directory.tls_pin(&account.email, &tls_pin::endpoint(&host, port), &seen).map_err(failed)?;
            if pin == Pin::Other {
                return Err(SessionError::Unreachable("TLSPinMismatch"));
            }
        }
        let password = self.directory.password(&account).map_err(failed)?;
        Ok(Box::new(server.login(&account.email, &password)?))
    }
}

/// The Accounts' record of each account's syncs: the Mailbox's (`bootstrap.accounts._sync_record`).
#[derive(Debug)]
pub(crate) struct MailboxSyncs(pub(crate) mm_mailbox::sync_record::SyncRecords);

impl mm_accounts::ports::SyncRecord for MailboxSyncs {
    fn sync_record(&self) -> Result<Vec<mm_accounts::ports::Synced>, Fault> {
        Ok(self
            .0
            .sync_record()?
            .into_iter()
            .map(|r| mm_accounts::ports::Synced {
                account: r.health.account,
                last_ok_ts: r.health.last_ok_ts,
                last_error_ts: r.health.last_error_ts,
                last_error_kind: r.health.last_error_kind,
                last_error: r.health.last_error,
                sync_count: r.health.sync_count,
                live: r.live.to_owned(),
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

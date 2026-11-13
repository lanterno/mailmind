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

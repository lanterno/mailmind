//! What Accounts' connections need of the world (`bootstrap.accounts`' `_Probe`): a test login through `mm_imap`, and
//! who hears of an account's change.

use std::sync::Arc;
use std::time::Duration;

use mm_accounts::Account;
use mm_accounts::directory::{Directory, Pin};
use mm_accounts::draft::Draft;
use mm_accounts::ports::{AccountChange, AccountEvents, LoginProbe, LoginTrouble};
use mm_accounts::tls_pin;
use mm_kernel::seams::{Outside, Secret};
use mm_mailbox::ports::MailSession;

/// A login tried at an account's IMAP server, as a session would open it: TLS, the pin of a loopback server's
/// certificate checked before a credential is sent, the login, and a logout.
#[derive(Debug)]
pub(crate) struct ImapProbe {
    pub(crate) directory: Arc<Directory>,
    pub(crate) outside: Arc<dyn Outside>,
}

impl ImapProbe {
    /// The server at `host`:`port` reached for `email`, its certificate pinned for the account saved under that
    /// address, if one is (a draft's address may name none), and logged in with what `password` gives.
    fn login(
        &self,
        email: &str,
        (host, port, starttls): (&str, u16, bool),
        password: impl FnOnce() -> Result<Secret, LoginTrouble>,
        timeout: Duration,
    ) -> Result<(), LoginTrouble> {
        let loopback = tls_pin::is_loopback(host);
        let endpoint = mm_imap::Endpoint { host, port, starttls, loopback };
        let trouble = |e: mm_imap::Error| match e {
            mm_imap::Error::Unreachable(class) | mm_imap::Error::Refused(class) => LoginTrouble(class),
        };
        let server = mm_imap::Server::connect(&*self.outside, endpoint, timeout).map_err(trouble)?;
        if loopback {
            let seen = tls_pin::fingerprint(server.certificate());
            let pinned = self.directory.tls_pin(email, &tls_pin::endpoint(host, port), &seen);
            if pinned.map_err(|f| LoginTrouble(f.class))? == Pin::Other {
                return Err(LoginTrouble("TLSPinMismatch"));
            }
        }
        let mut session = server.login(email, &password()?).map_err(trouble)?;
        session.logout();
        Ok(())
    }
}

impl LoginProbe for ImapProbe {
    fn saved(&self, account: &Account, timeout: Duration) -> Result<(), LoginTrouble> {
        let (host, port) = account.imap_server().ok_or(LoginTrouble("NoImapServer"))?;
        let password = || self.directory.password(account).map_err(|f| LoginTrouble(f.class));
        self.login(&account.email, (&host, port, account.starttls), password, timeout)
    }

    fn draft(&self, draft: &Draft, timeout: Duration) -> Result<(), LoginTrouble> {
        // A host that isn't text is one no lookup finds, as the Python app's `getaddrinfo` says of it.
        let host = draft.host.as_deref().ok_or(LoginTrouble("NameNotResolved"))?;
        let email = draft.email.as_deref().unwrap_or_default();
        let password = || Ok(Secret::new(draft.password.expose().to_owned()));
        self.login(email, (host, draft.port, draft.starttls), password, timeout)
    }
}

/// An account's change, heard by no one yet: the Python app's logins and IDLE hear it, and neither runs here.
#[derive(Debug)]
pub(crate) struct NoOneListens;

impl AccountEvents for NoOneListens {
    fn publish(&self, _change: AccountChange) {}
}

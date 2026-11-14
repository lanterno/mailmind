//! How the Rust server logs in at an account's IMAP server (`bootstrap.mailbox`'s `open_session`, `try_login` and
//! `session_for`): TLS, a loopback server's certificate checked against its pin before the password is read or sent,
//! and the login. Every session it opens, the sync's, the change queue's, reconcile's and a person's reads alike,
//! comes through here, so a login its server refused waits out its hold (`mm_accounts::logins`): nothing is sent, and
//! it fails as the refusal did, so each caller does as it did then.

use std::sync::Arc;
use std::time::Duration;

use mm_accounts::directory::Pin;
use mm_accounts::login_hold;
use mm_accounts::logins::Logins;
use mm_accounts::ports::{AccountChange, AccountEvents, LoginTrouble};
use mm_accounts::{Account, Directory, tls_pin};
use mm_kernel::errors::Fault;
use mm_kernel::seams::{Outside, Secret};
use mm_mailbox::ports::SessionError;
use mm_mailbox::session::ServerError;

/// Why a login didn't go.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Trouble {
    /// The connection's or the server's, by its class (`mm_imap`'s).
    Imap(mm_imap::Error),
    /// The certificate at a loopback endpoint isn't the one pinned for it: left before a credential went.
    PinMismatch,
    /// What the login needed couldn't be had: the account's server, its pin or its secret.
    Fault(Fault),
}

impl From<Trouble> for ServerError {
    fn from(trouble: Trouble) -> ServerError {
        match trouble {
            Trouble::Imap(e) => e.into(),
            Trouble::PinMismatch => ServerError::Unreachable("TLSPinMismatch"),
            Trouble::Fault(fault) => ServerError::Failed(fault),
        }
    }
}

impl From<Trouble> for SessionError {
    fn from(trouble: Trouble) -> SessionError {
        match trouble {
            Trouble::Imap(e) => e.into(),
            Trouble::PinMismatch => SessionError::Unreachable("TLSPinMismatch"),
            Trouble::Fault(fault) => SessionError::Failed(fault),
        }
    }
}

impl From<Trouble> for LoginTrouble {
    fn from(trouble: Trouble) -> LoginTrouble {
        match trouble {
            Trouble::Imap(mm_imap::Error::Unreachable(class) | mm_imap::Error::Refused(class)) => LoginTrouble(class),
            Trouble::PinMismatch => LoginTrouble("TLSPinMismatch"),
            Trouble::Fault(fault) => LoginTrouble(fault.class),
        }
    }
}

/// Logins at the accounts' IMAP servers, with what the Directory keeps for each, held back after a refusal.
#[derive(Debug)]
pub(crate) struct ImapLogin {
    pub(crate) directory: Arc<Directory>,
    pub(crate) outside: Arc<dyn Outside>,
    /// Which logins are held back.
    pub(crate) logins: Arc<Logins>,
    /// Who hears that a server refused a login (CredentialsRejected).
    pub(crate) events: Arc<dyn AccountEvents>,
}

impl ImapLogin {
    /// `open_session`: a session for `account`, logged in. A login its server refused waits out its hold.
    pub(crate) fn open(&self, account: &Account, timeout: Duration) -> Result<mm_imap::Session, Trouble> {
        if self.logins.held(account) {
            return Err(Trouble::Imap(mm_imap::Error::Unreachable(login_hold::REFUSED)));
        }
        self.try_login(account, timeout)
    }

    /// `try_login`: a session for `account` whatever the hold says, for someone who asked. What the server answers
    /// counts as any login's does: a refusal is published (CredentialsRejected), and a login that works ends the hold.
    pub(crate) fn try_login(&self, account: &Account, timeout: Duration) -> Result<mm_imap::Session, Trouble> {
        let (host, port) = account.imap_server().ok_or(Trouble::Fault(Fault::new("NoImapServer")))?;
        let password = || self.directory.password(account).map_err(Trouble::Fault);
        match self.session_for(&account.email, (&host, port, account.starttls), password, timeout) {
            Ok(session) => {
                self.logins.accepted(&account.email);
                Ok(session)
            }
            Err(Trouble::Imap(mm_imap::Error::Unreachable(class))) if login_hold::refuses(class) => {
                let login = self.logins.login_id(account);
                self.events.publish(AccountChange::CredentialsRejected(account.email.clone(), login));
                Err(Trouble::Imap(mm_imap::Error::Unreachable(class)))
            }
            Err(trouble) => Err(trouble),
        }
    }

    /// `session_for`: a session at `host`:`port` for `email`, saved or not, as a draft is tried: nothing is asked of
    /// a hold, or told of how it went. The certificate is pinned for the account saved under that address, if one
    /// is (a draft's may name none), and the password is read only once the server has passed.
    pub(crate) fn session_for(
        &self,
        email: &str,
        (host, port, starttls): (&str, u16, bool),
        password: impl FnOnce() -> Result<Secret, Trouble>,
        timeout: Duration,
    ) -> Result<mm_imap::Session, Trouble> {
        let loopback = tls_pin::is_loopback(host);
        let endpoint = mm_imap::Endpoint { host, port, starttls, loopback };
        let server = mm_imap::Server::connect(&*self.outside, endpoint, timeout).map_err(Trouble::Imap)?;
        if loopback {
            let seen = tls_pin::fingerprint(server.certificate());
            let pinned = self.directory.tls_pin(email, &tls_pin::endpoint(host, port), &seen);
            if pinned.map_err(Trouble::Fault)? == Pin::Other {
                return Err(Trouble::PinMismatch);
            }
        }
        server.login(email, &password()?).map_err(Trouble::Imap)
    }
}

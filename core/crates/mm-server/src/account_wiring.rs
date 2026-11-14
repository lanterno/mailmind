//! What Accounts' connections need of the world (`bootstrap.accounts`' `_Probe`): a test login through `mm_imap`. Who
//! hears of an account's change is in `heard`.

use std::sync::Arc;
use std::time::Duration;

use mm_accounts::Account;
use mm_accounts::draft::Draft;
use mm_accounts::ports::{LoginProbe, LoginTrouble};
use mm_kernel::seams::Secret;
use mm_mailbox::ports::MailSession;

use crate::login_wiring::ImapLogin;

/// A login tried at an account's IMAP server, as a session would open it (`login_wiring`), and a logout.
#[derive(Debug)]
pub(crate) struct ImapProbe {
    pub(crate) login: Arc<ImapLogin>,
}

impl LoginProbe for ImapProbe {
    /// Whatever the hold says, since someone asked, and counted as any login is (`try_login`).
    fn saved(&self, account: &Account, timeout: Duration) -> Result<(), LoginTrouble> {
        let mut session = self.login.try_login(account, timeout)?;
        session.logout();
        Ok(())
    }

    /// Where the draft was typed: no hold is asked or told (`session_for`).
    fn draft(&self, draft: &Draft, timeout: Duration) -> Result<(), LoginTrouble> {
        // A host that isn't text is one no lookup finds, as the Python app's `getaddrinfo` says of it.
        let host = draft.host.as_deref().ok_or(LoginTrouble("NameNotResolved"))?;
        let email = draft.email.as_deref().unwrap_or_default();
        let password = || Ok(Secret::new(draft.password.expose().to_owned()));
        let mut session = self.login.session_for(email, (host, draft.port, draft.starttls), password, timeout)?;
        session.logout();
        Ok(())
    }
}

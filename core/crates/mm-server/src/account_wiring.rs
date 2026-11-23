//! What Accounts' connections need of the world (`bootstrap.accounts`' `_Probe`): a test login through `mm_imap`, and
//! then through `mm_smtp` at the outgoing server; and what this Mac keeps of a removed account's mail, let go
//! (`bootstrap.mailbox.forget_account`). Who hears of an account's change is in `heard`.

use std::sync::Arc;
use std::time::Duration;

use mm_accounts::account;
use mm_accounts::directory::Pin;
use mm_accounts::draft::Draft;
use mm_accounts::ports::{AccountMail, ConfigFile, LoginProbe, LoginTrouble};
use mm_accounts::{Account, tls_pin};
use mm_kernel::errors::Fault;
use mm_kernel::log;
use mm_kernel::seams::Secret;
use mm_mailbox::flights::Flights;
use mm_mailbox::gates::{Gates, Priority, Wait, on_gate};
use mm_mailbox::ports::MailSession;
use mm_store::Database;
use serde_json::Value;

use crate::login_wiring::ImapLogin;

/// A login tried at an account's IMAP server, as a session would open it (`login_wiring`), and a logout; then one at
/// its outgoing server, and a QUIT.
#[derive(Debug)]
pub(crate) struct ServerProbe {
    pub(crate) login: Arc<ImapLogin>,
    /// Where a saved account's outgoing server is: the file as it is when the test runs.
    pub(crate) config: Arc<dyn ConfigFile>,
    /// A read-only process sends nothing, and tries no outgoing server.
    pub(crate) read_only: bool,
}

/// How long letting go of a removed account's mail waits for what holds its gate (opening a body, a move, reconcile)
/// to end: then it goes anyway, as the account is already gone.
const FORGET_WAIT: Duration = Duration::from_secs(30);

/// What this Mac keeps of a removed account's mail, let go on the account's gate, once what holds the gate has ended;
/// and again as each sync that was reading its mail lands, so what one stored after the removal goes too.
#[derive(Debug)]
pub(crate) struct KeptMail {
    pub(crate) database: Arc<Database>,
    pub(crate) gates: Arc<dyn Gates>,
    pub(crate) flights: Arc<Flights>,
}

impl AccountMail for KeptMail {
    fn forget(&self, email: &str) -> Result<usize, Fault> {
        let forget = || AccountMail::forget(&*self.database, email);
        let forgotten = match on_gate(&*self.gates, email, Priority::Interactive, Wait::For(FORGET_WAIT), forget) {
            Ok(forgotten) => forgotten,
            Err(_) => {
                log::warning("accounts", "a removed account's gate stayed held: its mail goes anyway", &[], None);
                AccountMail::forget(&*self.database, email)
            }
        }?;
        let (database, account) = (self.database.clone(), email.to_owned());
        self.flights.after(
            email,
            Box::new(move || {
                if let Err(fault) = AccountMail::forget(&*database, &account) {
                    log::warning("accounts", "what a removed account's sync read couldn't go", &[], Some(fault.class));
                }
            }),
        );
        Ok(forgotten)
    }
}

/// What an SMTP login's trouble is, by the class the Python app's `conn_error.quiet` reads of `smtplib`'s.
fn smtp_trouble(e: mm_smtp::Error) -> LoginTrouble {
    LoginTrouble(match e {
        mm_smtp::Error::Auth(_) => mm_accounts::login_hold::REFUSED,
        mm_smtp::Error::Connect(mm_net::Lost(class)) | mm_smtp::Error::Tls(mm_net::Lost(class)) => class,
        mm_smtp::Error::Greeting(_) => "SMTPConnectError",
        mm_smtp::Error::Disconnected => "SMTPServerDisconnected",
        mm_smtp::Error::Failed => "SMTPNotSupportedError",
        mm_smtp::Error::Unsendable => "Unsendable",
        mm_smtp::Error::Tripped => "Tripped",
        _ => "SMTPException",
    })
}

/// A port as the configuration gives one: a number, or digits as text, as `int()` reads them.
fn port_of(value: &Value) -> Option<u16> {
    match value {
        Value::Number(n) => n.as_u64().and_then(|p| u16::try_from(p).ok()),
        Value::String(s) => mm_kernel::text::strip(s).parse().ok(),
        _ => None,
    }
}

impl ServerProbe {
    /// A login at `host`:`port` for `email` and a QUIT, as the outbox's would go (`compose_wiring::SmtpTransport`): a
    /// loopback server's certificate is checked against the pin kept for the address, if one is, before the password
    /// is read or sent.
    fn smtp_login(
        &self,
        email: &str,
        (host, port, starttls): (&str, u16, bool),
        password: impl FnOnce() -> Result<Secret, LoginTrouble>,
        timeout: Duration,
    ) -> Result<(), LoginTrouble> {
        let loopback = tls_pin::is_loopback(host);
        let endpoint = mm_smtp::Endpoint { host, port, starttls, loopback };
        let server = mm_smtp::Server::connect(&*self.login.outside, endpoint, timeout).map_err(smtp_trouble)?;
        if loopback {
            let seen = tls_pin::fingerprint(server.certificate());
            let pinned = self.login.directory.tls_pin(email, &tls_pin::endpoint(host, port), &seen);
            if pinned.map_err(|fault| LoginTrouble(fault.class))? == Pin::Other {
                return Err(LoginTrouble("TLSPinMismatch"));
            }
        }
        server.login(email, &password()?).map_err(smtp_trouble)?.quit();
        Ok(())
    }
}

impl LoginProbe for ServerProbe {
    /// Whatever the hold says, since someone asked, and counted as any login is (`try_login`).
    fn saved(&self, account: &Account, timeout: Duration) -> Result<(), LoginTrouble> {
        #[cfg(target_os = "macos")]
        if account.kind == "hodhod" {
            let secret = self.login.directory.password(account).map_err(|fault| LoginTrouble(fault.class))?;
            return crate::hosted_mail::probe(&secret, &*self.login.outside, &account.email, timeout)
                .map_err(|trouble| LoginTrouble(trouble.class()));
        }
        let mut session = self.login.try_login(account, timeout)?;
        session.logout();
        Ok(())
    }

    /// Where the draft was typed: no hold is asked or told (`session_for`).
    fn draft(&self, draft: &Draft, timeout: Duration) -> Result<(), LoginTrouble> {
        if draft.hosted {
            #[cfg(not(target_os = "macos"))]
            return Err(LoginTrouble("HodHodRequiresMacOS"));
            #[cfg(target_os = "macos")]
            return crate::hosted_mail::probe(
                &draft.password,
                &*self.login.outside,
                draft.email.as_deref().unwrap_or_default(),
                timeout,
            )
            .map_err(|trouble| LoginTrouble(trouble.class()));
        }
        // A host that isn't text is one no lookup finds, as the Python app's `getaddrinfo` says of it.
        let host = draft.host.as_deref().ok_or(LoginTrouble("NameNotResolved"))?;
        let email = draft.email.as_deref().unwrap_or_default();
        let password = || Ok(Secret::new(draft.password.expose().to_owned()));
        let mut session = self.login.session_for(email, (host, draft.port, draft.starttls), password, timeout)?;
        session.logout();
        Ok(())
    }

    /// The outgoing server the file names for the account, or its type gives it (`account::smtp_endpoint`).
    fn saved_outgoing(&self, account: &Account, timeout: Duration) -> Result<(), LoginTrouble> {
        if self.read_only || account.kind == "hodhod" {
            return Ok(());
        }
        let mut raw = self.config.read().map_err(|fault| LoginTrouble(fault.class))?;
        let entry = account::find(&mut raw, &account.email).ok_or(LoginTrouble("NoSmtpServer"))?;
        let endpoint = account::smtp_endpoint(entry);
        let host = endpoint.host.as_str().map(str::to_owned).ok_or(LoginTrouble("NameNotResolved"))?;
        let port = port_of(&endpoint.port).ok_or(LoginTrouble(mm_accounts::draft::BAD_SMTP_PORT))?;
        let starttls = account::truthy(&endpoint.starttls);
        let password = || self.login.directory.password(account).map_err(|fault| LoginTrouble(fault.class));
        self.smtp_login(&account.email, (&host, port, starttls), password, timeout)
    }

    fn draft_outgoing(&self, draft: &Draft, timeout: Duration) -> Result<(), LoginTrouble> {
        if self.read_only || draft.hosted {
            return Ok(());
        }
        let host = draft.smtp_host.as_deref().ok_or(LoginTrouble("NameNotResolved"))?;
        let email = draft.email.as_deref().unwrap_or_default();
        let password = || Ok(Secret::new(draft.password.expose().to_owned()));
        self.smtp_login(email, (host, draft.smtp_port, draft.smtp_starttls), password, timeout)
    }
}

//! IDLE's workers bound to their adapters (`bootstrap.mailbox`'s `realtime`, and what `wiring.wire` subscribes it to):
//! the accounts from the Directory, sessions that log in through `login_wiring` like every other, so a refused login
//! is held back and a held one isn't tried, and their news answered with a sync of those accounts.
//!
//! - IDLE's syncs go through the sync's single flight (`Syncing::sync_guarded`), as the five-minute round does, and take
//!   no gate, as neither does in the Python app: a sync asked for while one runs makes that one go round again.
//! - An account added, changed or removed brings the workers in line (`realtime.reconcile`), after its logins have
//!   heard of it, so a worker started for a new password finds its login no longer held.
//! - `mailmind-server` starts them in `Workers::start`, unless the process only reads, as the Python app does: the
//!   read-only profile runs no background sync, and IDLE's news is one. A stop interrupts each worker's IDLE, and
//!   each logs out. `mm-served` starts none.

use std::collections::BTreeSet;
use std::sync::{Arc, OnceLock, Weak};
use std::thread::JoinHandle;
use std::time::Duration;

use mm_accounts::ports::{AccountChange, AccountEvents};
use mm_accounts::{Account, Directory};
use mm_kernel::errors::Fault;
use mm_mailbox::idle_supervisor::Interrupt;
use mm_mailbox::realtime::{Pace, Realtime, RealtimeParts};
use mm_mailbox::realtime_ports::{IdleAccounts, IdleSession, IdleSessions, InboxNews, Reached};
use mm_mailbox::session::{Selected, ServerError};
use mm_mailbox::syncing::Syncing;
use mm_mailbox::workers::{Kick, Stop};
use serde_json::json;

use crate::app::App;
use crate::login_wiring::ImapLogin;

/// How long an IDLE session waits for each answer outside IDLE (`open_session`'s timeout).
const TIMEOUT: Duration = Duration::from_secs(60);

/// How an account is reached: everything its login depends on (`realtime._ENDPOINT`). The Python app's
/// `imap_adapter` has no counterpart here: the Rust server has one IMAP adapter.
fn reached(account: Account) -> Reached {
    let way =
        json!([account.kind, account.host, account.port, account.starttls, account.credential, account.strip_spaces]);
    Reached { email: account.email, way }
}

/// The accounts IDLE watches, from the Directory: enabled ones, in the configuration's order.
#[derive(Debug)]
pub(crate) struct DirectoryIdle(pub(crate) Arc<Directory>);

impl IdleAccounts for DirectoryIdle {
    fn accounts(&self) -> Result<Vec<Reached>, Fault> {
        Ok(self.0.accounts()?.into_iter().map(reached).collect())
    }

    fn get(&self, email: &str) -> Result<Option<Reached>, Fault> {
        Ok(self.0.get(email)?.map(reached))
    }
}

/// IDLE's sessions, each logged in as `login` logs in. In a read-only process each opens its folder read-only.
#[derive(Debug)]
pub(crate) struct ImapIdle {
    pub(crate) login: Arc<ImapLogin>,
    pub(crate) read_only: bool,
}

impl IdleSessions for ImapIdle {
    fn open(&self, email: &str) -> Result<Box<dyn IdleSession>, ServerError> {
        let failed = ServerError::Failed;
        let account = self.login.directory.get(email).map_err(failed)?.ok_or(failed(Fault::new("UnknownAccount")))?;
        let session = self.login.open(&account, TIMEOUT)?;
        Ok(if self.read_only { Box::new(Examined(session)) } else { Box::new(session) })
    }
}

/// A read-only process's IDLE session: its folder opened read-only, so a wait on it changes nothing.
#[derive(Debug)]
struct Examined(mm_imap::Session);

impl IdleSession for Examined {
    fn capabilities(&self) -> Vec<String> {
        IdleSession::capabilities(&self.0)
    }

    fn select(&mut self, folder: &str, _readonly: bool) -> Result<Selected, ServerError> {
        IdleSession::select(&mut self.0, folder, true)
    }

    fn idle(&mut self, wait: Duration) -> Result<bool, ServerError> {
        IdleSession::idle(&mut self.0, wait)
    }

    fn interrupter(&self) -> Result<Arc<dyn Interrupt>, ServerError> {
        IdleSession::interrupter(&self.0)
    }

    fn logout(&mut self) {
        IdleSession::logout(&mut self.0);
    }
}

/// IDLE's news, heard (`wiring._after_idle`): a sync of those accounts, through the sync's single flight.
#[derive(Debug)]
pub(crate) struct SyncOnNews(pub(crate) Arc<Syncing>);

impl InboxNews for SyncOnNews {
    fn inbox_changed(&self, accounts: BTreeSet<String>) {
        self.0.sync_guarded(Some(accounts));
    }
}

/// What happened to an account, heard first by whoever heard it before IDLE did (its logins), and then by IDLE's
/// workers when it was added, changed or removed (`realtime.reconcile`). A refused login is the logins' alone.
#[derive(Debug)]
pub(crate) struct IdleHeard {
    first: Arc<dyn AccountEvents>,
    /// The workers, once the app has them: weak, since their sessions' logins tell this of a refusal.
    realtime: OnceLock<Weak<Realtime>>,
}

impl IdleHeard {
    pub(crate) fn over(first: Arc<dyn AccountEvents>) -> Arc<IdleHeard> {
        Arc::new(IdleHeard { first, realtime: OnceLock::new() })
    }
}

impl AccountEvents for IdleHeard {
    fn publish(&self, change: AccountChange) {
        let reconciles =
            matches!(change, AccountChange::Added(_) | AccountChange::Changed(_) | AccountChange::Removed(_));
        self.first.publish(change);
        if reconciles && let Some(realtime) = self.realtime.get().and_then(Weak::upgrade) {
            realtime.reconcile();
        }
    }
}

/// IDLE's workers over the Directory and `login`, their news syncing those accounts, and heard by `heard` from now on.
pub(crate) fn realtime(
    directory: &Arc<Directory>,
    login: &Arc<ImapLogin>,
    syncing: &Arc<Syncing>,
    read_only: bool,
    heard: &IdleHeard,
) -> Arc<Realtime> {
    let realtime = Arc::new(Realtime::new(RealtimeParts {
        accounts: Arc::new(DirectoryIdle(directory.clone())),
        sessions: Arc::new(ImapIdle { login: login.clone(), read_only }),
        news: Arc::new(SyncOnNews(syncing.clone())),
    }));
    let _ = heard.realtime.set(Arc::downgrade(&realtime));
    realtime
}

/// `Workers::start`'s: IDLE's workers started on a thread that then waits for the workers' stop, which stops them and
/// waits for each to log out.
pub(crate) fn watch(app: &Arc<App>, stop: &Arc<Stop>) -> Option<JoinHandle<()>> {
    let (realtime, stop) = (app.realtime.clone(), stop.clone());
    let stopped = Arc::new(Kick::default());
    stop.wakes(stopped.clone());
    let watching = move || {
        realtime.start(Pace::default());
        while !stop.is_set() {
            stopped.wait(Duration::from_secs(3600));
        }
        realtime.stop();
        realtime.join();
    };
    std::thread::Builder::new().name("idle".to_owned()).spawn(watching).ok()
}

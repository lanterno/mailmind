//! The folder rail (`mailmind.mailbox.application.folders`).
//!
//! The live LIST and each folder's STATUS take a round trip each, and the rail loads on every account switch and
//! after every triage action, so that part is kept for each account for a few seconds; the counts from the database
//! stay fresh. When a refresh fails, the last list kept serves, however old; with none kept, a server that can't be
//! reached gives the folders the database holds mail in.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use mm_kernel::errors::{Failure, Fault, Problem};
use mm_kernel::log;
use mm_kernel::seams::Clock;

use crate::ports::{Accounts, MailSession, MailSessions, SessionError, StoredMail, ViewCounts};
use crate::roles::{self, RailFolder};

const TAG: &str = "folders";
const POISONED: Fault = Fault::new("PoisonedLock");

/// One account's rail, as `GET /api/folders?account=` answers it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct AccountFolders {
    pub account: String,
    /// Each folder, and whether a person may file mail into it.
    pub folders: Vec<(RailFolder, bool)>,
    pub counts: ViewCounts,
}

#[derive(Clone, Debug)]
struct Listed {
    folders: Vec<RailFolder>,
    at: f64,
}

#[derive(Debug)]
pub struct Folders {
    accounts: Arc<dyn Accounts>,
    sessions: Arc<dyn MailSessions>,
    stored: Arc<dyn StoredMail>,
    clock: Arc<dyn Clock>,
    listed: Mutex<HashMap<String, Listed>>,
    /// One refresh of an account's list at a time, so a cold burst does one live fetch. It stands in for the IMAP
    /// gate (`platform.workers.gates`), which comes with the jobs that share it.
    refreshing: Mutex<HashMap<String, Arc<Mutex<()>>>>,
}

impl Folders {
    /// How stale the folders' unread and total badges may be, in seconds.
    pub const LIST_TTL: f64 = 25.0;

    pub fn new(
        accounts: Arc<dyn Accounts>,
        sessions: Arc<dyn MailSessions>,
        stored: Arc<dyn StoredMail>,
        clock: Arc<dyn Clock>,
    ) -> Folders {
        Folders {
            accounts,
            sessions,
            stored,
            clock,
            listed: Mutex::new(HashMap::new()),
            refreshing: Mutex::new(HashMap::new()),
        }
    }

    /// The folders with their roles and counts for one account, and the views that aren't folders counted.
    pub fn of_account(&self, account: &str) -> Result<AccountFolders, Failure> {
        if !self.accounts.known(account)? {
            return Err(Problem::refused("unknown account").into());
        }
        let folders =
            self.kept_or_live(account)?.into_iter().map(|f| (roles::takes_filing(f.role), f)).map(|(t, f)| (f, t));
        Ok(AccountFolders {
            account: account.to_owned(),
            folders: folders.collect(),
            counts: self.stored.view_counts(&[account.into()])?,
        })
    }

    /// Drop what is kept of one account's list, or of every account's, so the next load asks the server: after a
    /// sync stored new mail, so the badges don't lag.
    pub fn invalidate(&self, account: Option<&str>) -> Result<(), Fault> {
        let mut listed = self.listed.lock().map_err(|_| POISONED)?;
        match account {
            Some(account) => {
                listed.remove(account);
            }
            None => listed.clear(),
        }
        Ok(())
    }

    /// What is kept of the account's list, and whether it is fresh enough to serve.
    fn kept(&self, account: &str) -> Result<Option<(Listed, bool)>, Fault> {
        let hit = self.listed.lock().map_err(|_| POISONED)?.get(account).cloned();
        let now = self.clock.now().seconds();
        Ok(hit.map(|h| {
            let fresh = now - h.at < Folders::LIST_TTL;
            (h, fresh)
        }))
    }

    fn kept_or_live(&self, account: &str) -> Result<Vec<RailFolder>, Failure> {
        let hit = match self.kept(account)? {
            Some((hit, true)) => return Ok(hit.folders),
            Some((hit, false)) => Some(hit),
            None => None,
        };
        match self.refresh(account) {
            Ok(folders) => Ok(folders),
            Err(error) if hit.is_some() => {
                log::warning(TAG, "a live refresh failed, so the list kept serves", &[], Some(error.class()));
                Ok(hit.map(|h| h.folders).unwrap_or_default())
            }
            Err(SessionError::Unreachable(class)) => {
                log::warning(TAG, "unreachable, so the stored folders serve", &[], Some(class));
                Ok(roles::stored_rail(&self.stored.stored_folder_counts(account)?))
            }
            Err(SessionError::Failed(fault)) => Err(fault.into()),
        }
    }

    fn refresh(&self, account: &str) -> Result<Vec<RailFolder>, SessionError> {
        let failed = SessionError::Failed;
        let gate = self.refreshing.lock().map_err(|_| failed(POISONED))?.entry(account.to_owned()).or_default().clone();
        let _held = gate.lock().map_err(|_| failed(POISONED))?;
        // Another caller may have refreshed it while this one waited.
        if let Some((hit, true)) = self.kept(account).map_err(failed)? {
            return Ok(hit.folders);
        }
        let folders = self.live(account)?;
        let at = self.clock.now().seconds();
        self.listed
            .lock()
            .map_err(|_| failed(POISONED))?
            .insert(account.to_owned(), Listed { folders: folders.clone(), at });
        Ok(folders)
    }

    /// The live LIST, with each selectable folder's STATUS (0 and 0 where it fails), over a session of its own,
    /// logged out after.
    fn live(&self, account: &str) -> Result<Vec<RailFolder>, SessionError> {
        let mut session = self.sessions.open(account)?;
        let rail = session.folders().map(|folders| roles::live_rail(&folders, |name| status(&mut *session, name)));
        session.logout();
        rail
    }
}

fn status(session: &mut dyn MailSession, folder: &str) -> (i64, i64) {
    session.status(folder).unwrap_or((0, 0))
}

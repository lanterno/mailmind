//! The folder rail (`mailmind.mailbox.application.folders`).
//!
//! The live LIST and each folder's STATUS take a round trip each, and the rail loads on every account switch and
//! after every triage action, so that part is kept for each account for a few seconds; the counts from the database
//! stay fresh. When a refresh fails, the last list kept serves, however old; with none kept, a server that can't be
//! reached gives the folders the database holds mail in.
//!
//! A folder another client deleted or renamed is forgotten (`gone`) once MailMind is sure the server no longer has it:
//! a LIST that worked doesn't name it, or a SELECT said so (`session::NO_SUCH_FOLDER`). Its mail leaves the lists, the
//! syncs stop asking for it, and a change queued for it is given back. A server that can't be reached, or a refusal
//! that says neither, never forgets a folder, and the Inbox is never forgotten. Nor is one while MailMind renames a
//! folder of that account (`changing`): the server has the new name before the rows go by it, and a LIST or a SELECT
//! in between would take the old one for gone, and its rows' state with it.

use std::collections::{BTreeSet, HashMap};
use std::fmt;
use std::sync::{Arc, Mutex, OnceLock};

use mm_kernel::errors::{Failure, Fault, Problem};
use mm_kernel::log;
use mm_kernel::seams::Clock;

use crate::folder_change;
use crate::ports::{Accounts, MailSession, MailSessions, SessionError, StoredMail, ViewCounts};
use crate::roles::{self, RailFolder};

const TAG: &str = "folders";
const POISONED: Fault = Fault::new("PoisonedLock");

/// What forgets a folder the server no longer has (`gone_folders::GoneFolders`): its stored mail and its sync state,
/// with each change queued for it given back. How many stored copies went.
pub trait ForgetsFolders: Send + Sync + fmt::Debug {
    fn forget(&self, account: &str, folder: &str) -> Result<usize, Fault>;
}

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

/// The changes MailMind has made to one account's folders: how many it has begun, and how many are not done yet.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
struct Changes {
    begun: u64,
    open: usize,
}

/// A change MailMind is making to an account's folders, until it drops (`Folders::changing`).
#[derive(Debug)]
#[must_use = "the change is done as soon as this drops"]
pub struct Changing<'a> {
    folders: &'a Folders,
    account: String,
}

impl Drop for Changing<'_> {
    fn drop(&mut self) {
        if let Ok(mut changes) = self.folders.changes.lock()
            && let Some(changes) = changes.get_mut(&self.account)
        {
            changes.open = changes.open.saturating_sub(1);
        }
    }
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
    /// When the last sync of each (account, folder) as it opened started, in the clock's seconds: what spaces its
    /// catch-ups (`_caught_up`), and what MailMind putting mail there takes away (`touched`).
    caught_up: Mutex<HashMap<(String, String), f64>>,
    /// What forgets a folder found gone; until it is bound, nothing is forgotten.
    forgets: OnceLock<Arc<dyn ForgetsFolders>>,
    /// The changes MailMind makes to each account's folders (`changing`).
    changes: Mutex<HashMap<String, Changes>>,
    /// The part each folder played in the last LIST of each account that worked, by name: kept after the list itself
    /// goes (`invalidate`), so a folder's SPECIAL-USE attribute still says what it is between loads of the rail.
    listed_roles: Mutex<HashMap<String, HashMap<String, &'static str>>>,
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
            caught_up: Mutex::new(HashMap::new()),
            forgets: OnceLock::new(),
            changes: Mutex::new(HashMap::new()),
            listed_roles: Mutex::new(HashMap::new()),
        }
    }

    /// Forget each folder found gone with `forgets`, bound once the store and the events it needs are: a later one
    /// changes nothing.
    pub fn forgetting_with(&self, forgets: Arc<dyn ForgetsFolders>) {
        _ = self.forgets.set(forgets);
    }

    /// The server no longer has `folder`: another client deleted or renamed it. Forget it, so its mail leaves the
    /// lists, no sync asks for it again and a change queued for it is given back, and let the rail kept for the
    /// account go. How many stored copies went; never the Inbox, which a server always has, nor any folder while
    /// MailMind renames one of the account's (`changing`).
    pub fn gone(&self, account: &str, folder: &str) -> Result<usize, Fault> {
        if folder.eq_ignore_ascii_case("INBOX") || self.changes_of(account)?.open > 0 {
            return Ok(0);
        }
        let Some(forgets) = self.forgets.get() else { return Ok(0) };
        let forgotten = forgets.forget(account, folder)?;
        self.caught_up.lock().map_err(|_| POISONED)?.remove(&(account.to_owned(), folder.to_owned()));
        self.invalidate(Some(account))?;
        Ok(forgotten)
    }

    /// MailMind is renaming a folder of `account`'s, on its server and then in the store, until what this answers
    /// drops: meanwhile no folder of the account is forgotten, and a refresh of its rail that overlapped it forgets
    /// none either. The server has the new name before the rows go by it, so a LIST or a SELECT in between would take
    /// the old name for a folder another client deleted, and forget its rows, their snoozes and marks with them, where
    /// the rename would have kept them.
    pub fn changing(&self, account: &str) -> Result<Changing<'_>, Fault> {
        let mut changes = self.changes.lock().map_err(|_| POISONED)?;
        let changes = changes.entry(account.to_owned()).or_default();
        changes.begun += 1;
        changes.open += 1;
        Ok(Changing { folders: self, account: account.to_owned() })
    }

    fn changes_of(&self, account: &str) -> Result<Changes, Fault> {
        Ok(self.changes.lock().map_err(|_| POISONED)?.get(account).copied().unwrap_or_default())
    }

    /// MailMind put mail in `folder` (a draft saved there, a message moved there): its next open catches it up at
    /// once.
    pub fn touched(&self, account: &str, folder: &str) -> Result<(), Fault> {
        self.caught_up.lock().map_err(|_| POISONED)?.remove(&(account.to_owned(), folder.to_owned()));
        Ok(())
    }

    /// A sync of `folder` as it opened starts now.
    pub fn catching_up(&self, account: &str, folder: &str) -> Result<(), Fault> {
        let now = self.clock.now().seconds();
        self.caught_up.lock().map_err(|_| POISONED)?.insert((account.to_owned(), folder.to_owned()), now);
        Ok(())
    }

    /// How many seconds ago the last sync of `folder` as it opened started, None when none has since MailMind
    /// started, or put mail there.
    pub fn since_caught_up(&self, account: &str, folder: &str) -> Result<Option<f64>, Fault> {
        let now = self.clock.now().seconds();
        let caught_up = self.caught_up.lock().map_err(|_| POISONED)?;
        Ok(caught_up.get(&(account.to_owned(), folder.to_owned())).map(|at| now - at))
    }

    /// The folders with their roles and counts for one account, and the views that aren't folders counted. The list
    /// is kept a few seconds; what a folder a sync has read holds is counted fresh, as its list counts it.
    pub fn of_account(&self, account: &str) -> Result<AccountFolders, Failure> {
        if !self.accounts.known(account)? {
            return Err(Problem::refused("unknown account").into());
        }
        let rail = roles::counted(self.kept_or_live(account)?, &self.stored.synced_folder_counts(account)?);
        let folders = rail.into_iter().map(|f| (roles::takes_filing(f.role), f)).map(|(t, f)| (f, t));
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

    /// Whether `folder` is this account's Drafts: what the last LIST said, its SPECIAL-USE attribute first, else
    /// what its name says on its own. Part of a name never counts: "Contract drafts" holds whatever was filed there.
    pub fn plays_drafts(&self, account: &str, folder: &str) -> Result<bool, Fault> {
        Ok(match self.listed.lock().map_err(|_| POISONED)?.get(account) {
            Some(hit) => hit.folders.iter().any(|f| f.name == folder && f.role == "drafts"),
            None => roles::names_drafts(folder),
        })
    }

    /// The part `folder` plays on this account: what the last LIST that worked said of it, its SPECIAL-USE attribute
    /// first, however long ago; else what its whole name says (`role_by_name`). Sent Mail counted for the week goes by
    /// it, so a Sent folder of any name counts, and a folder whose name merely holds "sent" doesn't.
    pub fn role_as_listed(&self, account: &str, folder: &str) -> Result<&'static str, Fault> {
        let listed = self.listed_roles.lock().map_err(|_| POISONED)?;
        Ok(listed
            .get(account)
            .and_then(|roles| roles.get(folder).copied())
            .unwrap_or_else(|| roles::role_by_name(folder, None)))
    }

    /// Refuse a move a person asked for into anything but a folder of this account's own that takes filing. What
    /// the last LIST said is enough, however old, then what is stored; only a folder neither knows (made since, on
    /// another client) waits for a LIST. A folder gone by then is the server's to refuse.
    pub fn ensure_takes_filing(&self, account: &str, folder: &str) -> Result<(), Failure> {
        let known = match self.kept(account)? {
            Some((hit, _)) => hit.folders,
            None => roles::stored_rail(&self.stored.stored_folder_counts(account)?),
        };
        let mut found = known.into_iter().find(|f| f.name == folder);
        if found.is_none() {
            if !self.accounts.known(account)? {
                return Err(Problem::refused("unknown account").into());
            }
            found = self.kept_or_live(account)?.into_iter().find(|f| f.name == folder);
        }
        match found {
            None => Err(Problem::refused("no such folder on this account").into()),
            Some(f) if !roles::takes_filing(f.role) => {
                Err(Problem::refused("mail can't be moved into that folder").into())
            }
            Some(_) => Ok(()),
        }
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
        // What is stored before the LIST asks: a folder a sync stores into afterwards is none of this one's.
        let changes = self.changes_of(account).map_err(failed)?;
        let stored = self.stored.stored_folders(account).map_err(failed)?;
        let (folders, listed) = self.live(account)?;
        // A rename of MailMind's own that overlapped the LIST may have left the old name out of it, stored still or
        // as it was stored: nothing is forgotten, and the list isn't kept, so the next load asks again.
        if changes.open > 0 || self.changes_of(account).map_err(failed)? != changes {
            return Ok(folders);
        }
        for folder in stored.iter().filter(|name| !listed.contains(name.as_str())) {
            if let Err(fault) = self.gone(account, folder) {
                log::warning(TAG, "a folder the server no longer has wasn't forgotten", &[], Some(fault.class));
            }
        }
        let at = self.clock.now().seconds();
        let named = folders.iter().map(|f| (f.name.clone(), f.role)).collect();
        self.listed_roles.lock().map_err(|_| failed(POISONED))?.insert(account.to_owned(), named);
        self.listed
            .lock()
            .map_err(|_| failed(POISONED))?
            .insert(account.to_owned(), Listed { folders: folders.clone(), at });
        Ok(folders)
    }

    /// The live LIST, with each selectable folder's STATUS (0 and 0 where it fails) and whether a person may change
    /// it, over a session of its own, logged out after; and the name of every folder it listed, selectable or not.
    fn live(&self, account: &str) -> Result<(Vec<RailFolder>, BTreeSet<String>), SessionError> {
        let trash = self.accounts.trash(account).map_err(SessionError::Failed)?;
        let mut session = self.sessions.open(account)?;
        let rail = session.folders().map(|folders| {
            let changeable = folder_change::changeable_names(&folders, trash.as_deref());
            let mut rail = roles::live_rail(&folders, |name| status(&mut *session, name));
            for folder in &mut rail {
                folder.changeable = changeable.contains(&folder.name);
            }
            (rail, folders.into_iter().map(|f| f.name).collect())
        });

        session.logout();
        rail
    }
}

fn status(session: &mut dyn MailSession, folder: &str) -> (i64, i64) {
    session.status(folder).unwrap_or((0, 0))
}

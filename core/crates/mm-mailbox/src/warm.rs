//! Warming folders beyond the Inbox (`mailbox.application.warm`): off unless asked for. With MAILMIND_WARM_FOLDERS
//! unset and no account asking, nothing runs, and search and threading stay the Inbox's.
//!
//! An account warmed has its Drafts, Archive (or All Mail) and Sent synced after each sync round's Inbox, inside the
//! round, so it never delays new mail: Drafts every round, the archive every second and Sent, the heavy one, every
//! third. It is a folder's own sync (`WarmSync`, `Ingest`'s), so it keeps every rule a sync keeps; a first touch reads
//! only the newest `WARM_RECENT` messages, and the folders pause half a second apart. Gmail's All Mail holds the Inbox
//! too, so a Gmail account's archive isn't warmed.
//!
//! Which folders play each part is found once for each account, over one connection, and kept: folder names are
//! stable. An account that can't be reached, or whose connection is lost while they are listed, is asked again on the
//! next round; a server that refuses the LIST has none found, which is kept.
//!
//! MAILMIND_WARM_FOLDERS, when set, decides for the run: on (or `all`) for every account, Proton Bridge's too, off for
//! none, or a comma-separated list of addresses. Unset, an account's `"warm_folders": true` in the file asks for it.

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use mm_kernel::errors::Fault;
use mm_kernel::log::{self, Field};

use crate::session::{MailServers, ServerError, find_role};
use crate::warm_ports::{WarmAccount, WarmAccounts, WarmSync};

const TAG: &str = "warm";
/// A first touch of a folder reads no more than its newest this many messages.
pub const WARM_RECENT: usize = 1500;
/// The pause between two folders a round warms.
pub const PAUSE: Duration = Duration::from_millis(500);
/// What a session waits for each answer (`open_session`'s default).
const TIMEOUT_SECONDS: u64 = 60;
/// What to warm, the folder roles that may play it in order, and every how many rounds.
const ROLES: [(&str, &[&str], u64); 3] =
    [("drafts", &["drafts"], 1), ("archive", &["all", "archive"], 2), ("sent", &["sent"], 3)];

const POISONED: Fault = Fault::new("PoisonedLock");

/// The accounts MAILMIND_WARM_FOLDERS turns warming on for, when it is set: every one, or those listed, by address
/// as written. Neither is none.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Which {
    pub every: bool,
    pub listed: BTreeSet<String>,
}

impl Which {
    /// The variable's value as `environment.Accounts` reads it: on or `all` for every account, off for none, else the
    /// entries of a comma-separated list that are addresses. None when it is unset or blank.
    pub fn read(value: Option<&str>) -> Option<Which> {
        let given = value.map(str::trim).filter(|v| !v.is_empty())?;
        let lower = given.to_lowercase();
        if ["1", "true", "yes", "on", "all"].contains(&lower.as_str()) {
            return Some(Which { every: true, listed: BTreeSet::new() });
        }
        if ["0", "false", "no", "off"].contains(&lower.as_str()) {
            return Some(Which::default());
        }
        let listed = given.split(',').map(str::trim).filter(|entry| entry.contains('@')).map(str::to_owned).collect();
        Some(Which { every: false, listed })
    }
}

/// Each part's folder, None where none plays it.
type Roles = BTreeMap<&'static str, Option<String>>;

#[derive(Debug)]
pub struct Warming {
    accounts: Arc<dyn WarmAccounts>,
    servers: Arc<dyn MailServers>,
    sync: Arc<dyn WarmSync>,
    /// MAILMIND_WARM_FOLDERS, when set.
    variable: Option<Which>,
    pause: Duration,
    /// Each account's parts' folders, once found.
    resolved: Mutex<HashMap<String, Roles>>,
    /// The rounds that warmed anything so far, which say which parts are due.
    tick: Mutex<u64>,
}

/// What wires Warming.
#[derive(Debug)]
pub struct WarmingParts {
    pub accounts: Arc<dyn WarmAccounts>,
    pub servers: Arc<dyn MailServers>,
    pub sync: Arc<dyn WarmSync>,
    pub variable: Option<Which>,
    /// Between two folders: `PAUSE`.
    pub pause: Duration,
}

impl Warming {
    pub fn new(parts: WarmingParts) -> Warming {
        let WarmingParts { accounts, servers, sync, variable, pause } = parts;
        Warming { accounts, servers, sync, variable, pause, resolved: Mutex::new(HashMap::new()), tick: Mutex::new(0) }
    }

    /// Whether the account is warmed: as the variable says when it is set, else as its entry in the file asks.
    fn enabled(&self, account: &WarmAccount) -> bool {
        match &self.variable {
            Some(which) => which.every || which.listed.contains(&account.email),
            None => account.asks,
        }
    }

    /// The account's Drafts, Archive and Sent, found over one connection and kept; none, and not kept, when it
    /// couldn't be reached or the connection went while they were listed.
    fn roles_for(&self, email: &str) -> Result<Roles, Fault> {
        if let Some(roles) = self.resolved.lock().map_err(|_| POISONED)?.get(email) {
            return Ok(roles.clone());
        }
        let mut server = match self.servers.open(email, TIMEOUT_SECONDS) {
            Ok(server) => server,
            Err(error) => {
                log::warning(TAG, "an account's folders couldn't be listed", &[], Some(error.class()));
                return Ok(Roles::new());
            }
        };
        let mut found = Roles::new();
        let mut lost = None;
        for (role, hints, _) in ROLES {
            let folder = match find_role(&mut *server, hints) {
                Ok(folder) => folder,
                Err(ServerError::Unreachable(class)) => {
                    lost = Some(class);
                    break;
                }
                Err(_) => None,
            };
            found.insert(role, folder);
        }
        server.logout();
        if let Some(class) = lost {
            log::warning(TAG, "an account's folders couldn't be listed", &[], Some(class));
            return Ok(Roles::new());
        }
        self.resolved.lock().map_err(|_| POISONED)?.insert(email.to_owned(), found.clone());
        Ok(found)
    }

    /// One warming round over the accounts warmed, of those `emails` names or of every one: each one's parts that are
    /// due. Nothing is asked of any server, nor counted, unless some account is warmed. A folder that fails is
    /// logged, and the round goes on.
    pub fn tick(&self, emails: Option<&BTreeSet<String>>) -> Result<(), Fault> {
        let accounts = self.accounts.warm_accounts()?;
        if !accounts.iter().any(|account| self.enabled(account)) {
            return Ok(());
        }
        let tick = {
            let mut tick = self.tick.lock().map_err(|_| POISONED)?;
            *tick += 1;
            *tick
        };
        let mut paced = false;
        for account in &accounts {
            if emails.is_some_and(|named| !named.contains(&account.email)) || !self.enabled(account) {
                continue;
            }
            let roles = self.roles_for(&account.email)?;
            for (role, _, every) in ROLES {
                if tick % every != 0 || (role == "archive" && account.archive_is_label_removal) {
                    continue;
                }
                let Some(Some(folder)) = roles.get(role) else { continue };
                if folder.to_uppercase() == "INBOX" {
                    continue;
                }
                if paced {
                    std::thread::sleep(self.pause);
                }
                paced = true;
                match self.sync.sync_recent(&account.email, folder, WARM_RECENT) {
                    Ok(0) => {}
                    Ok(kept) => log::info(TAG, "a folder warmed: +%(count)s", &[(Field::Count, &kept.to_string())]),
                    Err(error) => log::warning(TAG, "a folder's warming failed", &[], Some(error.class())),
                }
            }
        }
        Ok(())
    }
}

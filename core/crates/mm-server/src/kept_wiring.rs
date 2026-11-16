//! Each account's connection kept between the jobs on its gate (#5, `mm_mailbox::kept`): how long each account's may
//! serve, as its provider allows, and the keeper told of an account's change or removal once its logins have heard.

use std::sync::{Arc, OnceLock, Weak};

use mm_accounts::account::Account;
use mm_accounts::directory::Directory;
use mm_accounts::ports::{AccountChange, AccountEvents};
use mm_accounts::tls_pin::is_loopback;
use mm_kernel::errors::Fault;
use mm_mailbox::kept::{Keeping, KeptAccounts, KeptServers};

/// How long `account`'s connection may serve (`provider.resolve`'s recycle): Proton Bridge's, or a server like it
/// on this machine reached with STARTTLS, sooner.
pub fn keeping_of(account: &Account) -> Keeping {
    let bridge = account.kind == "proton" || (account.starttls && account.host.as_str().is_some_and(is_loopback));
    if bridge { Keeping::BRIDGE } else { Keeping::USUAL }
}

/// The accounts, as the configuration holds them now.
#[derive(Debug)]
pub(crate) struct DirectoryKeeping(pub(crate) Arc<Directory>);

impl KeptAccounts for DirectoryKeeping {
    fn keeping(&self, account: &str) -> Result<Option<Keeping>, Fault> {
        Ok(self.0.get(account)?.as_ref().map(keeping_of))
    }
}

/// What happened to an account, heard first by whoever heard it before (its logins, then IDLE), and then by the
/// keeper: a changed account's connection, logged in as it was, and a removed one's, are closed.
#[derive(Debug)]
pub(crate) struct KeptHeard {
    first: Arc<dyn AccountEvents>,
    /// The keeper, once the app has it: weak, since its connections' logins tell this of a refusal.
    kept: OnceLock<Weak<KeptServers>>,
}

impl KeptHeard {
    pub(crate) fn over(first: Arc<dyn AccountEvents>) -> Arc<KeptHeard> {
        Arc::new(KeptHeard { first, kept: OnceLock::new() })
    }

    /// Tell `kept` of each change from now on.
    pub(crate) fn tell(&self, kept: &Arc<KeptServers>) {
        let _ = self.kept.set(Arc::downgrade(kept));
    }
}

impl AccountEvents for KeptHeard {
    fn publish(&self, change: AccountChange) {
        let gone = match &change {
            AccountChange::Changed(email) | AccountChange::Removed(email) => Some(email.clone()),
            AccountChange::Added(_) | AccountChange::CredentialsRejected(..) => None,
        };
        self.first.publish(change);
        if let Some(email) = gone
            && let Some(kept) = self.kept.get().and_then(Weak::upgrade)
        {
            kept.forget(&email);
        }
    }
}

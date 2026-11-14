//! The Mailbox's background work beyond the sync, bound to its adapters (`bootstrap.mailbox`'s `warm`): folders
//! beyond the Inbox warmed as each sync round ends, for the accounts asking, over the sessions every sync opens.

use std::sync::Arc;

use mm_accounts::Directory;
use mm_kernel::errors::Fault;
use mm_mailbox::destination::Provider;
use mm_mailbox::ingest::Ingest;
use mm_mailbox::session::MailServers;
use mm_mailbox::warm::{Warming, WarmingParts};
use mm_mailbox::warm_ports::{WarmAccount, WarmAccounts};

use crate::environment::Environment;

/// What warming knows of each account, from the Directory.
#[derive(Debug)]
pub(crate) struct DirectoryWarmAccounts(pub(crate) Arc<Directory>);

impl WarmAccounts for DirectoryWarmAccounts {
    fn warm_accounts(&self) -> Result<Vec<WarmAccount>, Fault> {
        Ok(self
            .0
            .accounts()?
            .into_iter()
            .map(|a| WarmAccount {
                archive_is_label_removal: Provider::of_kind(&a.kind).archive_is_label_removal,
                asks: a.warm_folders,
                email: a.email,
            })
            .collect())
    }
}

/// Warming, as MAILMIND_WARM_FOLDERS and each account's entry ask for it, syncing through `ingest`.
pub(crate) fn warming(
    directory: &Arc<Directory>,
    servers: &Arc<dyn MailServers>,
    ingest: &Arc<Ingest>,
    environment: &Environment,
) -> Arc<Warming> {
    Arc::new(Warming::new(WarmingParts {
        accounts: Arc::new(DirectoryWarmAccounts(directory.clone())),
        servers: servers.clone(),
        sync: ingest.clone(),
        variable: environment.warm_folders(),
        pause: mm_mailbox::warm::PAUSE,
    }))
}

//! The Mailbox's background work beyond the sync, bound to its adapters (`bootstrap.mailbox`'s `warm`, and
//! `syncing`'s backfill): folders beyond the Inbox warmed as each sync round ends, for the accounts asking, over the
//! sessions every sync opens; and the body backfill, which `Workers::start` puts on a lane of its own and `work
//! backfill` runs a round of, fetching as a prefetch does, on each account's gate.

use std::sync::Arc;

use mm_accounts::Directory;
use mm_kernel::errors::Fault;
use mm_kernel::seams::Clock;
use mm_mailbox::backfill::{Backfill, BackfillParts};
use mm_mailbox::destination::Provider;
use mm_mailbox::fetching::{Fetching, FetchingParts};
use mm_mailbox::gates::Gates;
use mm_mailbox::ingest::Ingest;
use mm_mailbox::session::MailServers;
use mm_mailbox::warm::{Warming, WarmingParts};
use mm_mailbox::warm_ports::{WarmAccount, WarmAccounts};
use mm_store::Database;

use crate::command_wiring::DirectoryMoveAccounts;
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

/// The body backfill over the stored mail, fetching each body as a prefetch does.
pub(crate) fn backfill(
    directory: &Arc<Directory>,
    database: &Arc<Database>,
    servers: &Arc<dyn MailServers>,
    gates: &Arc<dyn Gates>,
    clock: &Arc<dyn Clock>,
) -> Backfill {
    let fetch = Fetching::new(FetchingParts {
        stored: database.clone(),
        kept: database.clone(),
        servers: servers.clone(),
        gates: gates.clone(),
        clock: clock.clone(),
    });
    Backfill::new(BackfillParts {
        accounts: Arc::new(DirectoryMoveAccounts(directory.clone())),
        wanted: database.clone(),
        fetch: Arc::new(fetch),
        clock: clock.clone(),
        pause: mm_mailbox::backfill::PAUSE,
    })
}

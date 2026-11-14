//! The reads a person waits on, as the app holds them: the lists and a conversation, a message opened, the digest,
//! an export, the outbox, the documents. `App` holds one `Reading`, which `App::assemble` makes
//! here from the adapters it made.

use std::sync::Arc;

use mm_accounts::ports::ConfigFile;
use mm_assistant::status::Status;
use mm_compose::drafts::Drafts;
use mm_compose::outbox::Outbox;
use mm_discovery::documents::DocumentHub;
use mm_discovery::senders::SenderFacts;
use mm_mailbox::bodies::Bodies;
use mm_mailbox::digest::{Digest, NotSyncing};
use mm_mailbox::folders::Folders;
use mm_mailbox::lists::Lists;
use mm_mailbox::ports::{BodyFetch, FolderSyncs};
use mm_store::Database;
use mm_store::export::Exports;

use crate::app::Seams;
use crate::environment::Environment;
use crate::wiring::{AiConfig, AssistantStatus, DraftsFolder, KnownAccounts, MessageOpening};

#[derive(Debug)]
pub struct Reading {
    pub lists: Lists,
    pub bodies: Bodies,
    pub digest: Digest,
    pub exports: Exports,
    pub outbox: Outbox,
    pub documents: DocumentHub,
}

/// What `Reading::assemble` binds: the adapters `App::assemble` made.
pub(crate) struct Adapters {
    pub(crate) accounts: Arc<KnownAccounts>,
    pub(crate) database: Arc<Database>,
    /// A folder's sync as it opens.
    pub(crate) syncs: Arc<dyn FolderSyncs>,
    pub(crate) config: Arc<dyn ConfigFile>,
    pub(crate) folders: Arc<Folders>,
    /// The calendar's reading of the invite a message opens with.
    pub(crate) invites: mm_calendar::invites::Invites,
    /// A message's body, fetched from its server where it isn't stored.
    pub(crate) fetch: Arc<dyn BodyFetch>,
}

impl Reading {
    pub(crate) fn assemble(made: Adapters, environment: &Environment, seams: &Seams) -> Reading {
        let Adapters { accounts, database, syncs, config, folders, invites, fetch } = made;
        let clock = seams.clock.clone();
        let ai = Status::new(
            Arc::new(AiConfig { file: config, environment_key: environment.gives_anthropic_key() }),
            database.clone(),
            clock.clone(),
        );
        let opening = MessageOpening {
            drafts: Drafts::new(database.clone(), Arc::new(DraftsFolder(folders)), clock.clone()),
            senders: SenderFacts::new(database.clone(), clock.clone()),
            invites,
        };
        Reading {
            exports: Exports::new(database.clone(), seams.outside.clone(), clock.clone(), environment.temporary()),
            lists: Lists::new(accounts.clone(), database.clone(), database.clone(), syncs, clock.clone()),
            bodies: Bodies::new(database.clone(), fetch, Arc::new(opening), seams.sanitizer.clone()),
            documents: DocumentHub::new(database.clone(), accounts.clone(), clock.clone()),
            outbox: Outbox::new(database.clone(), clock.clone(), environment.profile() == "full"),
            digest: Digest::new(
                accounts,
                database,
                Arc::new(NotSyncing),
                Arc::new(AssistantStatus(ai)),
                environment.profile(),
                clock,
            ),
        }
    }
}

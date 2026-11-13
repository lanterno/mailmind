//! The reads a person waits on, as the app holds them: the lists and a conversation, the tray of changes, the
//! digest, an export. `App` holds one `Reading`, which `App::assemble` makes here from the adapters it made.

use std::sync::Arc;

use mm_accounts::ports::ConfigFile;
use mm_assistant::status::Status;
use mm_mailbox::digest::{Digest, NotSyncing};
use mm_mailbox::lists::Lists;
use mm_mailbox::opening::StoredOnly;
use mm_mailbox::ports::MailSessions;
use mm_mailbox::tray::Tray;
use mm_store::Database;
use mm_store::export::Exports;

use crate::app::Seams;
use crate::environment::Environment;
use crate::wiring::{AiConfig, AssistantStatus, KnownAccounts};

#[derive(Debug)]
pub struct Reading {
    pub lists: Lists,
    pub tray: Tray,
    pub digest: Digest,
    pub exports: Exports,
}

impl Reading {
    pub(crate) fn assemble(
        accounts: Arc<KnownAccounts>,
        database: Arc<Database>,
        sessions: Arc<dyn MailSessions>,
        config: Arc<dyn ConfigFile>,
        environment: &Environment,
        seams: &Seams,
    ) -> Reading {
        let clock = seams.clock.clone();
        let opening = Arc::new(StoredOnly::new(database.clone(), sessions));
        let ai = Status::new(
            Arc::new(AiConfig { file: config, environment_key: environment.gives_anthropic_key() }),
            database.clone(),
            clock.clone(),
        );
        Reading {
            exports: Exports::new(database.clone(), seams.outside.clone(), clock.clone(), environment.temporary()),
            lists: Lists::new(accounts.clone(), database.clone(), database.clone(), opening, clock.clone()),
            tray: Tray::new(database.clone()),
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

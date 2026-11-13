//! The reads a person waits on, as the app holds them: the lists and a conversation, the tray of changes. `App`
//! holds one `Reading`, which `App::assemble` makes here from the adapters it made.

use std::sync::Arc;

use mm_kernel::seams::Clock;
use mm_mailbox::lists::Lists;
use mm_mailbox::opening::StoredOnly;
use mm_mailbox::ports::MailSessions;
use mm_mailbox::tray::Tray;
use mm_store::Database;

use crate::wiring::KnownAccounts;

#[derive(Debug)]
pub struct Reading {
    pub lists: Lists,
    pub tray: Tray,
}

impl Reading {
    pub(crate) fn assemble(
        accounts: Arc<KnownAccounts>,
        database: Arc<Database>,
        sessions: Arc<dyn MailSessions>,
        clock: Arc<dyn Clock>,
    ) -> Reading {
        let opening = Arc::new(StoredOnly::new(database.clone(), sessions));
        Reading {
            lists: Lists::new(accounts, database.clone(), database.clone(), opening, clock),
            tray: Tray::new(database),
        }
    }
}

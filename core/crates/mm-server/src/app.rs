//! The app as one process holds it: each use case bound to its adapters, this run's CSRF token, the page and the
//! port it is served on. A route reaches a use case here and nowhere else.
//!
//! What the app meets the world through comes in as `Seams`, so the app itself never knows whether it runs for a
//! person or under a test.

use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use mm_accounts::Directory;
use mm_accounts::credential::DATABASE_KEY_ITEM;
use mm_kernel::seams::{Clock, Keychain, Outside};
use mm_mailbox::folders::Folders;
use mm_mailbox::ingest::Ingest;
use mm_mailbox::session::MailServers;
use mm_store::{ConfigFile, Database, DbKey, PageFiles};

use crate::environment::{Environment, Settings};
use crate::page::Page;
use crate::serve::Work;
use crate::sync_wiring::{DirectoryAccounts, ImapServers, RuleBookClassifier, Unheard};
use crate::wiring::{ImapSessions, KnownAccounts};

/// Where the app reads the time and its secrets, and what it asks before it opens a file or reaches a host.
#[derive(Clone, Debug)]
pub struct Seams {
    pub clock: Arc<dyn Clock>,
    pub outside: Arc<dyn Outside>,
    pub keychain: Arc<dyn Keychain>,
}

#[derive(Debug)]
pub struct App {
    /// The port it is served on, which the Host and Origin checks name.
    pub port: u16,
    /// This run's CSRF token, which the page carries and every POST must.
    pub csrf: String,
    pub page: Page,
    pub directory: Arc<Directory>,
    pub folders: Folders,
    /// The sync: a folder's mail into the database (`work sync`, Undo's way back).
    pub ingest: Arc<Ingest>,
    /// The requests being answered now, which a stop waits for.
    pub work: Arc<Work>,
    /// The reads a person waits on: lists, conversations, the tray of changes.
    pub reading: crate::reading::Reading,
}

impl App {
    /// The app over the data in `settings.home`, serving the page built in `dist`, on `port`. It fails, saying why
    /// in words a person can act on and naming no secret, when the database's key can't be had.
    pub fn assemble(
        environment: &Environment,
        settings: &Settings,
        dist: PathBuf,
        port: u16,
        seams: &Seams,
    ) -> Result<App, String> {
        let key = match environment.db_key() {
            Some(key) => key,
            None => seams
                .keychain
                .find(DATABASE_KEY_ITEM)
                .ok()
                .flatten()
                .ok_or("no database key: MAILMIND_DB_KEY isn't set, and the Keychain can't give one")?,
        };
        let key = DbKey::from_hex(key.expose()).map_err(|_| "the database key isn't 64 hex digits")?;
        let csrf = crate::perimeter::new_token().map_err(|_| "the system's random source failed")?;
        let config = Arc::new(ConfigFile::new(settings.config(), seams.outside.clone()));
        let directory = Arc::new(Directory::new(config, seams.keychain.clone(), seams.clock.clone()));
        let database = Arc::new(Database::new(settings.db(), key, seams.outside.clone()));
        let sessions = Arc::new(ImapSessions { directory: directory.clone(), outside: seams.outside.clone() });
        let reading = crate::reading::Reading::assemble(
            Arc::new(KnownAccounts(directory.clone())),
            database.clone(),
            sessions.clone(),
            seams.clock.clone(),
        );
        let folders =
            Folders::new(Arc::new(KnownAccounts(directory.clone())), sessions, database.clone(), seams.clock.clone());
        let servers: Arc<dyn MailServers> = Arc::new(ImapServers {
            directory: directory.clone(),
            outside: seams.outside.clone(),
            read_only: environment.read_only(),
        });
        let ingest = Arc::new(Ingest::new(
            Arc::new(DirectoryAccounts(directory.clone())),
            servers,
            database.clone(),
            Arc::new(RuleBookClassifier { database: database.clone(), last: Mutex::new(None) }),
            Arc::new(Unheard),
            seams.clock.clone(),
        ));
        Ok(App {
            port,
            csrf,
            page: Page::new(PageFiles::new(dist, seams.outside.clone())),
            directory,
            folders,
            ingest,
            work: Arc::new(Work::default()),
            reading,
        })
    }
}

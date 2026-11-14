//! The app as one process holds it: each use case bound to its adapters, this run's CSRF token, the page and the
//! port it is served on. A route reaches a use case here and nowhere else.
//!
//! What the app meets the world through comes in as `Seams`, so the app itself never knows whether it runs for a
//! person or under a test.

use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use mm_accounts::Directory;
use mm_accounts::health::Health;
use mm_compose::snippets::Snippets;
use mm_curation::sweep_plan::StoredPlans;
use mm_kernel::seams::{Clock, Keychain, Outside};
use mm_mailbox::command_ports::Tokens;
use mm_mailbox::commands::{CommandParts, MailCommands};
use mm_mailbox::filing::{Filing, FilingParts};
use mm_mailbox::folders::Folders;
use mm_mailbox::gates::Gates;
use mm_mailbox::ingest::Ingest;
use mm_mailbox::labels::Labels;
use mm_mailbox::reconcile::{Reconciler, ReconcilerParts};
use mm_mailbox::reversal::Reversals;
use mm_mailbox::server_ops::ServerOps;
use mm_mailbox::session::MailServers;
use mm_mailbox::stats::LiveStats;
use mm_mailbox::sync_ports::Events;
use mm_mailbox::sync_record::SyncRecords;
use mm_mailbox::syncing::{Syncing, SyncingParts};
use mm_mailbox::undo::Undo;
use mm_store::compose::SnippetsDocument;
use mm_store::places::Places;
use mm_store::{ConfigFile, Database, DbKey, PageFiles};

use crate::command_wiring::{DirectoryMoveAccounts, RandomTokens};
use crate::environment::{Environment, Settings};
use crate::events::Hub;
use crate::filing_wiring::{AroundRounds, CurationFiling, FilingFlags, MuteReverser, SnoozeReverser, UnblockReverser};
use crate::gates::AccountGates;
use crate::heard::Heard;
use crate::page::Page;
use crate::serve::Work;
use crate::sync_wiring::{DirectoryAccounts, ImapServers, RuleBookClassifier};
use crate::wiring::{CurationPlans, ImapSessions, KnownAccounts, ListedAccounts, MailboxSyncs};
use crate::workers::Jobs;

/// Where the app reads the time and its secrets, and what it asks before it opens a file or reaches a host.
#[derive(Clone, Debug)]
pub struct Seams {
    pub clock: Arc<dyn Clock>,
    pub outside: Arc<dyn Outside>,
    pub keychain: Arc<dyn Keychain>,
    /// What makes a body's HTML safe for the reader and its frame: the sanitizer itself (`real::Sanitizing`), which
    /// only the test launcher takes away, to show the frame's walls hold alone.
    pub sanitizer: Arc<dyn mm_mailbox::ports::Sanitizer>,
}

#[derive(Debug)]
pub struct App {
    /// The port it is served on, which the Host and Origin checks name.
    pub port: u16,
    /// This run's CSRF token, which the page carries and every POST must.
    pub csrf: String,
    pub page: Page,
    pub directory: Arc<Directory>,
    pub folders: Arc<Folders>,
    /// The sync: a folder's mail into the database (`work sync`, Undo's way back).
    pub ingest: Arc<Ingest>,
    /// Each account's IMAP gate, which every job that talks to its server holds.
    pub gates: Arc<dyn Gates>,
    /// The change queue: a change to the mail on a server, and `work commands`.
    pub commands: Arc<MailCommands>,
    pub undo: Undo,
    pub filing: Arc<Filing>,
    /// Each account's INBOX synced, one round at a time (`/api/refresh`).
    pub syncing: Arc<Syncing>,
    /// IDLE's workers, one for each account, which `Workers::start` starts: none in the served suites.
    pub realtime: Arc<mm_mailbox::realtime::Realtime>,
    /// Ghosts pruned: mail gone from the server (`/api/reconcile`, `work reconcile`).
    pub reconciler: Reconciler,
    /// Keys with no copy left and finished changes, let go a month on (`work forget`).
    pub forgetting: mm_mailbox::forgetting::Forgetting,
    /// The requests being answered now, which a stop waits for.
    pub work: Arc<Work>,
    /// The reads a person waits on: lists, conversations, the tray of changes.
    pub reading: crate::reading::Reading,
    // The page's own state, and what the app says of itself.
    /// Where the app reads the time.
    pub clock: Arc<dyn Clock>,
    /// The places the page's URLs name.
    pub places: Places,
    /// What the app tells the open tabs: `app.events.publish(Topic::Mute, json!({...}))`, or a use case's
    /// `Arc<dyn Events>`.
    pub events: Arc<Hub>,
    /// The words a person keeps for the composer.
    pub snippets: Snippets,
    /// How each account stands.
    pub health: Health,
    /// Each account's stored inbox counted by bucket, kept until something moves them.
    pub stats: Arc<LiveStats>,
    /// The background jobs this process runs, which `Workers::start` schedules: none in the served suites.
    pub jobs: Arc<Jobs>,
    /// A Gmail account's own labels.
    pub labels: Labels,
    // Discovery's, Curation's and Attention's use cases.
    pub discovery: crate::bound::Discovery,
    pub curation: crate::bound::Curation,
    pub attention: crate::bound::Attention,
    pub tidying: crate::bound::Tidying,
    // Accounts' changes, and their test login.
    pub connections: mm_accounts::connections::Connections,
    /// Compose's: a send, a draft, the outbox (`work outbox`) and the drafts' pushes (`work drafts`).
    pub compose: crate::compose_wiring::Compose,
    /// Which logins are held back after their server refused them (`/api/sync-retry-account` lets one go).
    pub logins: Arc<mm_accounts::logins::Logins>,
    /// A sender's cleanup and a sweep.
    pub cleanups: crate::cleanup_wiring::Cleanups,
    /// Today & next, an answer to an invite and an event file.
    pub calendar: crate::calendar_wiring::Calendar,
    /// The Assistant's: every AI call, the cost governor, and how AI stands.
    pub assistant: crate::assistant_wiring::Assistant,
    /// The assistant's background work and Tidy's run (`work assess`, `work tidy`), which `Workers::start` schedules.
    pub background: crate::background::Background,
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
        let key: DbKey = crate::database_version::key(environment, &settings.db(), seams)?;
        let csrf = crate::perimeter::new_token().map_err(|_| "the system's random source failed")?;
        let config = Arc::new(ConfigFile::new(settings.config(), seams.outside.clone()));
        let directory = Arc::new(Directory::new(config, seams.keychain.clone(), seams.clock.clone()));
        // Every session logs in through `login`, which holds back a login its server refused; a refusal, and an
        // account's change, are heard by its logins. This process's own key names each login.
        let mut login_key = [0u8; 32];
        getrandom::fill(&mut login_key).map_err(|_| "the system's random source failed")?;
        let login_key = mm_accounts::login_hold::LoginKey::new(login_key);
        let logins = Arc::new(mm_accounts::logins::Logins::new(login_key, seams.clock.clone()));
        let account_events: Arc<dyn mm_accounts::ports::AccountEvents> =
            Arc::new(crate::heard::AccountsHeard { logins: logins.clone() });
        // IDLE's workers hear an account added, changed or removed once its logins have (`realtime.reconcile`).
        let idle_heard = crate::idle_wiring::IdleHeard::over(account_events);
        let account_events: Arc<dyn mm_accounts::ports::AccountEvents> = idle_heard.clone();
        let login = Arc::new(crate::login_wiring::ImapLogin {
            directory: directory.clone(),
            outside: seams.outside.clone(),
            logins: logins.clone(),
            events: account_events.clone(),
        });
        let database = Arc::new(Database::new(settings.db(), key, seams.outside.clone()));
        crate::database_version::ready(&database, &*seams.clock)?;
        // Discovery's, Curation's and Attention's, in the person's own zone.
        let zone = mm_store::zone_file::person_zone(environment.zone(), &*seams.outside);
        let gates: Arc<dyn Gates> = Arc::new(AccountGates::new(environment.gate_per_account()));
        // The Assistant's: every AI call, and how AI stands, which the digest says.
        let assistant = crate::assistant_wiring::Assistant::assemble(
            crate::assistant_wiring::Parts {
                database: database.clone(),
                accounts: Arc::new(KnownAccounts(directory.clone())),
                servers: Arc::new(ImapServers { login: login.clone(), read_only: environment.read_only() }),
                gates: gates.clone(),
                config: Arc::new(ConfigFile::new(settings.config(), seams.outside.clone())),
                keychain: seams.keychain.clone(),
                outside: seams.outside.clone(),
                clock: seams.clock.clone(),
                zone: zone.clone(),
            },
            environment,
        );
        let discovery = crate::bound::Discovery::assemble(&database, &directory, &seams.clock, &zone);
        let tidying = crate::bound::Tidying::assemble(&database, &seams.clock);
        // The calendar's, reading a TZID in the system's zone database.
        let zones = Arc::new(mm_store::calendar::SystemZones::new(seams.outside.clone()));
        let sessions = Arc::new(ImapSessions { login: login.clone() });
        let folders = Arc::new(Folders::new(
            Arc::new(KnownAccounts(directory.clone())),
            sessions.clone(),
            database.clone(),
            seams.clock.clone(),
        ));
        let servers: Arc<dyn MailServers> =
            Arc::new(ImapServers { login: login.clone(), read_only: environment.read_only() });
        // The page's own state, and what the app says of itself.
        let places = Places::new(database.clone(), seams.clock.clone());
        let events = Arc::new(Hub::new().map_err(|_| "the system's random source failed")?);
        let snippets = Snippets::new(Arc::new(SnippetsDocument::new(database.clone(), seams.clock.clone())));
        let syncs = MailboxSyncs(SyncRecords::new(database.clone()));
        let health = Health::new(Arc::new(syncs), seams.clock.clone());
        let plans = CurationPlans(StoredPlans::new(database.clone(), database.clone()));
        let stats =
            Arc::new(LiveStats::new(Arc::new(ListedAccounts(directory.clone())), Arc::new(plans), seams.clock.clone()));
        let labels = Labels::new(database.clone());
        // What the Mailbox and Attention say happened, told to the open tabs.
        let heard = Arc::new(Heard { hub: events.clone(), stats: stats.clone(), folders: folders.clone() });
        let mailbox_events: Arc<dyn Events> = heard.clone();
        let ingest = Arc::new(Ingest::new(
            Arc::new(DirectoryAccounts(directory.clone())),
            servers.clone(),
            database.clone(),
            Arc::new(RuleBookClassifier { database: database.clone(), last: Mutex::new(None) }),
            mailbox_events.clone(),
            seams.clock.clone(),
        ));
        // A message's body from its server where it isn't stored, on its account's gate; the reads a person waits on.
        let fetch: Arc<dyn mm_mailbox::ports::BodyFetch> =
            Arc::new(mm_mailbox::fetching::Fetching::new(mm_mailbox::fetching::FetchingParts {
                stored: database.clone(),
                kept: database.clone(),
                servers: servers.clone(),
                gates: gates.clone(),
                clock: seams.clock.clone(),
            }));
        // A folder's sync as it opens, on a thread counted as the requests being answered are.
        let work = Arc::new(Work::default());
        let syncs = Arc::new(mm_mailbox::opening::FolderOpening::new(mm_mailbox::opening::OpeningParts {
            stored: database.clone(),
            sync: ingest.clone(),
            gates: gates.clone(),
            events: mailbox_events.clone(),
            folders: folders.clone(),
            background: Arc::new(crate::sync_wiring::WorkThreads(work.clone())),
            waits: mm_mailbox::opening::Waits::default(),
        }));
        let reading = crate::reading::Reading::assemble(
            crate::reading::Adapters {
                accounts: Arc::new(KnownAccounts(directory.clone())),
                database: database.clone(),
                syncs,
                ai: Arc::new(crate::assistant_wiring::SharedStatus(assistant.status.clone())),
                folders: folders.clone(),
                invites: mm_calendar::invites::Invites::new(zones.clone(), zone.clone()),
                fetch: fetch.clone(),
            },
            environment,
            seams,
        );
        let tokens: Arc<dyn Tokens> = Arc::new(RandomTokens::default());
        let commands = Arc::new(MailCommands::new(CommandParts {
            store: database.clone(),
            accounts: Arc::new(DirectoryMoveAccounts(directory.clone())),
            server: ServerOps::new(servers.clone(), environment.read_only()),
            gates: gates.clone(),
            clock: seams.clock.clone(),
            events: mailbox_events.clone(),
            tokens: tokens.clone(),
            read_only: environment.read_only(),
        }));
        let reversals = Arc::new(Reversals::new(commands.clone(), ingest.clone()));
        let filing = Arc::new(Filing::new(FilingParts {
            commands: commands.clone(),
            ingest: ingest.clone(),
            folders: folders.clone(),
            gates: gates.clone(),
            clock: seams.clock.clone(),
            events: mailbox_events.clone(),
            tokens,
            stats: stats.clone(),
        }));
        // A person's filters, judged of new Inbox mail as a sync stores it: what they ask is queued as Filing queues it.
        ingest.hear_arrivals(Arc::new(crate::arrival_wiring::ArrivalFlags::new(
            &database,
            &seams.clock,
            &zone,
            filing.clone(),
        )));
        // Curation's and Attention's, which file and queue through the Mailbox, and whose changes Undo takes back.
        let curation_filing = Arc::new(CurationFiling {
            filing: filing.clone(),
            stats: stats.clone(),
            store: Arc::new(mm_store::curation::CurationStore::new(database.clone(), seams.clock.clone())),
            folders: folders.clone(),
            events: mailbox_events.clone(),
        });
        let curation =
            crate::bound::Curation::assemble(&database, &directory, &seams.clock, &zone, curation_filing.clone());
        let flags = Arc::new(FilingFlags(filing.clone()));
        let attention = crate::bound::Attention::assemble(&database, &directory, &seams.clock, &zone, flags, heard);
        let undo = Undo::new(
            database.clone(),
            gates.clone(),
            seams.clock.clone(),
            mailbox_events.clone(),
            vec![
                reversals,
                Arc::new(SnoozeReverser(attention.snoozing.clone())),
                Arc::new(MuteReverser(attention.muting.clone())),
                Arc::new(UnblockReverser(curation.screener.clone())),
            ],
        );
        let jobs = Arc::new(Jobs::new(seams.clock.clone()));
        // The servers a cleanup, a sweep and expiry read and move live mail on.
        let cleanup_servers = servers.clone();
        let move_accounts = Arc::new(DirectoryMoveAccounts(directory.clone()));
        let syncing = Arc::new(Syncing::new(SyncingParts {
            ingest: ingest.clone(),
            accounts: move_accounts.clone(),
            store: database.clone(),
            folders: folders.clone(),
            events: mailbox_events.clone(),
            clock: seams.clock.clone(),
            around: Arc::new(AroundRounds { snoozing: attention.snoozing.clone(), jobs: jobs.clone() }),
        }));
        // IDLE: a worker for each account, which `Workers::start` sets going; what it hears syncs those accounts.
        let realtime = crate::idle_wiring::realtime(&directory, &login, &syncing, environment.read_only(), &idle_heard);
        let reconciler = Reconciler::new(ReconcilerParts {
            store: database.clone(),
            accounts: move_accounts,
            servers,
            gates: gates.clone(),
            folders: folders.clone(),
            events: mailbox_events,
            clock: seams.clock.clone(),
            stats: stats.clone(),
        });
        // Accounts' changes, and their test login.
        let probe = crate::account_wiring::ImapProbe { login: login.clone() };
        let connections = mm_accounts::connections::Connections::new(
            directory.clone(),
            Arc::new(probe),
            account_events,
            seams.clock.clone(),
        );
        // Compose's, over sessions of its own and the config file as it is when each send goes.
        let compose = crate::compose_wiring::Compose::assemble(crate::compose_wiring::Parts {
            database: database.clone(),
            directory: directory.clone(),
            config: Arc::new(ConfigFile::new(settings.config(), seams.outside.clone())),
            outside: seams.outside.clone(),
            // Its copies' sessions log in as every other does, so a refused login is held back here too.
            servers: Arc::new(ImapServers { login: login.clone(), read_only: environment.read_only() }),
            gates: gates.clone(),
            folders: folders.clone(),
            events: events.clone(),
            ingest: ingest.clone(),
            clock: seams.clock.clone(),
            read_only: environment.read_only(),
            stats: stats.clone(),
            heard: Arc::new(Heard { hub: events.clone(), stats: stats.clone(), folders: folders.clone() }),
        });
        // A sender's cleanup, a sweep and expiry, which file as the Screener does; a mailto: unsubscribe goes through
        // Compose's outbox.
        let cleanups = crate::cleanup_wiring::Cleanups::assemble(crate::cleanup_wiring::CleanupsParts {
            database: database.clone(),
            directory: directory.clone(),
            servers: cleanup_servers,
            gates: gates.clone(),
            filing: curation_filing,
            clock: seams.clock.clone(),
            secret: csrf.clone(),
            read_only: environment.read_only(),
            delivery: compose.delivery.clone(),
        });
        // An answer to an invite goes through Compose's outbox.
        let calendar = crate::calendar_wiring::Calendar::assemble(crate::calendar_wiring::CalendarParts {
            database: database.clone(),
            directory: directory.clone(),
            fetch,
            zones,
            clock: seams.clock.clone(),
            zone: zone.clone(),
            delivery: compose.delivery.clone(),
        });
        // The assistant's background work, and Tidy's run, which files through the Mailbox.
        let background = crate::background::Background::assemble(crate::background::Parts {
            database: database.clone(),
            assistant: &assistant,
            directory: directory.clone(),
            filing: filing.clone(),
            events: events.clone(),
            clock: seams.clock.clone(),
            zone: zone.clone(),
            drafts_ahead: !environment.no_draft_tick(),
        });
        Ok(App {
            port,
            csrf,
            page: Page::new(PageFiles::new(dist, seams.outside.clone())),
            directory,
            folders,
            ingest,
            gates,
            commands,
            undo,
            filing,
            syncing,
            realtime,
            reconciler,
            forgetting: mm_mailbox::forgetting::Forgetting::new(database.clone(), seams.clock.clone()),
            work,
            reading,
            clock: seams.clock.clone(),
            places,
            events,
            snippets,
            health,
            stats,
            jobs,
            labels,
            discovery,
            curation,
            attention,
            tidying,
            connections,
            compose,
            logins,
            cleanups,
            calendar,
            assistant,
            background,
        })
    }
}

//! Discovery's, Curation's and Attention's use cases bound to their adapters (`mailmind.bootstrap.discovery`,
//! `.curation` and `.attention`), with the glue they need from the other contexts: which folder plays which part (the
//! Mailbox's), the accounts' names (the Directory's), whom the rule book keeps (Curation's) and whether a search can
//! be a filter (Discovery's). A context never names another, so it is answered here.

use std::collections::{BTreeMap, HashSet};
use std::sync::Arc;

use mm_accounts::Directory;
use mm_attention::followups::FollowUps;
use mm_attention::muting::{Muting, MutingParts};
use mm_attention::ports::{Accounts, Events as AttentionEvents, Flags};
use mm_attention::snoozing::{Snoozing, SnoozingParts};
use mm_attention::tasks::Tasks;
use mm_curation::cleanup::Cleanup;
use mm_curation::filters::Filters;
use mm_curation::ports::{Conditions, Filing, FilterIds, Mailboxes, RuleBook as _};
use mm_curation::screener::{Audit, Screener};
use mm_discovery::contacts::Contacts;
use mm_discovery::known::KnownContacts;
use mm_discovery::ports::{AccountNames, FolderRoles, KeptSenders};
use mm_discovery::search::Search;
use mm_discovery::suggest::Suggesting;
use mm_discovery::views::{Ids, SavedViews};
use mm_kernel::errors::Fault;
use mm_kernel::seams::Clock;
use mm_kernel::zone::Zone;
use mm_store::Database;
use mm_store::attention::{AttentionStore, UndoStack};
use mm_store::curation::CurationStore;
use mm_store::discovery::DiscoveryStore;

/// Which folder plays which part, by its name. What the last LIST said of an account's folders isn't asked yet:
/// the Mailbox keeps it inside `Folders`, which says nothing of one folder.
#[derive(Debug)]
pub(crate) struct NamedRoles;

impl FolderRoles for NamedRoles {
    fn role(&self, _account: &str, folder: &str) -> String {
        mm_mailbox::roles::role_by_name(folder, None).to_owned()
    }

    fn role_by_name(&self, folder: &str) -> String {
        mm_mailbox::roles::role_by_name(folder, None).to_owned()
    }
}

/// The accounts as the Directory names them.
#[derive(Debug)]
pub(crate) struct Names(pub(crate) Arc<Directory>);

impl AccountNames for Names {
    fn emails(&self) -> Result<Vec<String>, Fault> {
        Ok(self.0.accounts()?.into_iter().map(|a| a.email).collect())
    }

    fn aliases(&self) -> Result<BTreeMap<String, Option<String>>, Fault> {
        self.0.alias_map()
    }
}

/// Whom the rule book keeps.
#[derive(Debug)]
pub(crate) struct Kept(pub(crate) Arc<CurationStore>);

impl KeptSenders for Kept {
    fn kept(&self) -> Result<HashSet<String>, Fault> {
        Ok(self.0.load()?.list("keep_senders").iter().cloned().collect())
    }
}

/// Ids drawn from the operating system's random source.
#[derive(Debug)]
pub(crate) struct Drawn;

/// `n` random bytes.
pub(crate) fn drawn(n: usize) -> Result<Vec<u8>, Fault> {
    let mut bytes = vec![0u8; n];
    getrandom::fill(&mut bytes).map_err(|_| Fault::new("RandomSourceFailed"))?;
    Ok(bytes)
}

impl Ids for Drawn {
    fn view_id(&self) -> Result<String, Fault> {
        Ok(format!("v{}", drawn(4)?.iter().map(|b| format!("{b:02x}")).collect::<String>()))
    }
}

/// Discovery's use cases.
#[derive(Debug)]
pub struct Discovery {
    pub search: Search,
    pub views: SavedViews,
    pub contacts: Contacts,
    pub suggesting: Suggesting,
    pub known: KnownContacts,
}

impl Discovery {
    pub(crate) fn assemble(
        database: &Arc<Database>,
        directory: &Arc<Directory>,
        clock: &Arc<dyn Clock>,
        zone: &Arc<dyn Zone>,
    ) -> Discovery {
        let store = Arc::new(DiscoveryStore::new(database.clone(), clock.clone()));
        let names = Arc::new(Names(directory.clone()));
        let roles = Arc::new(NamedRoles);
        Discovery {
            search: Search::new(store.clone(), names.clone(), roles.clone(), clock.clone(), zone.clone()),
            views: SavedViews::new(store.clone(), Arc::new(Drawn)),
            contacts: Contacts::new(store.clone(), clock.clone()),
            suggesting: Suggesting::new(store.clone()),
            known: KnownContacts::new(
                store,
                names,
                roles,
                Arc::new(Kept(Arc::new(CurationStore::new(database.clone(), clock.clone())))),
            ),
        }
    }
}

/// The accounts the app may use, by address: disabled ones left out.
#[derive(Debug)]
pub(crate) struct Enabled(pub(crate) Arc<Directory>);

impl Mailboxes for Enabled {
    fn known(&self) -> Result<HashSet<String>, Fault> {
        Ok(self.0.accounts()?.into_iter().map(|a| a.email).collect())
    }
}

impl Accounts for Enabled {
    fn emails(&self) -> Result<Vec<String>, Fault> {
        Ok(self.0.accounts()?.into_iter().map(|a| a.email).collect())
    }
}

/// Whether a search can be a filter, as Discovery judges one, with a date that names no zone placed in the person's.
#[derive(Debug)]
pub(crate) struct ArrivalConditions(pub(crate) Arc<dyn Zone>);

impl Conditions for ArrivalConditions {
    fn refusal(&self, q: &str) -> Option<String> {
        mm_discovery::arrival::refusal(q, |wall| mm_discovery::search::placed(&*self.0, wall))
    }
}

/// `n` random bytes in URL-safe base64 without padding, as Python's `secrets.token_urlsafe(n)`.
pub(crate) fn token_urlsafe(n: usize) -> Result<String, Fault> {
    const ALPHABET: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    let bytes = drawn(n)?;
    let mut out = String::with_capacity(n.div_ceil(3) * 4);
    for chunk in bytes.chunks(3) {
        let b = [chunk[0], chunk.get(1).copied().unwrap_or(0), chunk.get(2).copied().unwrap_or(0)];
        let word = (u32::from(b[0]) << 16) | (u32::from(b[1]) << 8) | u32::from(b[2]);
        for i in 0..=chunk.len() {
            out.push(char::from(ALPHABET[((word >> (18 - 6 * i)) & 63) as usize]));
        }
    }
    Ok(out)
}

impl FilterIds for Drawn {
    fn filter_id(&self) -> Result<String, Fault> {
        Ok(format!("f-{}", token_urlsafe(8)?))
    }
}

/// Curation's use cases.
#[derive(Debug)]
pub struct Curation {
    pub screener: Arc<Screener>,
    pub filters: Filters,
    pub audit: Audit,
    pub cleanup: Cleanup,
    /// The database, for the copy a request names.
    pub(crate) database: Arc<Database>,
}

impl Curation {
    pub(crate) fn assemble(
        database: &Arc<Database>,
        directory: &Arc<Directory>,
        clock: &Arc<dyn Clock>,
        zone: &Arc<dyn Zone>,
        filing: Arc<dyn Filing>,
    ) -> Curation {
        let store = Arc::new(CurationStore::new(database.clone(), clock.clone()));
        Curation {
            screener: Arc::new(Screener::new(
                store.clone(),
                store.clone(),
                Arc::new(Enabled(directory.clone())),
                filing,
                clock.clone(),
            )),
            filters: Filters::new(store.clone(), Arc::new(ArrivalConditions(zone.clone())), Arc::new(Drawn)),
            audit: Audit::new(store.clone()),
            cleanup: Cleanup::new(store.clone(), store, clock.clone()),
            database: database.clone(),
        }
    }

    /// The pk the copy `key`, `folder` and `uid` name has now, or None where that copy isn't there.
    pub(crate) fn copy_pk(&self, key: i64, folder: &str, uid: Option<i64>) -> Result<Option<i64>, Fault> {
        self.database.read(|c| mm_store::copies::copy_pk(c, key, folder, uid))
    }
}

/// Attention's use cases.
#[derive(Debug)]
pub struct Attention {
    pub snoozing: Arc<Snoozing>,
    pub muting: Arc<Muting>,
    pub follow_ups: FollowUps,
    pub tasks: Tasks,
    /// The database, for the copies and the message a request names.
    pub(crate) database: Arc<Database>,
}

impl Attention {
    pub(crate) fn assemble(
        database: &Arc<Database>,
        directory: &Arc<Directory>,
        clock: &Arc<dyn Clock>,
        zone: &Arc<dyn Zone>,
        flags: Arc<dyn Flags>,
        events: Arc<dyn AttentionEvents>,
    ) -> Attention {
        let book = Arc::new(AttentionStore::new(database.clone()));
        let accounts = Arc::new(Enabled(directory.clone()));
        let undo = Arc::new(UndoStack::new(database.clone(), clock.clone()));
        Attention {
            snoozing: Arc::new(Snoozing::new(SnoozingParts {
                book: book.clone(),
                undo: undo.clone(),
                flags: flags.clone(),
                events: events.clone(),
                clock: clock.clone(),
                zone: zone.clone(),
            })),
            muting: Arc::new(Muting::new(MutingParts {
                book: book.clone(),
                accounts: accounts.clone(),
                undo,
                flags,
                events: events.clone(),
                clock: clock.clone(),
            })),
            follow_ups: FollowUps::new(book.clone(), accounts.clone(), events.clone(), clock.clone()),
            tasks: Tasks::new(book, accounts, events, clock.clone(), zone.clone()),
            database: database.clone(),
        }
    }

    /// The pk the copy `key`, `folder` and `uid` name has now, or None where that copy isn't there.
    pub(crate) fn copy_pk(&self, key: i64, folder: &str, uid: Option<i64>) -> Result<Option<i64>, Fault> {
        self.database.read(|c| mm_store::copies::copy_pk(c, key, folder, uid))
    }

    /// A copy of message `key` now: the one at `uid` in `folder`, else in `folder`, else another; None where none is left.
    pub(crate) fn message_pk(&self, key: i64, folder: Option<&str>, uid: Option<i64>) -> Result<Option<i64>, Fault> {
        self.database.read(|c| mm_store::copies::message_pk(c, key, folder, uid))
    }
}

/// Tidy's runs, as the brief's strip reads the newest (the Mailbox's `tidying.latest`).
#[derive(Debug)]
pub struct Tidying {
    stack: Arc<UndoStack>,
}

impl Tidying {
    pub(crate) fn assemble(database: &Arc<Database>, clock: &Arc<dyn Clock>) -> Tidying {
        Tidying { stack: Arc::new(UndoStack::new(database.clone(), clock.clone())) }
    }

    /// `GET /api/tidy`: the newest run a person can still take back, and what it filed, the last first.
    pub fn latest(&self) -> Result<serde_json::Value, Fault> {
        Ok(match mm_mailbox::tidy::latest(&*self.stack)? {
            Some(run) => serde_json::json!({
                "ok": true, "group": run.group, "filed": run.filed.len(),
                "items": run.filed.iter().map(|d| serde_json::json!({"subject": d.subject, "who": d.who})).collect::<Vec<_>>(),
            }),
            None => serde_json::json!({"ok": true, "group": null, "filed": 0, "items": []}),
        })
    }
}

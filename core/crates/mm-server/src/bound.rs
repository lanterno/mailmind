//! Discovery's use cases bound to their adapters (`mailmind.bootstrap.discovery`), with the glue they need from the
//! other contexts: which folder plays which part (the Mailbox's), the accounts' names (the Directory's) and whom the
//! rule book keeps (Curation's). A context never names another, so it is answered here.

use std::collections::{BTreeMap, HashSet};
use std::sync::Arc;

use mm_accounts::Directory;
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
use mm_store::discovery::DiscoveryStore;
use serde_json::Value;

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

/// Whom the rule book keeps, as the `rules` document holds them.
#[derive(Debug)]
pub(crate) struct RuleBook(pub(crate) Arc<Database>);

impl KeptSenders for RuleBook {
    fn kept(&self) -> Result<HashSet<String>, Fault> {
        let rules = self.0.read(|c| mm_store::blocks::document(c, "rules"))?;
        Ok(rules
            .get("keep_senders")
            .and_then(Value::as_array)
            .map(|kept| kept.iter().filter_map(Value::as_str).map(str::to_owned).collect())
            .unwrap_or_default())
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
            known: KnownContacts::new(store, names, roles, Arc::new(RuleBook(database.clone()))),
        }
    }
}

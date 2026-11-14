//! A person's filters as a sync stores new Inbox mail (`bootstrap.mailbox._Curation`'s `arrival_flags` and
//! `flags_queued`): Curation judges them, Discovery says whether a stored message is one a search finds, and what they
//! ask is queued as Filing queues a flag change.

use std::sync::Arc;

use mm_curation::arrival_ports::Judge;
use mm_curation::arriving::Arriving;
use mm_discovery::query::QuerySpec;
use mm_discovery::search::placed;
use mm_kernel::errors::Fault;
use mm_kernel::seams::Clock;
use mm_kernel::zone::Zone;
use mm_mailbox::arrival_ports::ArrivalFilters;
use mm_mailbox::filing::Filing;
use mm_store::Database;
use mm_store::curation::CurationStore;

/// Discovery's `conditions.holds`: a filter's search, read in the person's zone, asked of one stored message.
#[derive(Debug)]
struct StoredConditions {
    database: Arc<Database>,
    zone: Arc<dyn Zone>,
}

impl Judge for StoredConditions {
    fn holds(&self, pk: i64, queries: &[&str]) -> Result<Vec<bool>, Fault> {
        let specs: Vec<QuerySpec> =
            queries.iter().map(|q| QuerySpec::parse(q, |wall| placed(&*self.zone, wall))).collect();
        self.database.read(|c| specs.iter().map(|spec| mm_store::discovery::fits(c, pk, spec)).collect())
    }
}

/// The filters as the sync asks them.
#[derive(Debug)]
pub(crate) struct ArrivalFlags {
    arriving: Arriving,
    filing: Arc<Filing>,
}

impl ArrivalFlags {
    pub(crate) fn new(
        database: &Arc<Database>,
        clock: &Arc<dyn Clock>,
        zone: &Arc<dyn Zone>,
        filing: Arc<Filing>,
    ) -> ArrivalFlags {
        let rules = Arc::new(CurationStore::new(database.clone(), clock.clone()));
        let judge = Arc::new(StoredConditions { database: database.clone(), zone: zone.clone() });
        ArrivalFlags { arriving: Arriving::new(rules, judge), filing }
    }
}

impl ArrivalFilters for ArrivalFlags {
    fn arrival_flags(&self, pk: i64) -> Vec<(&'static str, bool)> {
        self.arriving.on_arrival(pk)
    }

    fn flags_queued(&self) {
        self.filing.flags_queued();
    }
}

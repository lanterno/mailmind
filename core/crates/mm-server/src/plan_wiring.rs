//! What the Mailbox's carrying out of a plan asks of the others (`bootstrap.mailbox`'s Attention port, and the store):
//! where the copies a preview saw are now, and a snooze through Attention's, under the plan's undo group.

use std::sync::Arc;

use mm_attention::snoozing::{Snoozing, Target};
use mm_kernel::errors::{Failure, Fault};
use mm_mailbox::filing::Filing;
use mm_mailbox::plans::{PlanCopies, PlanSnooze, Plans, PlansParts};
use mm_store::Database;
use serde_json::Value;

use crate::command_wiring::RandomTokens;

/// The copies a preview saw, as the store has them now.
#[derive(Debug)]
pub(crate) struct StoredCopies(pub(crate) Arc<Database>);

impl PlanCopies for StoredCopies {
    fn still_there(&self, messages: &[(i64, String)]) -> Result<Vec<i64>, Fault> {
        self.0.read(|c| {
            let mut pks = Vec::with_capacity(messages.len());
            for (key, folder) in messages {
                pks.extend(mm_store::copies::pk_in(c, *key, folder)?);
            }
            Ok(pks)
        })
    }
}

/// A plan's snooze, as `POST /api/snooze` makes one of the messages it names.
#[derive(Debug)]
pub(crate) struct PlanSnoozing(pub(crate) Arc<Snoozing>);

impl PlanSnooze for PlanSnoozing {
    fn snooze(&self, pks: &[i64], preset: Option<&str>, group: &str) -> Result<Value, Failure> {
        let target = Target { pks: Some(pks.to_vec()), ..Target::default() };
        self.0.snooze(&target, preset, None, Some(group))
    }
}

/// The Mailbox's carrying out of a plan, over filing, the store and Attention's snoozing.
pub(crate) fn plans(filing: &Arc<Filing>, database: &Arc<Database>, snoozing: &Arc<Snoozing>) -> Plans {
    Plans::new(PlansParts {
        filing: filing.clone(),
        copies: Arc::new(StoredCopies(database.clone())),
        snooze: Arc::new(PlanSnoozing(snoozing.clone())),
        tokens: Arc::new(RandomTokens::default()),
    })
}

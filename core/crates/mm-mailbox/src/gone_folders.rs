//! A folder another client deleted or renamed, forgotten once MailMind is sure the server no longer has it
//! (`folders::Folders::gone`): its stored mail and its sync state go, as a folder MailMind deleted goes
//! (`folder_changes::FolderStore::forget_folder`), so its mail leaves the lists and no sync asks for it again.
//!
//! Each change queued for it, out of it or into it, that hasn't reached the server is given back as one the server
//! refused, in the same transaction: a move's message is listed again where it was, and the tray says why. One a try
//! holds now is left to that try, which the server refuses too. Open lists hear `reconcile` only where a row went or
//! a change was given back: a folder never stored, opened from a name the server doesn't have, changes no list.

use std::sync::Arc;

use mm_kernel::errors::Fault;
use mm_kernel::events::Topic;
use mm_kernel::log::{self, Field};
use mm_kernel::seams::Clock;
use serde_json::json;

use crate::command::{self, Outcome};
use crate::command_ports::{self, CommandStore};
use crate::commands::MailCommands;
use crate::folders::ForgetsFolders;
use crate::stats::LiveStats;
use crate::sync_ports::{Events, MailboxEvent};

const TAG: &str = "folders";

#[derive(Debug)]
pub struct GoneFolders {
    store: Arc<dyn CommandStore>,
    events: Arc<dyn Events>,
    clock: Arc<dyn Clock>,
    /// The counts the page shows, which forgetting a folder's mail moves.
    stats: Arc<LiveStats>,
}

impl GoneFolders {
    pub fn new(
        store: Arc<dyn CommandStore>,
        events: Arc<dyn Events>,
        clock: Arc<dyn Clock>,
        stats: Arc<LiveStats>,
    ) -> GoneFolders {
        GoneFolders { store, events, clock, stats }
    }
}

impl ForgetsFolders for GoneFolders {
    /// Forget `folder` of `account`, its queued changes given back first: how many stored copies went. Open lists
    /// hear that mail went (`reconcile`, naming the folder), and the tray that changes were given back.
    fn forget(&self, account: &str, folder: &str) -> Result<usize, Fault> {
        let now = self.clock.now().micros().div_euclid(1_000_000);
        let why = command::why_given_up(Outcome::Refused);
        let (given_back, forgotten) = command_ports::write(&*self.store, |tx| {
            let aimed = tx.queued_for_folder(account, folder)?;
            for row in &aimed {
                tx.attempt_failed(row.id, true, row.attempts + 1, 0, why)?;
                MailCommands::give_back(tx, row)?;
            }
            Ok((aimed, tx.forget_folder(account, folder, now)?))
        })?;
        self.stats.invalidate();
        for row in &given_back {
            self.events.publish(MailboxEvent::Restored { id: row.id, account: row.account.clone() });
        }
        if forgotten > 0 || !given_back.is_empty() {
            let data = json!({"account": account, "folder": folder, "pruned": forgotten});
            self.events.publish(MailboxEvent::Page { topic: Topic::Reconcile, data });
        }
        log::info(
            TAG,
            "a folder the server no longer has was forgotten: %(count)s copies",
            &[(Field::Count, &forgotten.to_string())],
        );
        Ok(forgotten)
    }
}

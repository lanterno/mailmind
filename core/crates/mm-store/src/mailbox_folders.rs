//! A folder renamed or deleted on the server, as the store follows it (`mm_mailbox::folder_changes::FolderStore`): the
//! Python app's `rename_folders` and `forget_folder` (`mailbox.adapters.sqlite.sync_state`).

use mm_kernel::errors::Fault;
use mm_mailbox::folder_change::moved;
use mm_mailbox::folder_changes::FolderStore;
use rusqlite::params;

use crate::db::Database;
use crate::mailbox_sync::delete_message;

impl FolderStore for Database {
    fn rename_folder(&self, account: &str, old: &str, new: &str, delimiter: Option<&str>) -> Result<(), Fault> {
        self.write(|c| {
            let stored: Vec<String> = c
                .prepare(
                    "SELECT folder FROM messages WHERE account=?1 UNION SELECT folder FROM sync_state WHERE account=?1",
                )?
                .query_map([account], |r| r.get(0))?
                .collect::<Result<_, _>>()?;
            for name in stored {
                if let Some(renamed) = moved(&name, old, new, delimiter) {
                    c.execute(
                        "UPDATE messages SET folder=? WHERE account=? AND folder=?",
                        params![renamed, account, name],
                    )?;
                    c.execute(
                        "UPDATE sync_state SET folder=? WHERE account=? AND folder=?",
                        params![renamed, account, name],
                    )?;
                }
            }
            Ok(())
        })
    }

    fn forget_folder(&self, account: &str, folder: &str, now: i64) -> Result<(), Fault> {
        self.write(|c| {
            let pks: Vec<i64> = c
                .prepare("SELECT pk FROM messages WHERE account=? AND folder=?")?
                .query_map(params![account, folder], |r| r.get(0))?
                .collect::<Result<_, _>>()?;
            for pk in pks {
                delete_message(c, pk, now)?;
            }
            c.execute("DELETE FROM sync_state WHERE account=? AND folder=?", params![account, folder])?;
            Ok(())
        })
    }
}

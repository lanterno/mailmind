//! What the forget round lets go of (`mm_mailbox::forgetting::ForgetStore`): the Python app's
//! `forget_gone_messages` (`mailbox.adapters.sqlite.messages`) and `command_forget_finished` (`command_log`), in one
//! transaction. A key's assessment, snooze and batch item go with it, by the foreign keys' cascade.

use mm_kernel::errors::Fault;
use mm_mailbox::forgetting::{ForgetStore, Forgotten};

use crate::db::Database;

impl ForgetStore for Database {
    fn forget(&self, gone_before: i64, done_before: &str) -> Result<Forgotten, Fault> {
        self.write(|c| {
            let keys = c.execute("DELETE FROM message_identity WHERE gone_at < ?", [gone_before])?;
            let commands = c.execute("DELETE FROM mail_commands WHERE state='done' AND done_at < ?", [done_before])?;
            Ok(Forgotten { keys, commands })
        })
    }
}

//! What this Mac keeps of an account's mail, let go as the account is removed and a person asks for it
//! (`mm_accounts::ports::AccountMail`; the Python app's `bootstrap.mailbox.forget_account`): the search index's rows of
//! it, each context's rows under its address, and its stored copies with what hangs on them (bodies, attachments,
//! labels, recipients, thread links, by the foreign keys' cascade), and its message keys with what was said of them
//! (assessments, snoozes, batch items, so too). One transaction. The server's mail is untouched.

use mm_accounts::ports::AccountMail;
use mm_kernel::errors::Fault;

use crate::db::Database;

/// Each table that keeps rows under an account's address, besides `messages`: a draft's journal goes with its draft.
const UNDER_THE_ADDRESS: [&str; 10] = [
    "followups",
    "muted",
    "tasks",
    "thread_summaries",
    "draft_ahead",
    "drafts",
    "outbox",
    "sync_state",
    "mail_commands",
    "message_identity",
];

impl AccountMail for Database {
    fn forget(&self, email: &str) -> Result<usize, Fault> {
        self.write(|c| {
            c.execute(
                "DELETE FROM msg_fts WHERE rowid IN (SELECT pk FROM messages WHERE account=? COLLATE NOCASE)",
                [email],
            )?;
            for table in UNDER_THE_ADDRESS {
                c.execute(&format!("DELETE FROM {table} WHERE account=? COLLATE NOCASE"), [email])?;
            }
            c.execute("DELETE FROM messages WHERE account=? COLLATE NOCASE", [email])
        })
    }
}

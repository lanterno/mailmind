//! What this Mac keeps of an account's mail, let go as the account is removed and a person asks for it
//! (`mm_accounts::ports::AccountMail`; the Python app's `bootstrap.mailbox.forget_account`): the search index's rows of
//! it, each context's rows under its address, and its stored copies with what hangs on them (bodies, attachments,
//! labels, recipients, thread links, by the foreign keys' cascade), and its message keys with what was said of them
//! (assessments, snoozes, batch items, so too). What else names its mail goes too: Undo's entries for it, the audit
//! trails' records of it, the AI's cards made for it (the brief, the review, what changed since you looked), and the
//! ways out of a list its mail offered. One transaction. The server's mail is untouched.

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

/// The AI's cards, each made for the accounts its `accounts` names (sorted, comma-joined): caches, made again.
const MADE_FOR_THE_ADDRESS: [&str; 3] = ["briefs", "reviews", "deltas"];

/// The tables that name the accounts a sender's mail came to, as a JSON list: the address leaves each list, and a row
/// that named only it goes.
const LISTING_THE_ADDRESS: [&str; 2] = ["sender_unsub", "unsub_log"];

/// An Undo entry as JSON, one that isn't read as none.
const ENTRY: &str = "CASE WHEN json_valid(undo_entries.entry) THEN undo_entries.entry ELSE '{}' END";

/// The JSON `column` as `json_each` may read it: one that isn't JSON reads as nothing.
fn json(column: &str) -> String {
    format!("CASE WHEN json_valid({column}) THEN {column} ELSE 'null' END")
}

/// A JSON object in `column` whose `account` is the address (`?1`).
fn of_the_address(column: &str) -> String {
    format!(
        "coalesce(CASE WHEN json_valid({column}) AND json_type({column}) = 'object' \
         THEN json_extract({column}, '$.account') END, '') = ?1 COLLATE NOCASE"
    )
}

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
            // Undo: an entry of the account's goes, and a group of its alone; another group keeps its other children
            // (as JSON: what a subquery answers is text).
            c.execute(&format!("DELETE FROM undo_entries WHERE {}", of_the_address("entry")), [email])?;
            let children = format!("FROM json_each({ENTRY}, '$.children')");
            let child = of_the_address("value");
            c.execute(
                &format!(
                    "DELETE FROM undo_entries WHERE EXISTS (SELECT 1 {children} WHERE {child}) \
                     AND NOT EXISTS (SELECT 1 {children} WHERE NOT ({child}))"
                ),
                [email],
            )?;
            c.execute(
                &format!(
                    "UPDATE undo_entries SET entry = json_set(entry, '$.children', \
                     json((SELECT json_group_array(json(value)) {children} WHERE NOT ({child})))) \
                     WHERE EXISTS (SELECT 1 {children} WHERE {child})"
                ),
                [email],
            )?;
            c.execute(&format!("DELETE FROM audit_events WHERE {}", of_the_address("body")), [email])?;
            for table in MADE_FOR_THE_ADDRESS {
                c.execute(
                    &format!(
                        "DELETE FROM {table} WHERE instr(',' || lower(accounts) || ',', ',' || lower(?) || ',') > 0"
                    ),
                    [email],
                )?;
            }
            for table in LISTING_THE_ADDRESS {
                let listed = format!("FROM json_each({})", json(&format!("{table}.accounts")));
                let named = format!("EXISTS (SELECT 1 {listed} WHERE value = ?1 COLLATE NOCASE)");
                let others = format!("EXISTS (SELECT 1 {listed} WHERE value <> ?1 COLLATE NOCASE)");
                c.execute(&format!("DELETE FROM {table} WHERE {named} AND NOT {others}"), [email])?;
                c.execute(
                    &format!(
                        "UPDATE {table} SET accounts = (SELECT json_group_array(value) {listed} \
                         WHERE value <> ?1 COLLATE NOCASE) WHERE {named}"
                    ),
                    [email],
                )?;
            }
            c.execute("DELETE FROM messages WHERE account=? COLLATE NOCASE", [email])
        })
    }
}

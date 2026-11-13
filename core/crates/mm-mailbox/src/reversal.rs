//! What reverses a move: the Mailbox's kinds of undo entry (`mailbox.application.reversal`). One that hasn't reached
//! the server is cancelled; one that has gets a counter-move, from where its command says it went.

use std::sync::Arc;

use mm_kernel::errors::Fault;
use mm_kernel::log;
use serde_json::Value;

use crate::command::Command;
use crate::command_ports::{self, Tx};
use crate::commands::MailCommands;
use crate::ingest::Ingest;
use crate::undo::{Reversed, Reverser};

const TAG: &str = "undo";

#[derive(Debug)]
pub struct Reversals {
    commands: Arc<MailCommands>,
    ingest: Arc<Ingest>,
}

fn text<'a>(entry: &'a Value, name: &str) -> &'a str {
    entry.get(name).and_then(Value::as_str).unwrap_or_default()
}

fn done(account: &str, folder: &str, ok: bool) -> Reversed {
    (ok, Some(account.to_owned()), Some(folder.to_owned()))
}

impl Reversals {
    pub fn new(commands: Arc<MailCommands>, ingest: Arc<Ingest>) -> Reversals {
        Reversals { commands, ingest }
    }

    fn command_of(tx: &dyn Tx, entry: &Value) -> Result<Option<Command>, Fault> {
        match entry.get("command").and_then(Value::as_i64).filter(|id| *id != 0) {
            Some(id) => tx.command(id),
            None => tx.latest_move(text(entry, "account"), text(entry, "message_id")),
        }
    }

    /// A counter-move ended this filing, so what counts a person's week leaves it out. The move has happened by now,
    /// and a failure here mustn't put its entry back on the stack, so it is only logged.
    fn note_undone(&self, entry: &Value) {
        let noted = command_ports::write(self.commands.store(), |tx| match Self::command_of(tx, entry)? {
            Some(q) => tx.mark_reversed(q.id),
            None => Ok(()),
        });
        if let Err(fault) = noted {
            log::warning(TAG, "an undone move couldn't be marked", &[], Some(fault.class));
        }
    }

    /// Re-read the exact message put back: a watermark sync would miss a reused UID.
    fn read_back(&self, account: &str, folder: &str, message_id: &str) -> usize {
        self.ingest.ingest_message(account, folder, message_id).unwrap_or(0)
    }

    /// The entry, reconciled with the command path. A move that hasn't reached the server (waiting, being tried or
    /// given up) is cancelled and its message listed again: its row waited, marked, for the move to settle, so no
    /// server is asked. The answer, or None when a counter-move is still needed; and the entry, with where the move
    /// went and what the server did on the way.
    fn settled_with_command(&self, entry: &Value) -> Result<(Option<Reversed>, Value), Fault> {
        let mut entry = entry.clone();
        let (account, mid) = (text(&entry, "account").to_owned(), text(&entry, "message_id").to_owned());
        if mid.is_empty() {
            return Ok((None, entry));
        }
        let store = self.commands.store();
        let q = command_ports::read(store, |tx| Self::command_of(tx, &entry))?;
        match q {
            Some(q) if !q.applied => {
                let src = match text(&entry, "src_folder") {
                    "" => "INBOX".to_owned(),
                    src => src.to_owned(),
                };
                let (cancelled, listed) = command_ports::write(store, |tx| {
                    let cancelled = tx.cancel_unapplied(q.id)?;
                    if cancelled {
                        tx.restore_pending(q.id)?;
                    }
                    Ok((cancelled, cancelled && tx.copy_in(&account, &src, &mid, true)?))
                })?;
                if listed {
                    return Ok((Some(done(&account, &src, true)), entry));
                }
                if cancelled {
                    if self.read_back(&account, &src, &mid) > 0 {
                        return Ok((Some(done(&account, &src, true)), entry));
                    }
                    if text(&entry, "dest_folder").is_empty() {
                        // It left by no doing of ours: nothing names where to look.
                        return Ok((Some(done(&account, &src, true)), entry));
                    }
                }
                Ok((None, entry))
            }
            Some(q) => {
                if let (Some(dest), true) = (q.dest.as_deref(), text(&entry, "dest_folder").is_empty()) {
                    entry["dest_folder"] = Value::String(dest.to_owned());
                }
                if q.outcome.get("unseen").is_some_and(crate::command::truthy) {
                    entry["unseen"] = Value::Bool(true);
                }
                Ok((None, entry))
            }
            None => Ok((None, entry)),
        }
    }

    /// A move back from where it went.
    fn move_back(&self, entry: &Value) -> Result<Reversed, Fault> {
        let (settled, entry) = self.settled_with_command(entry)?;
        if let Some(settled) = settled {
            return Ok(settled);
        }
        let (account, src, mid) = (text(&entry, "account"), text(&entry, "src_folder"), text(&entry, "message_id"));
        let unseen = entry.get("unseen").is_some_and(crate::command::truthy);
        let moved = self
            .commands
            .server()
            .move_by_msgid(account, text(&entry, "dest_folder"), src, mid, unseen)
            .map_err(|e| Fault::new(e.class()))?;
        if !moved {
            return Ok(done(account, src, false));
        }
        self.note_undone(&entry);
        self.read_back(account, src, mid);
        Ok(done(account, src, true))
    }

    /// Gmail's archive reversed: \Inbox back on it in All Mail.
    fn gmail_archive(&self, entry: &Value) -> Result<Reversed, Fault> {
        let (settled, entry) = self.settled_with_command(entry)?;
        if let Some(settled) = settled {
            return Ok(settled);
        }
        let (account, mid) = (text(&entry, "account"), text(&entry, "message_id"));
        let server = self.commands.server();
        let all_mail = server
            .find_special(account, &["all"])
            .map_err(|e| Fault::new(e.class()))?
            .unwrap_or_else(|| "[Gmail]/All Mail".to_owned());
        let relabeled =
            server.relabel_by_msgid(account, &all_mail, mid, "\\Inbox").map_err(|e| Fault::new(e.class()))?;
        if !relabeled {
            return Ok(done(account, "INBOX", false));
        }
        self.note_undone(&entry);
        // Gmail keeps the original INBOX UID on a relabel: a targeted read, not a watermark sync.
        self.read_back(account, "INBOX", mid);
        Ok(done(account, "INBOX", true))
    }
}

impl Reverser for Reversals {
    fn reverses(&self, kind: &str) -> bool {
        matches!(kind, "move" | "gmail_archive")
    }

    fn reverse(&self, entry: &Value) -> Result<Reversed, Fault> {
        if entry.get("kind").and_then(Value::as_str) == Some("gmail_archive") {
            self.gmail_archive(entry)
        } else {
            self.move_back(entry)
        }
    }
}

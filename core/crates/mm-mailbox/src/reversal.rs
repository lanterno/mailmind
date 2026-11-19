//! What reverses a move: the Mailbox's kinds of undo entry (`mailbox.application.reversal`). One that hasn't reached
//! the server is cancelled; one that has gets a counter-move, from where its command says it went. A sweep's ways back
//! (`sweep_back`) go together, an account's on one session. A read-only process cancels as ever, and refuses a
//! counter-move (`READ_ONLY`) before any server is asked.

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

    /// The copy the move left in `folder` went back with the Undo: its row goes, as that folder's next reconcile would
    /// take it, so the folder no longer lists what its server no longer holds. Only logged when it fails: the message
    /// is back where it was.
    fn forget_left(&self, account: &str, folder: &str, message_id: &str) {
        let now = self.commands.now();
        let forgot =
            command_ports::write(self.commands.store(), |tx| match tx.newest_copy_in(account, folder, message_id)? {
                Some(pk) => tx.delete_message(pk, now),
                None => Ok(()),
            });
        if let Err(fault) = forgot {
            log::warning(TAG, "a copy moved back couldn't be forgotten", &[], Some(fault.class));
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
        self.commands.refuse_writes()?;
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
        self.forget_left(account, text(&entry, "dest_folder"), mid);
        Ok(done(account, src, true))
    }

    /// Gmail's archive reversed: \Inbox back on it in All Mail.
    fn gmail_archive(&self, entry: &Value) -> Result<Reversed, Fault> {
        let (settled, entry) = self.settled_with_command(entry)?;
        if let Some(settled) = settled {
            return Ok(settled);
        }
        self.commands.refuse_writes()?;
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

/// What a sweep's way back is: a message it moved from the INBOX to Trash, which no command moved.
const SWEEP_BACK: &str = "sweep_back";

impl Reversals {
    /// A sweep's ways back, one account's, at once: on one session each message found in the Trash it went to by its
    /// Message-ID moves back to where it was, then one more session reads them all in. One still where it was, which
    /// the sweep never moved, is back.
    fn swept_back(&self, entries: &[Value]) -> Result<Vec<Reversed>, Fault> {
        self.commands.refuse_writes()?;
        // Where each went from and to: (its account, its Trash, where it was).
        let places: Vec<(&str, &str, &str)> = entries
            .iter()
            .map(|e| match text(e, "src_folder") {
                "" => (text(e, "account"), text(e, "dest_folder"), "INBOX"),
                src => (text(e, "account"), text(e, "dest_folder"), src),
            })
            .collect();
        let mut answers: Vec<Reversed> = places.iter().map(|(account, _, src)| done(account, src, false)).collect();
        let mut asked: Vec<(&str, &str, &str)> = Vec::new();
        for (k, place) in places.iter().enumerate() {
            if asked.contains(place) || text(&entries[k], "message_id").is_empty() {
                continue;
            }
            asked.push(*place);
            let (account, trash, to) = *place;
            let at: Vec<usize> = (k..entries.len())
                .filter(|j| places[*j] == *place && !text(&entries[*j], "message_id").is_empty())
                .collect();
            let mids: Vec<String> = at.iter().map(|j| text(&entries[*j], "message_id").to_owned()).collect();
            let back =
                self.commands.server().move_back_many(account, trash, to, &mids).map_err(|e| Fault::new(e.class()))?;
            let mut read = Vec::new();
            for ((j, mid), went) in at.iter().zip(mids).zip(back) {
                answers[*j].0 = went;
                if went {
                    read.push(mid);
                }
            }
            // Read in as each message put back is (`read_back`), on one more session for them all.
            _ = self.ingest.ingest_messages(account, to, &read);
        }
        Ok(answers)
    }
}

impl Reverser for Reversals {
    fn reverses(&self, kind: &str) -> bool {
        matches!(kind, "move" | "gmail_archive" | SWEEP_BACK)
    }

    fn reverse(&self, entry: &Value) -> Result<Reversed, Fault> {
        match entry.get("kind").and_then(Value::as_str) {
            Some("gmail_archive") => self.gmail_archive(entry),
            Some(SWEEP_BACK) => {
                let mut answers = self.swept_back(std::slice::from_ref(entry))?;
                Ok(answers.pop().unwrap_or((false, None, None)))
            }
            _ => self.move_back(entry),
        }
    }

    fn together(&self, kind: &str) -> bool {
        kind == SWEEP_BACK
    }

    fn reverse_together(&self, entries: &[Value]) -> Result<Vec<Reversed>, Fault> {
        if entries.iter().all(|e| e.get("kind").and_then(Value::as_str) == Some(SWEEP_BACK)) {
            self.swept_back(entries)
        } else {
            entries.iter().map(|entry| self.reverse(entry)).collect()
        }
    }
}

//! A folder a person makes, renames or deletes (`mailmind.mailbox.application.folder_changes`): on the server through
//! the one command path (`MailCommands::change_folder`), and then here. Nothing of it queues: a server that can't be
//! reached says so, and nothing has changed.
//!
//! A folder that plays a part, that the server keeps, or that holds folders isn't deleted (`folder_change`). One that
//! holds mail is deleted only once a person has said so, having been told how much it holds, and then its mail goes to
//! the account's Trash first, as the server lists it (`roles::trash`): a MOVE of each message by UID, or a copy then
//! each original's own UID EXPUNGE, never a bulk EXPUNGE. If any mail is still there after that, it isn't deleted. On
//! Gmail a folder is a label, and its mail stays in All Mail and under every other label it has: so nothing moves, and
//! the label alone goes, as Gmail's own delete does. A delete that stops once mail has gone to Trash says so. A renamed
//! folder's rows go by its new name here, and a deleted one's go, its mail stored again as Trash is next read.

use std::fmt;
use std::sync::Arc;

use mm_kernel::errors::{Failure, Fault, Kind, Problem};
use mm_kernel::seams::Clock;
use serde_json::{Value, json};

use crate::command_ports::MoveAccounts;
use crate::commands::MailCommands;
use crate::folder_change;
use crate::folders::Folders;
use crate::ports::SessionError;
use crate::roles;
use crate::session::{MailServer, Search, ServerError, move_to};

pub const SERVER_SAID_NO: &str = "The server didn’t make that change.";
pub const NOT_REACHED: &str = "The server couldn’t be reached, so nothing changed. Try again in a moment.";
pub const NO_ACCOUNT: &str = "unknown account";
pub const STILL_HOLDS: &str = "Mail is still in this folder, so it wasn’t deleted. Try again.";
pub const MAY_BE_IN_TRASH: &str =
    "The server stopped answering, so some of this folder’s mail may be in Trash already. Try again in a moment.";
/// UIDs to one MOVE into Trash, so no command line grows too long.
pub const MOVE_BATCH: usize = 200;

fn messages(count: usize) -> String {
    if count == 1 { "1 message".to_owned() } else { format!("{count} messages") }
}

/// What a person is told of a folder that holds mail, before they say to delete it: on Gmail (`labels`), that the
/// mail stays.
pub fn holds(count: usize, labels: bool) -> String {
    if labels {
        format!("This folder holds {}. Deleting it leaves them in All Mail.", messages(count))
    } else {
        format!("This folder holds {}. Deleting it moves them to Trash first.", messages(count))
    }
}

/// What a person is told when mail went to Trash and then the folder stayed.
pub fn kept_folder(moved: usize) -> String {
    format!("Moved {} to Trash, but the server kept the folder.", messages(moved))
}

/// What the store keeps of an account's folders by name.
pub trait FolderStore: Send + Sync + fmt::Debug {
    /// A folder renamed on the server, and on a server with a separator (`delimiter`) the folders inside it, go by
    /// their new names here, in one transaction: their rows and their syncs' records keep what they were, and the next
    /// sync finds out whether the server kept the UIDs.
    fn rename_folder(&self, account: &str, old: &str, new: &str, delimiter: Option<&str>) -> Result<(), Fault>;
    /// A folder deleted on the server: its rows and its sync's record go.
    fn forget_folder(&self, account: &str, folder: &str, now: i64) -> Result<(), Fault>;
}

/// Why a change on the server stopped: a person's refusal in their words, or the server's.
enum Stop {
    Said(Problem),
    Server(ServerError),
}

impl From<Problem> for Stop {
    fn from(problem: Problem) -> Stop {
        Stop::Said(problem)
    }
}

impl From<ServerError> for Stop {
    fn from(error: ServerError) -> Stop {
        Stop::Server(error)
    }
}

impl From<SessionError> for Stop {
    fn from(error: SessionError) -> Stop {
        Stop::Server(error.into())
    }
}

fn conflict(said: impl Into<String>) -> Problem {
    Problem::new(Kind::Conflict, said)
}

/// What wires FolderChanges.
#[derive(Debug)]
pub struct FolderChangeParts {
    pub commands: Arc<MailCommands>,
    pub accounts: Arc<dyn MoveAccounts>,
    pub store: Arc<dyn FolderStore>,
    pub folders: Arc<Folders>,
    pub clock: Arc<dyn Clock>,
}

#[derive(Debug)]
pub struct FolderChanges {
    commands: Arc<MailCommands>,
    accounts: Arc<dyn MoveAccounts>,
    store: Arc<dyn FolderStore>,
    folders: Arc<Folders>,
    clock: Arc<dyn Clock>,
}

impl FolderChanges {
    pub fn new(parts: FolderChangeParts) -> FolderChanges {
        let FolderChangeParts { commands, accounts, store, folders, clock } = parts;
        FolderChanges { commands, accounts, store, folders, clock }
    }

    /// The Trash the account names, and whether its folders are Gmail's labels; not found when there is no such
    /// account.
    fn trash(&self, account: &str) -> Result<(String, bool), Failure> {
        let found = self.accounts.move_account(account)?.ok_or_else(|| Problem::not_found(NO_ACCOUNT))?;
        Ok((found.trash, found.provider.archive_is_label_removal))
    }

    /// `change(session)` as a job on the account's gate, each failure in a person's words.
    fn on_server<T>(
        &self,
        account: &str,
        change: impl FnOnce(&mut dyn MailServer) -> Result<T, Stop>,
    ) -> Result<T, Failure> {
        let answer = self.commands.change_folder(account, |s| match change(s) {
            Ok(done) => Ok(Ok(done)),
            Err(Stop::Said(problem)) => Ok(Err(problem)),
            Err(Stop::Server(error)) => Err(error),
        })?;
        match answer {
            Ok(Ok(done)) => Ok(done),
            Ok(Err(problem)) => Err(problem.into()),
            Err(ServerError::Unreachable(_)) => Err(Problem::new(Kind::Unreachable, NOT_REACHED).into()),
            Err(ServerError::Refused(_)) => Err(conflict(SERVER_SAID_NO).into()),
            Err(ServerError::Failed(fault)) => Err(fault.into()),
        }
    }

    /// A new folder at the top of the account's, by the name a person typed.
    pub fn create(&self, account: Option<&str>, name: Option<&str>) -> Result<Value, Failure> {
        let account = account.filter(|a| !a.is_empty()).ok_or_else(|| Problem::refused("account required"))?;
        self.trash(account)?;
        let made = self.on_server(account, |s| {
            let made = folder_change::new_name(name, &s.folders()?)?;
            s.create_folder(&made)?;
            Ok(made)
        })?;
        self.folders.invalidate(Some(account))?;
        Ok(json!({"ok": true, "folder": made}))
    }

    /// `folder` called what a person typed, where it is, with the folders inside it.
    pub fn rename(&self, account: Option<&str>, folder: Option<&str>, name: Option<&str>) -> Result<Value, Failure> {
        let (Some(account), Some(folder)) = (account.filter(|a| !a.is_empty()), folder.filter(|f| !f.is_empty()))
        else {
            return Err(Problem::refused("account and folder required").into());
        };
        let (trash, _) = self.trash(account)?;
        let (to, delimiter) = self.on_server(account, |s| {
            let folders = s.folders()?;
            let found = folder_change::changeable(&folders, folder, Some(&trash))?;
            let to = folder_change::renamed_to(found, name, &folders)?;
            let delimiter = found.delimiter.clone();
            if to != folder {
                s.rename_folder(folder, &to)?;
            }
            Ok((to, delimiter))
        })?;
        if to != folder {
            self.store.rename_folder(account, folder, &to, delimiter.as_deref())?;
        }
        self.folders.invalidate(Some(account))?;
        Ok(json!({"ok": true, "folder": to}))
    }

    /// Delete `folder`: at once when it holds no mail, and when it does, once a person has said so (`confirmed`),
    /// after its mail has gone to Trash, or on Gmail with its mail left in All Mail. A conflict, saying how much it
    /// holds and whether it goes to Trash, until they have. One that stops once mail has gone says so, and how much
    /// went.
    pub fn delete(&self, account: Option<&str>, folder: Option<&str>, confirmed: bool) -> Result<Value, Failure> {
        let (Some(account), Some(folder)) = (account.filter(|a| !a.is_empty()), folder.filter(|f| !f.is_empty()))
        else {
            return Err(Problem::refused("account and folder required").into());
        };
        let (named, labels) = self.trash(account)?;
        let (mut trash, mut moved, mut moving) = (named.clone(), 0usize, false);
        let deleted = self.on_server(account, |s| {
            let folders = s.folders()?;
            let found = folder_change::changeable(&folders, folder, Some(&named))?;
            // As the server lists it: a localised or renamed Trash too.
            roles::trash(&folders, &named).clone_into(&mut trash);
            if folder_change::has_folders_inside(&folders, found) {
                return Err(Problem::refused(folder_change::HAS_FOLDERS).into());
            }
            s.select(folder, false)?;
            let held = s.search(Search::Undeleted)?;
            if !held.is_empty() && !confirmed {
                let holding = conflict(holds(held.len(), labels)).with("messages", json!(held.len()));
                return Err(holding.with("to_trash", json!(!labels)).into());
            }
            // On Gmail the mail stays in All Mail, and the label alone goes.
            let going: &[u32] = if labels { &[] } else { &held };
            for batch in going.chunks(MOVE_BATCH) {
                moving = true;
                move_to(s, batch, &trash)?;
                moved += batch.len();
            }
            if !going.is_empty() && !s.search(Search::Undeleted)?.is_empty() {
                return Err(conflict(STILL_HOLDS).into());
            }
            // Leave it, expunging nothing, before it goes.
            s.select("INBOX", true)?;
            s.delete_folder(folder)?;
            Ok(moved)
        });
        let trashed = match deleted {
            Ok(trashed) => trashed,
            Err(Failure::Problem(problem)) if problem.kind == Kind::Unreachable && moving => {
                self.deleted(account, folder, &trash, true)?;
                return Err(Problem::new(Kind::Unreachable, MAY_BE_IN_TRASH).into());
            }
            Err(Failure::Problem(problem)) if problem.kind == Kind::Conflict && moved > 0 => {
                self.deleted(account, folder, &trash, true)?;
                return Err(conflict(kept_folder(moved)).with("trashed", json!(moved)).into());
            }
            Err(failure) => return Err(failure),
        };
        self.deleted(account, folder, &trash, trashed > 0)?;
        Ok(json!({"ok": true, "trashed": trashed}))
    }

    /// The folder's rows go, so what is still in it is read again when it next opens, and Trash, which mail went to,
    /// is caught up as it opens.
    fn deleted(&self, account: &str, folder: &str, trash: &str, moved: bool) -> Result<(), Failure> {
        self.store.forget_folder(account, folder, self.clock.now().whole_seconds())?;
        self.folders.invalidate(Some(account))?;
        if moved {
            self.folders.touched(account, trash)?;
        }
        Ok(())
    }
}

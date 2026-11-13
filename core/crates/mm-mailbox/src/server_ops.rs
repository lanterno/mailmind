//! What a change to the mail asks of the server (`mailbox.application.server_ops`): one command's try (`apply`) and
//! the reversals. Trash is a move to the account's Trash folder, so it can be reversed. A connection that fails is
//! `ServerError::Unreachable`; a server's NO or BAD means the message didn't move.
//!
//! Each opens a session of its own and logs out after, or hangs up with no LOGOUT once the connection failed: a
//! server that didn't answer the last command won't answer that one, and waiting to hear so is a second timeout.

use std::sync::Arc;

use serde_json::Value;

use crate::command::{self, Command, Outcome, ServerAnswer};
use crate::command_ports::MoveAccount;
use crate::destination::{self, Way};
use crate::session::{
    MailServer, MailServers, Search, ServerError, by_message_id, find_role, found_by_message_id, move_to,
};
use crate::sync_plan::{COLUMNS, IMAP_NAMES};

/// Seconds each step waits for the server when no one waits on the answer.
const SESSION_TIMEOUT: u64 = 60;
/// A read-only process's changes queue for a full run: refused before any connection, as unreachable.
const READ_ONLY: ServerError = ServerError::Unreachable("ReadOnlyError");

#[derive(Debug)]
pub struct ServerOps {
    servers: Arc<dyn MailServers>,
    read_only: bool,
}

/// The selected folder's message `uid` carries \Seen, as the server says now; `otherwise` when it won't say.
fn seen_on_server(s: &mut dyn MailServer, uid: u32, otherwise: bool) -> Result<bool, ServerError> {
    match s.flags(&[uid]) {
        Ok(flags) => Ok(flags.get(&uid).map_or(otherwise, |f| f.iter().any(|f| f.eq_ignore_ascii_case("\\Seen")))),
        Err(ServerError::Refused(_)) => Ok(otherwise),
        Err(other) => Err(other),
    }
}

/// Whether `folder` already holds a message with Message-ID `mid`, with `back_to` selected again after. A queued
/// move asks before it moves: on a server without MOVE, a connection that dropped after the COPY landed left the
/// original behind, and moving it again would copy twice. A copy flagged \Deleted doesn't count.
fn already_in(s: &mut dyn MailServer, folder: &str, mid: &str, back_to: &str) -> Result<bool, ServerError> {
    if mid.is_empty() {
        return Ok(false);
    }
    let found = match s.select(folder, false) {
        Err(ServerError::Refused(_)) => Ok(false),
        Err(other) => Err(other),
        Ok(_) => match s.search(Search::UndeletedMessageId(mid)) {
            Ok(uids) => Ok(!uids.is_empty()),
            Err(ServerError::Refused(_)) => Ok(false),
            Err(other) => Err(other),
        },
    };
    s.select(back_to, false)?;
    found
}

/// Select `src` and name the UID a try acts on, or None when the message has left it.
fn target(
    s: &mut dyn MailServer,
    src: &str,
    mid: &str,
    row: &Command,
    first_try: bool,
) -> Result<Option<u32>, ServerError> {
    let selected = s.select(src, false)?;
    let number = |name: &str| row.param(name).and_then(Value::as_i64);
    let aim = command::of_try(number("uid"), number("epoch"), selected.uidvalidity, first_try, !mid.is_empty());
    if aim.by_message_id {
        match by_message_id(s, mid) {
            Ok(found) => return Ok(found),
            // It won't search: only the UID can say.
            Err(ServerError::Refused(_)) => {}
            Err(other) => return Err(other),
        }
    }
    Ok(aim.uid)
}

impl ServerOps {
    pub fn new(servers: Arc<dyn MailServers>, read_only: bool) -> ServerOps {
        ServerOps { servers, read_only }
    }

    /// Open a session on `account`'s server, run `work`, and log out; `timeout` is seconds for each step.
    fn on_server<T>(
        &self,
        account: &str,
        timeout: Option<u64>,
        work: impl FnOnce(&mut dyn MailServer) -> Result<T, ServerError>,
    ) -> Result<T, ServerError> {
        let mut s = self.servers.open(account, timeout.unwrap_or(SESSION_TIMEOUT))?;
        let answer = work(&mut *s);
        match answer {
            Err(ServerError::Unreachable(_)) => s.shutdown(),
            _ => s.logout(),
        }
        answer
    }

    /// One try at the command `row` on `account`'s server, on its gate. Unless this is the first try, an earlier one
    /// may have got part of the way, so it asks the server before it repeats anything: exactly once, however many
    /// tries it takes. `timeout` is how long each step waits, when someone is waiting on the try. A connection that
    /// fails is an error, which the caller queues or backs off on.
    pub fn apply(
        &self,
        row: &Command,
        account: &MoveAccount,
        first_try: bool,
        timeout: Option<u64>,
    ) -> Result<ServerAnswer, ServerError> {
        if self.read_only {
            return Err(READ_ONLY);
        }
        let (kind, src, mid) = (row.kind.as_str(), row.src_folder.as_str(), row.message_id.trim());
        let run = |s: &mut dyn MailServer| -> Result<ServerAnswer, ServerError> {
            let Some(uid) = target(s, src, mid, row, first_try)? else {
                // Already gone: done, and never retried.
                return Ok(ServerAnswer::default());
            };
            if kind == "flag" {
                for (name, on) in command::flags_of(row.param("flags")) {
                    if let Some(i) = COLUMNS.iter().position(|c| *c == name) {
                        s.store_flags(&[uid], on, &[IMAP_NAMES[i]])?;
                    }
                }
                return Ok(ServerAnswer::default());
            }
            let trash = Some(account.trash.as_str());
            let place = destination::resolve(kind, row.dest.as_deref(), &account.provider, trash, |roles| {
                find_role(&mut *s, roles)
            })?;
            let way = destination::choose(kind, &place, &s.capabilities(), account.provider.marks_trash_read, trash);
            if way.way == Way::Labels {
                s.store_labels(&[uid], false, &["\\Inbox"])?;
                return Ok(ServerAnswer::default());
            }
            let Some(folder) = place.folder else {
                return Ok(ServerAnswer::wont(Outcome::NoFolder));
            };
            let seen = row.param("seen").is_none_or(command::truthy);
            let unseen = way.read_seen_first && !seen_on_server(s, uid, seen)?;
            if !first_try && already_in(s, &folder, mid, src)? {
                // An earlier try's COPY landed, then its connection dropped: only the original is left to remove.
                s.store_flags(&[uid], true, &["\\Deleted"])?;
                s.expunge(&[uid])?;
            } else {
                move_to(s, &[uid], &folder)?;
            }
            Ok(ServerAnswer { wont: None, dest: Some(folder), unseen })
        };
        match self.on_server(&account.email, timeout, run) {
            // NO or BAD: it didn't happen, and won't.
            Err(ServerError::Refused(_)) => Ok(ServerAnswer::wont(Outcome::Refused)),
            other => other,
        }
    }

    /// Undo's way back: the message with this exact Message-ID in `search_folder` moves to `dest_folder`. False
    /// when it isn't found, never a guess, or the server refuses the move. `unseen` takes \Seen off it there, as it
    /// was before a server that marks trashed mail read (Proton) set it.
    pub fn move_by_msgid(
        &self,
        account: &str,
        search_folder: &str,
        dest_folder: &str,
        message_id: &str,
        unseen: bool,
    ) -> Result<bool, ServerError> {
        self.on_server(account, None, |s| {
            s.select(search_folder, false)?;
            let Some(uid) = found_by_message_id(s, message_id)? else { return Ok(false) };
            match move_to(s, &[uid], dest_folder) {
                Err(ServerError::Refused(_)) => return Ok(false),
                other => other?,
            }
            if unseen {
                s.select(dest_folder, false)?;
                if let Some(back) = found_by_message_id(s, message_id)? {
                    match s.store_flags(&[back], false, &["\\Seen"]) {
                        Err(ServerError::Refused(_)) | Ok(()) => {}
                        Err(other) => return Err(other),
                    }
                }
            }
            Ok(true)
        })
    }

    /// Gmail's archive reversed: `label` back on the message found by Message-ID in `search_folder` (All Mail).
    /// False when it isn't found or the server refuses the label.
    pub fn relabel_by_msgid(
        &self,
        account: &str,
        search_folder: &str,
        message_id: &str,
        label: &str,
    ) -> Result<bool, ServerError> {
        self.on_server(account, None, |s| {
            s.select(search_folder, false)?;
            let Some(uid) = found_by_message_id(s, message_id)? else { return Ok(false) };
            match s.store_labels(&[uid], true, &[label]) {
                Ok(()) => Ok(true),
                Err(ServerError::Refused(_)) => Ok(false),
                Err(other) => Err(other),
            }
        })
    }

    /// The folder playing the first of `roles` on an account's server (Gmail's All Mail, say).
    pub fn find_special(&self, account: &str, roles: &[&str]) -> Result<Option<String>, ServerError> {
        self.on_server(account, None, |s| find_role(s, roles))
    }
}

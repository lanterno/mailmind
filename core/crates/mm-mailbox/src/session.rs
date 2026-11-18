//! What MailMind asks of a logged-in connection to an account's mail server beyond the folder rail: the
//! `MailServer` port (`mailmind.mailbox.application.session`), which mm-imap implements, and what the use cases
//! build on it (`move_to`, `move_by_copy`, `find_role`, `by_message_id`), and the read-only session.
//!
//! - A folder name is as LIST gave it, its quoted-string escapes in place: the form the database stores and the
//!   network sends back as it came. A UID is a u32.
//! - A server that refuses a command answers `ServerError::Refused`, and a connection that is lost
//!   `ServerError::Unreachable`: the Python app's `IMAP4.error` and `IMAP4.abort`, so every caller tells them apart
//!   as it does there.
//! - A read-only process's session (`ReadOnly`) opens every folder read-only and refuses any write before it is
//!   sent, as unreachable (`profile.ReadOnlyError` is a member of `UNREACHABLE`). A read-only process refuses a
//!   change before it is queued, so this is the last wall: what a full run queued and reaches it waits for the next.

use std::collections::BTreeMap;
use std::fmt;

use mm_kernel::errors::Fault;

use crate::ports::{MailSession, SessionError};
use crate::roles::{self, Folder};

/// Why a command on a server didn't do what was asked, by its class alone: never what a server said.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ServerError {
    /// The connection failed or was lost, or this process only reads: asking again later may help (`UNREACHABLE`).
    Unreachable(&'static str),
    /// The server said NO or BAD, or a value couldn't be sent: it didn't happen, and asking again won't help.
    Refused(&'static str),
    /// Something else went wrong.
    Failed(Fault),
}

impl ServerError {
    pub fn class(self) -> &'static str {
        match self {
            ServerError::Unreachable(class) | ServerError::Refused(class) => class,
            ServerError::Failed(fault) => fault.class,
        }
    }

    pub fn is_unreachable(self) -> bool {
        matches!(self, ServerError::Unreachable(_))
    }

    /// Whether the server said the folder asked for doesn't exist (`NO_SUCH_FOLDER`): another client deleted or
    /// renamed it.
    pub fn is_gone(self) -> bool {
        self == ServerError::Refused(NO_SUCH_FOLDER)
    }
}

/// The class of a SELECT or EXAMINE the server refused because the folder doesn't exist: its NO said so in a
/// `[NONEXISTENT]` code (RFC 5530), or a LIST that worked doesn't name it. Never a refusal that says neither, and never
/// a server that couldn't be reached: those leave the folder as it was.
pub const NO_SUCH_FOLDER: &str = "NoSuchFolder";

/// Select `folder` (`readonly` as `MailServer::select`), telling a folder the server no longer has apart from any
/// other refusal: a NO without the `[NONEXISTENT]` code asks LIST, and a LIST that works but doesn't name the folder
/// means it is gone. INBOX is never gone. A LIST that fails leaves the refusal as it was.
pub fn select_or_gone(server: &mut dyn MailServer, folder: &str, readonly: bool) -> Result<Selected, ServerError> {
    match server.select(folder, readonly) {
        Err(ServerError::Refused(class)) if class != NO_SUCH_FOLDER && !folder.eq_ignore_ascii_case("INBOX") => {
            match server.folders() {
                Ok(listed) if !listed.iter().any(|f| f.name == folder) => Err(ServerError::Refused(NO_SUCH_FOLDER)),
                _ => Err(ServerError::Refused(class)),
            }
        }
        other => other,
    }
}

impl From<SessionError> for ServerError {
    fn from(error: SessionError) -> ServerError {
        match error {
            SessionError::Unreachable(class) => ServerError::Unreachable(class),
            // The rail's session says a refusal as a failure of its class.
            SessionError::Failed(fault) => ServerError::Refused(fault.class),
        }
    }
}

impl From<ServerError> for SessionError {
    fn from(error: ServerError) -> SessionError {
        match error {
            ServerError::Unreachable(class) => SessionError::Unreachable(class),
            ServerError::Refused(class) => SessionError::Failed(Fault::new(class)),
            ServerError::Failed(fault) => SessionError::Failed(fault),
        }
    }
}

/// What selecting a folder said of it.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Selected {
    pub exists: u32,
    pub uidvalidity: Option<u32>,
    pub uidnext: Option<u32>,
    /// Once `enable_condstore` said yes, if the server keeps one for the folder.
    pub highest_modseq: Option<u64>,
}

/// One message's header fields, as a header fetch got them.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Headers {
    pub uid: u32,
    pub flags: Vec<String>,
    /// The fields asked for, as the server sent them.
    pub raw: Vec<u8>,
    /// Gmail's X-GM-THRID, when asked for.
    pub gmail_thread: Option<String>,
    /// Gmail's X-GM-LABELS, when asked for, each as a person reads it (a system one as `\Inbox`).
    pub gmail_labels: Option<Vec<String>>,
}

/// What a UID SEARCH asks for. Each value in it is quoted by the network, never written into a command here.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Search<'a> {
    /// Every message.
    All,
    /// Every message not flagged \Deleted.
    Undeleted,
    /// Every UID above this one (`UID n+1:*`, which always matches the last UID, even one below it).
    Above(u32),
    /// Every UID below this one (`UID 1:n-1`); none below 1.
    Below(u32),
    /// This UID, if the folder still holds it.
    Uid(u32),
    /// What arrived on or after this day, in days since 1970-01-01 (`SINCE 1-Jan-1970`).
    Since(i64),
    /// The messages with this Message-ID header.
    MessageId(&'a str),
    /// The same, leaving out a copy flagged \Deleted.
    UndeletedMessageId(&'a str),
    /// What changed past this mod-sequence (CONDSTORE), once enabled.
    ChangedSince(u64),
    /// The messages whose From holds this text (`FROM "notify.example.test"`), as the server matches it: a coarse
    /// filter, which the caller narrows.
    From(&'a str),
}

/// The upper-case header names a sync fetches.
pub const HEADER_FIELDS: [&str; 11] = [
    "FROM",
    "TO",
    "CC",
    "SUBJECT",
    "DATE",
    "MESSAGE-ID",
    "IN-REPLY-TO",
    "REFERENCES",
    "LIST-UNSUBSCRIBE",
    "LIST-UNSUBSCRIBE-POST",
    "LIST-ID",
];

/// UIDs to a header FETCH, so no command line grows too long.
pub const HEADER_BATCH: usize = 300;
/// UIDs to a FETCH of messages' heads, so no answer holds more than a few megabytes.
pub const HEAD_BATCH: usize = 50;

/// The first bytes of a message, and how long the whole of it is, as the server says (RFC822.SIZE).
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Opening {
    pub raw: Vec<u8>,
    /// The whole message's size in bytes; None where the server didn't say.
    pub size: Option<u32>,
}

impl Opening {
    /// Whether this is the whole message, when its first `asked` bytes were asked for: it ended before them, or it is
    /// exactly as long as the server says the message is. A size shorter than what came is wrong (Exchange's are
    /// estimates), so says nothing: the message counts as cut off.
    pub fn is_whole(&self, asked: u32) -> bool {
        self.raw.len() < usize::try_from(asked).unwrap_or(usize::MAX)
            || self.size.and_then(|size| usize::try_from(size).ok()) == Some(self.raw.len())
    }
}

/// A logged-in connection, for everything the Mailbox asks of a server.
pub trait MailServer: MailSession {
    /// The server's capabilities, upper-case, as it listed them once logged in.
    fn capabilities(&self) -> Vec<String>;
    fn select(&mut self, folder: &str, readonly: bool) -> Result<Selected, ServerError>;
    /// The UIDs a search found, in order.
    fn search(&mut self, criteria: Search<'_>) -> Result<Vec<u32>, ServerError>;
    fn flags(&mut self, uids: &[u32]) -> Result<BTreeMap<u32, Vec<String>>, ServerError>;
    /// Before any select: whether the server keeps mod-sequences for this connection from now on.
    fn enable_condstore(&mut self) -> Result<bool, ServerError>;
    /// These header `fields` (upper-case names, as `HEADER_FIELDS`) of each message, in UID order. `gmail` asks for
    /// X-GM-THRID and X-GM-LABELS too.
    fn headers(&mut self, uids: &[u32], fields: &[&str], gmail: bool) -> Result<Vec<Headers>, ServerError>;
    /// A whole message, read without marking it read (BODY.PEEK[]).
    fn body(&mut self, uid: u32) -> Result<Option<Vec<u8>>, ServerError>;
    /// The first `bytes` of each message, read without marking it read (`BODY.PEEK[]<0.bytes>`), by UID: a message
    /// no longer than that whole, and one the server sent nothing of left out. A server that can't send part of one
    /// sends it whole, cut here. One the server refuses to send is left out too, and the others kept: only a lost
    /// connection fails it.
    fn heads(&mut self, uids: &[u32], bytes: u32) -> Result<BTreeMap<u32, Vec<u8>>, ServerError> {
        let mut out = BTreeMap::new();
        for uid in uids {
            match self.body(*uid) {
                Ok(Some(mut raw)) => {
                    raw.truncate(usize::try_from(bytes).unwrap_or(usize::MAX));
                    out.insert(*uid, raw);
                }
                Err(e) if e.is_unreachable() => return Err(e),
                Ok(None) | Err(_) => {}
            }
        }
        Ok(out)
    }
    /// The first `bytes` of each message with its whole size (`RFC822.SIZE BODY.PEEK[]<0.bytes>`), read without
    /// marking it read, by UID, as `heads` reads them: one the server sent nothing of, or refuses to send, is left
    /// out, and only a lost connection fails it.
    fn openings(&mut self, uids: &[u32], bytes: u32) -> Result<BTreeMap<u32, Opening>, ServerError> {
        let mut out = BTreeMap::new();
        for uid in uids {
            match self.body(*uid) {
                Ok(Some(mut raw)) => {
                    let size = u32::try_from(raw.len()).ok();
                    raw.truncate(usize::try_from(bytes).unwrap_or(usize::MAX));
                    out.insert(*uid, Opening { raw, size });
                }
                Err(e) if e.is_unreachable() => return Err(e),
                Ok(None) | Err(_) => {}
            }
        }
        Ok(out)
    }
    /// Whole messages, read without marking them read (BODY.PEEK[]), several a FETCH, by UID: one the server sent
    /// nothing of, or refuses to send, is left out, and only a lost connection fails it.
    fn bodies(&mut self, uids: &[u32]) -> Result<BTreeMap<u32, Vec<u8>>, ServerError> {
        let mut out = BTreeMap::new();
        for uid in uids {
            match self.body(*uid) {
                Ok(Some(raw)) => {
                    out.insert(*uid, raw);
                }
                Err(e) if e.is_unreachable() => return Err(e),
                Ok(None) | Err(_) => {}
            }
        }
        Ok(out)
    }
    /// When each message arrived, as the server says (INTERNALDATE), in Unix seconds: one whose date doesn't read
    /// is left out.
    fn arrivals(&mut self, uids: &[u32]) -> Result<BTreeMap<u32, i64>, ServerError>;
    fn store_flags(&mut self, uids: &[u32], add: bool, flags: &[&str]) -> Result<(), ServerError>;
    /// Gmail's X-GM-LABELS.
    fn store_labels(&mut self, uids: &[u32], add: bool, labels: &[&str]) -> Result<(), ServerError>;
    fn copy(&mut self, uids: &[u32], dest: &str) -> Result<(), ServerError>;
    /// One UID MOVE for them all, as the server offers it: `move_to` is what a use case calls.
    fn uid_move(&mut self, uids: &[u32], dest: &str) -> Result<(), ServerError>;
    /// UID EXPUNGE of these alone, never a bulk one.
    fn expunge(&mut self, uids: &[u32]) -> Result<(), ServerError>;
    /// Its UID, when the server says.
    fn append(
        &mut self,
        folder: &str,
        raw: &[u8],
        flags: &[&str],
        when: Option<&str>,
    ) -> Result<Option<u32>, ServerError>;
    /// Close without a LOGOUT, when the server won't answer one.
    fn shutdown(&mut self);

    // A folder itself, as a person makes, renames or deletes one: each name in LIST's form, as the rest. A session
    // that can't says so as a refusal.
    fn create_folder(&mut self, _name: &str) -> Result<(), ServerError> {
        Err(ServerError::Refused("Unsupported"))
    }
    fn rename_folder(&mut self, _name: &str, _to: &str) -> Result<(), ServerError> {
        Err(ServerError::Refused("Unsupported"))
    }
    /// The caller selects another folder first: a server may refuse to delete the selected one.
    fn delete_folder(&mut self, _name: &str) -> Result<(), ServerError> {
        Err(ServerError::Refused("Unsupported"))
    }

    /// Whether the connection still answers (NOOP): a connection kept between jobs is asked before each.
    fn noop(&mut self) -> Result<(), ServerError> {
        Ok(())
    }
    /// From now on wait at most `seconds` for each answer: a connection kept between jobs waits as each one asks.
    fn set_timeout(&mut self, _seconds: u64) {}
}

/// Where servers' sessions come from.
pub trait MailServers: Send + Sync + fmt::Debug {
    /// A logged-in session on `account`'s server, each answer waited for at most `timeout_seconds`.
    fn open(&self, account: &str, timeout_seconds: u64) -> Result<Box<dyn MailServer>, ServerError>;
}

/// The newest UID in the selected folder whose Message-ID is `message_id`; None when there is none or there is no
/// Message-ID to go by.
pub fn by_message_id(server: &mut dyn MailServer, message_id: &str) -> Result<Option<u32>, ServerError> {
    if message_id.is_empty() {
        return Ok(None);
    }
    Ok(server.search(Search::MessageId(message_id))?.into_iter().max())
}

/// The same, a refused search finding nothing; a lost connection still fails.
pub fn found_by_message_id(server: &mut dyn MailServer, message_id: &str) -> Result<Option<u32>, ServerError> {
    match by_message_id(server, message_id) {
        Err(ServerError::Refused(_)) => Ok(None),
        other => other,
    }
}

/// MOVE on a server without it: COPY, checked, then \Deleted and a UID EXPUNGE of each UID alone, never a bulk
/// EXPUNGE. A refused COPY fails before an original is touched. A server without UIDPLUS refuses the UID EXPUNGE and
/// leaves the original flagged \Deleted, as any client without UIDPLUS deletes; the next expunge finishes it.
pub fn move_by_copy(server: &mut dyn MailServer, uids: &[u32], dest: &str) -> Result<(), ServerError> {
    server.copy(uids, dest)?;
    server.store_flags(uids, true, &["\\Deleted"])?;
    for uid in uids {
        match server.expunge(&[*uid]) {
            Err(ServerError::Unreachable(class)) => return Err(ServerError::Unreachable(class)),
            _ => continue,
        }
    }
    Ok(())
}

/// Whether a server with these capabilities takes a UID MOVE.
pub fn offers_move(capabilities: &[String]) -> bool {
    capabilities.iter().any(|c| c == "MOVE")
}

/// Move the UIDs to `dest`: one MOVE where the server offers it, by copy where it doesn't or refuses it.
pub fn move_to(server: &mut dyn MailServer, uids: &[u32], dest: &str) -> Result<(), ServerError> {
    if offers_move(&server.capabilities()) {
        match server.uid_move(uids, dest) {
            Ok(()) => return Ok(()),
            Err(ServerError::Refused(_)) => {}
            Err(other) => return Err(other),
        }
    }
    move_by_copy(server, uids, dest)
}

/// The folder playing the first of `wanted` that one plays, or None: when none does, and when the server refuses
/// the LIST, so the caller falls back to its default name. A lost connection fails.
pub fn find_role(server: &mut dyn MailServer, wanted: &[&str]) -> Result<Option<String>, ServerError> {
    let folders: Vec<Folder> = match server.folders() {
        Ok(folders) => folders,
        Err(SessionError::Unreachable(class)) => return Err(ServerError::Unreachable(class)),
        Err(SessionError::Failed(_)) => return Ok(None),
    };
    Ok(roles::find(&folders, wanted).map(str::to_owned))
}

/// The account's Trash on the server (`roles::trash`): `named` when the server lists it, else the folder that plays
/// Trash there; `named` when the server refuses the LIST. A lost connection fails.
pub fn find_trash(server: &mut dyn MailServer, named: &str) -> Result<String, ServerError> {
    let folders: Vec<Folder> = match server.folders() {
        Ok(folders) => folders,
        Err(SessionError::Unreachable(class)) => return Err(ServerError::Unreachable(class)),
        Err(SessionError::Failed(_)) => return Ok(named.to_owned()),
    };
    Ok(roles::trash(&folders, named).to_owned())
}

/// A read-only process's session: every folder opens read-only, and a write is refused before it is sent.
pub struct ReadOnly(pub Box<dyn MailServer>);

impl fmt::Debug for ReadOnly {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("ReadOnly(..)")
    }
}

const READ_ONLY: ServerError = ServerError::Unreachable("ReadOnlyError");

impl MailSession for ReadOnly {
    fn folders(&mut self) -> Result<Vec<Folder>, SessionError> {
        self.0.folders()
    }

    fn status(&mut self, folder: &str) -> Result<(i64, i64), SessionError> {
        self.0.status(folder)
    }

    fn logout(&mut self) {
        self.0.logout();
    }
}

impl MailServer for ReadOnly {
    fn capabilities(&self) -> Vec<String> {
        self.0.capabilities()
    }

    fn select(&mut self, folder: &str, _readonly: bool) -> Result<Selected, ServerError> {
        self.0.select(folder, true)
    }

    fn search(&mut self, criteria: Search<'_>) -> Result<Vec<u32>, ServerError> {
        self.0.search(criteria)
    }

    fn flags(&mut self, uids: &[u32]) -> Result<BTreeMap<u32, Vec<String>>, ServerError> {
        self.0.flags(uids)
    }

    fn enable_condstore(&mut self) -> Result<bool, ServerError> {
        self.0.enable_condstore()
    }

    fn headers(&mut self, uids: &[u32], fields: &[&str], gmail: bool) -> Result<Vec<Headers>, ServerError> {
        self.0.headers(uids, fields, gmail)
    }

    fn body(&mut self, uid: u32) -> Result<Option<Vec<u8>>, ServerError> {
        self.0.body(uid)
    }

    fn heads(&mut self, uids: &[u32], bytes: u32) -> Result<BTreeMap<u32, Vec<u8>>, ServerError> {
        self.0.heads(uids, bytes)
    }

    fn openings(&mut self, uids: &[u32], bytes: u32) -> Result<BTreeMap<u32, Opening>, ServerError> {
        self.0.openings(uids, bytes)
    }

    fn bodies(&mut self, uids: &[u32]) -> Result<BTreeMap<u32, Vec<u8>>, ServerError> {
        self.0.bodies(uids)
    }

    fn arrivals(&mut self, uids: &[u32]) -> Result<BTreeMap<u32, i64>, ServerError> {
        self.0.arrivals(uids)
    }

    fn store_flags(&mut self, _: &[u32], _: bool, _: &[&str]) -> Result<(), ServerError> {
        Err(READ_ONLY)
    }

    fn store_labels(&mut self, _: &[u32], _: bool, _: &[&str]) -> Result<(), ServerError> {
        Err(READ_ONLY)
    }

    fn copy(&mut self, _: &[u32], _: &str) -> Result<(), ServerError> {
        Err(READ_ONLY)
    }

    fn uid_move(&mut self, _: &[u32], _: &str) -> Result<(), ServerError> {
        Err(READ_ONLY)
    }

    fn expunge(&mut self, _: &[u32]) -> Result<(), ServerError> {
        Err(READ_ONLY)
    }

    fn append(&mut self, _: &str, _: &[u8], _: &[&str], _: Option<&str>) -> Result<Option<u32>, ServerError> {
        Err(READ_ONLY)
    }

    fn shutdown(&mut self) {
        self.0.shutdown();
    }

    fn create_folder(&mut self, _: &str) -> Result<(), ServerError> {
        Err(READ_ONLY)
    }

    fn rename_folder(&mut self, _: &str, _: &str) -> Result<(), ServerError> {
        Err(READ_ONLY)
    }

    fn delete_folder(&mut self, _: &str) -> Result<(), ServerError> {
        Err(READ_ONLY)
    }

    fn noop(&mut self) -> Result<(), ServerError> {
        self.0.noop()
    }

    fn set_timeout(&mut self, seconds: u64) {
        self.0.set_timeout(seconds);
    }
}

//! A message's body from its server where it isn't stored (`Ingest.fetch_body`, as `bodies.message_body` runs it):
//! on its account's gate, as a person waits on it, or as a prefetch that gives way at once to anything holding the
//! gate. A body fetched is kept with what opening it records, so the next open reads it from the store.
//!
//! A body that is stored is read without the gate: only the server is the gate's. One that isn't is looked for again
//! once the gate is had, since the job before may have fetched it.
//!
//! The body backfill fetches a folder's few messages at a time, as a prefetch gives way, and is told a lost connection
//! apart from the rest (`try_bodies`). Once it has the gate it looks at each again, as a fetch does: one opened
//! meanwhile has its body, and one moved or removed is no longer where the round found it, so neither is asked for. It
//! asks for their openings several a FETCH, with each one's whole size: an
//! opening that holds the whole message is kept as its body; one cut off is fetched whole, several a FETCH too, where
//! the message is at most `WHOLE_MOST`; and a larger message keeps only its opening's text, which search indexes, so
//! its attachments are fetched when it is opened.

use std::collections::HashMap;
use std::sync::Arc;

use mm_kernel::errors::Fault;
use mm_kernel::seams::Clock;
use mm_kernel::when;
use serde_json::Value;

use crate::backfill_ports::{BackfillFetch, Tried};
use crate::fetch_ports::{KeptBodies, KeptBody, KeptText};
use crate::folders::Folders;
use crate::gates::{Gates, Priority, Refused, Wait, on_gate};
use crate::ports::{BodyFetch, Row, StoredBodies};
use crate::session::{MailServer, MailServers, ServerError, select_or_gone};

/// What a session waits for each answer (`pool.KEPT_TIMEOUT`, as a fetch opens one).
const TIMEOUT_SECONDS: u64 = 60;
/// The first bytes of each message the body backfill reads, as a sync reads them.
pub const OPENING_BYTES: u32 = 65_536;
/// The largest message the body backfill keeps whole, in bytes; a larger one waits to be opened for its attachments.
pub const WHOLE_MOST: u32 = 262_144;
/// About how many bytes of whole messages one FETCH asks for.
pub const FETCH_BYTES: u32 = 2_097_152;

/// The UIDs of `sized` (UID, size) in groups of at most `most` bytes, in order: one larger than that alone.
fn by_bytes(sized: &[(u32, u32)], most: u32) -> Vec<Vec<u32>> {
    let mut groups: Vec<Vec<u32>> = Vec::new();
    let mut bytes = 0u32;
    for (uid, size) in sized {
        match groups.last_mut() {
            Some(group) if bytes.saturating_add(*size) <= most => {
                group.push(*uid);
                bytes = bytes.saturating_add(*size);
            }
            _ => {
                groups.push(vec![*uid]);
                bytes = *size;
            }
        }
    }
    groups
}

#[derive(Debug)]
pub struct Fetching {
    stored: Arc<dyn StoredBodies>,
    kept: Arc<dyn KeptBodies>,
    servers: Arc<dyn MailServers>,
    gates: Arc<dyn Gates>,
    clock: Arc<dyn Clock>,
    /// What forgets a folder the server no longer has, where a message a person opens finds it gone.
    folders: Option<Arc<Folders>>,
}

/// What wires Fetching.
#[derive(Debug)]
pub struct FetchingParts {
    pub stored: Arc<dyn StoredBodies>,
    pub kept: Arc<dyn KeptBodies>,
    pub servers: Arc<dyn MailServers>,
    pub gates: Arc<dyn Gates>,
    pub clock: Arc<dyn Clock>,
}

fn text<'r>(row: &'r Row, name: &str) -> &'r str {
    row.get(name).and_then(Value::as_str).unwrap_or_default()
}

fn fault(error: ServerError) -> Fault {
    match error {
        ServerError::Unreachable(class) | ServerError::Refused(class) => Fault::new(class),
        ServerError::Failed(fault) => fault,
    }
}

fn gate_fault(refused: Refused) -> Fault {
    match refused {
        Refused::Busy => Fault::new("Busy"),
        Refused::Nested => Fault::new("NestedGate"),
    }
}

impl Fetching {
    pub fn new(parts: FetchingParts) -> Fetching {
        let FetchingParts { stored, kept, servers, gates, clock } = parts;
        Fetching { stored, kept, servers, gates, clock, folders: None }
    }

    /// The same, forgetting a message's folder that its server no longer has as the message is opened: its body is
    /// then the `NO_SUCH_FOLDER` fault.
    #[must_use]
    pub fn forgetting_gone(mut self, folders: Arc<Folders>) -> Fetching {
        self.folders = Some(folders);
        self
    }

    /// The body as stored, its parts kept again where a parsing change took them away; None when it isn't stored.
    fn as_stored(&self, pk: i64, row: &Row) -> Result<Option<Vec<u8>>, Fault> {
        let fetched = row.get("body_fetched").and_then(Value::as_i64).is_some_and(|n| n != 0);
        let raw = if fetched { self.stored.raw(pk)?.unwrap_or_default() } else { Vec::new() };
        if raw.is_empty() {
            return Ok(None);
        }
        let parts = mm_content::mime::parse(&raw).attachments;
        if !parts.is_empty() && !self.stored.has_parts(pk)? {
            self.stored.keep_parts(pk, &parts)?;
        }
        Ok(Some(raw))
    }

    /// The message's whole body from its server, read-only and without marking it read; empty when the server has
    /// none, or refuses to give it. A folder the server won't select fails, as a lost connection does; one it no
    /// longer has is forgotten first, where this forgets them.
    fn read_server(&self, row: &Row) -> Result<Vec<u8>, ServerError> {
        let Some(uid) = row.get("uid").and_then(Value::as_i64).and_then(|uid| u32::try_from(uid).ok()) else {
            return Ok(Vec::new());
        };
        let (account, folder) = (text(row, "account"), text(row, "folder"));
        let mut server = self.servers.open(account, TIMEOUT_SECONDS)?;
        let read = select_or_gone(&mut *server, folder, true).and_then(|_| match server.body(uid) {
            Err(ServerError::Refused(_)) => Ok(None),
            other => other,
        });
        server.logout();
        if let (Err(error), Some(folders)) = (&read, &self.folders)
            && error.is_gone()
        {
            folders.gone(account, folder).map_err(ServerError::Failed)?;
        }
        Ok(read?.unwrap_or_default())
    }

    /// `fetch_body` with the gate held: the body as stored, or fetched and kept. What went wrong, as the server said
    /// it, or the store's fault as `Failed`.
    fn fetch_held(&self, pk: i64) -> Result<Option<Vec<u8>>, ServerError> {
        let Some(row) = self.stored.message(pk).map_err(ServerError::Failed)? else { return Ok(None) };
        if let Some(raw) = self.as_stored(pk, &row).map_err(ServerError::Failed)? {
            return Ok(Some(raw));
        }
        let raw = self.read_server(&row)?;
        if raw.is_empty() {
            return Ok(Some(raw));
        }
        let body = KeptBody::of(raw, when::iso(self.clock.now()));
        self.kept.keep_body(pk, &body).map_err(ServerError::Failed)?;
        Ok(Some(body.raw))
    }

    fn fetch_body(&self, pk: i64) -> Result<Option<Vec<u8>>, Fault> {
        self.fetch_held(pk).map_err(fault)
    }

    /// Of `messages`, those to ask for now the gate is held: each still `account`'s at its UID in `folder`, not on its
    /// way out, with no body or text kept yet. One opened since the round found it has its body, and one moved,
    /// removed or in a folder renamed meanwhile is no longer there.
    fn still_wanted(&self, account: &str, folder: &str, messages: &[(i64, u32)]) -> Result<Vec<(i64, u32)>, Fault> {
        let mut wanted = Vec::with_capacity(messages.len());
        for &(pk, uid) in messages {
            let Some(row) = self.stored.message(pk)? else { continue };
            let there = text(&row, "account") == account
                && text(&row, "folder") == folder
                && row.get("uid").and_then(Value::as_i64) == Some(i64::from(uid))
                && row.get("pending_removal").is_none_or(Value::is_null);
            let without_text = row.get("body_text").is_none_or(Value::is_null);
            if there && without_text && self.as_stored(pk, &row)?.is_none() {
                wanted.push((pk, uid));
            }
        }
        Ok(wanted)
    }

    /// `try_bodies` with the gate held, on the session its account's jobs share.
    fn bodies_held(&self, account: &str, folder: &str, messages: &[(i64, u32)]) -> Result<(), ServerError> {
        let messages = self.still_wanted(account, folder, messages).map_err(ServerError::Failed)?;
        if messages.is_empty() {
            return Ok(());
        }
        let mut server = self.servers.open(account, TIMEOUT_SECONDS)?;
        let read = self.read_bodies(&mut *server, folder, &messages);
        match read {
            Err(ServerError::Unreachable(_)) => server.shutdown(),
            _ => server.logout(),
        }
        read
    }

    /// Each message's opening, then the whole of those cut off but small enough, each kept as it comes: what was
    /// kept stays kept, however the rest goes. A folder the server won't select keeps nothing.
    fn read_bodies(&self, s: &mut dyn MailServer, folder: &str, messages: &[(i64, u32)]) -> Result<(), ServerError> {
        s.select(folder, true)?;
        let pk_of: HashMap<u32, i64> = messages.iter().map(|(pk, uid)| (*uid, *pk)).collect();
        let uids: Vec<u32> = messages.iter().map(|(_, uid)| *uid).collect();
        let when = when::iso(self.clock.now());
        let keep = |pk: i64, raw: Vec<u8>| self.kept.keep_body(pk, &KeptBody::of(raw, when.clone()));
        let mut whole = Vec::new();
        for (uid, opening) in s.openings(&uids, OPENING_BYTES)? {
            let Some(pk) = pk_of.get(&uid).copied() else { continue };
            if opening.is_whole(OPENING_BYTES) {
                keep(pk, opening.raw).map_err(ServerError::Failed)?;
            } else if let Some(size) = opening.size.filter(|size| *size <= WHOLE_MOST) {
                whole.push((uid, size));
            } else {
                self.kept.keep_text(pk, &KeptText::of(&opening.raw)).map_err(ServerError::Failed)?;
            }
        }
        for group in by_bytes(&whole, FETCH_BYTES) {
            for (uid, raw) in s.bodies(&group)? {
                if let Some(pk) = pk_of.get(&uid).copied() {
                    keep(pk, raw).map_err(ServerError::Failed)?;
                }
            }
        }
        Ok(())
    }
}

impl BodyFetch for Fetching {
    fn fetch(&self, pk: i64) -> Result<Option<Vec<u8>>, Fault> {
        let Some(row) = self.stored.message(pk)? else { return Ok(None) };
        if let Some(raw) = self.as_stored(pk, &row)? {
            return Ok(Some(raw));
        }
        let account = text(&row, "account");
        on_gate(&*self.gates, account, Priority::Interactive, Wait::Ever, || self.fetch_body(pk)).map_err(gate_fault)?
    }

    fn prefetch(&self, pk: i64) -> Result<bool, Fault> {
        let Some(row) = self.stored.message(pk)? else { return Ok(false) };
        match on_gate(&*self.gates, text(&row, "account"), Priority::Maintenance, Wait::Not, || self.fetch_body(pk)) {
            // It gave way: nothing went wrong, and the open will fetch it.
            Err(Refused::Busy) => Ok(false),
            Err(refused) => Err(gate_fault(refused)),
            Ok(fetched) => fetched.map(|_| true),
        }
    }
}

impl BackfillFetch for Fetching {
    /// `gates.run(account, fetch_bodies, MAINTENANCE, wait=False)`: a held gate is Busy at once, and a lost connection
    /// is told apart from everything else that can go wrong.
    fn try_bodies(&self, account: &str, folder: &str, messages: &[(i64, u32)]) -> Tried {
        let fetch = || self.bodies_held(account, folder, messages);
        match on_gate(&*self.gates, account, Priority::Maintenance, Wait::Not, fetch) {
            // Busy, or a wait from inside a job, which no lane is.
            Err(_) => Tried::Busy,
            Ok(Err(ServerError::Unreachable(_))) => Tried::Unreachable,
            Ok(_) => Tried::Ran,
        }
    }
}

//! A message's body from its server where it isn't stored (`Ingest.fetch_body`, as `bodies.message_body` runs it):
//! on its account's gate, as a person waits on it, or as a prefetch that gives way at once to anything holding the
//! gate. A body fetched is kept with what opening it records, so the next open reads it from the store.
//!
//! A body that is stored is read without the gate: only the server is the gate's. One that isn't is looked for again
//! once the gate is had, since the job before may have fetched it.
//!
//! The body backfill fetches as a prefetch does, and is told a lost connection apart from the rest (`try_body`).

use std::sync::Arc;

use mm_kernel::errors::Fault;
use mm_kernel::seams::Clock;
use mm_kernel::when;
use serde_json::Value;

use crate::backfill_ports::{BackfillFetch, Tried};
use crate::fetch_ports::{KeptBodies, KeptBody};
use crate::gates::{Gates, Priority, Refused, Wait, on_gate};
use crate::ports::{BodyFetch, Row, StoredBodies};
use crate::session::{MailServers, ServerError};

/// What a session waits for each answer (`pool.KEPT_TIMEOUT`, as a fetch opens one).
const TIMEOUT_SECONDS: u64 = 60;

#[derive(Debug)]
pub struct Fetching {
    stored: Arc<dyn StoredBodies>,
    kept: Arc<dyn KeptBodies>,
    servers: Arc<dyn MailServers>,
    gates: Arc<dyn Gates>,
    clock: Arc<dyn Clock>,
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
        Fetching { stored, kept, servers, gates, clock }
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
    /// none, or refuses to give it. A folder the server won't select fails, as a lost connection does.
    fn read_server(&self, row: &Row) -> Result<Vec<u8>, ServerError> {
        let Some(uid) = row.get("uid").and_then(Value::as_i64).and_then(|uid| u32::try_from(uid).ok()) else {
            return Ok(Vec::new());
        };
        let mut server = self.servers.open(text(row, "account"), TIMEOUT_SECONDS)?;
        let read = server.select(text(row, "folder"), true).and_then(|_| match server.body(uid) {
            Err(ServerError::Refused(_)) => Ok(None),
            other => other,
        });
        server.logout();
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
    /// `gates.run(account, fetch_body, MAINTENANCE, wait=False)`: a held gate is Busy at once, and a lost connection is
    /// told apart from everything else that can go wrong.
    fn try_body(&self, account: &str, pk: i64) -> Tried {
        match on_gate(&*self.gates, account, Priority::Maintenance, Wait::Not, || self.fetch_held(pk)) {
            // Busy, or a wait from inside a job, which no lane is.
            Err(_) => Tried::Busy,
            Ok(Err(ServerError::Unreachable(_))) => Tried::Unreachable,
            Ok(_) => Tried::Ran,
        }
    }
}

//! A sender's cleanup, its unsubscribe, a sweep and expiry bound to their adapters (`bootstrap.curation`'s `_Filing`,
//! `_ListHeaders`, `_Mailboxes`, `_SafeHttp` and `_SystemSend`): the filing the Screener files through, the Mailbox's
//! servers, each account's gate, the confirm tokens under this run's secret, and Curation's store.
//!
//! The two ways an unsubscribe leaves this machine aren't here yet: the one-click POST wants an HTTPS client, and a
//! mailto: wants the outbox. Each says it isn't ready, so a cleanup that would use one answers as not yet served
//! before it changes anything.

use std::collections::BTreeMap;
use std::fmt::Write as _;
use std::sync::Arc;
use std::time::Duration;

use mm_accounts::Directory;
use mm_curation::cleaning::{Cleaning, CleaningParts};
use mm_curation::cleanup_ports::{
    ConfirmTokens, Ids, ListHeaders, LiveError, LiveInboxes, LiveSession, SafeHttp, SweptAccount, SystemSend, UnsubRow,
};
use mm_curation::expiry::{Expiry, ExpiryParts};
use mm_curation::sweeping::{Sweeps, SweepsParts};
use mm_curation::unsubscribe::{Unsubscribing, UnsubscribingParts};
use mm_kernel::errors::Fault;
use mm_kernel::log::{self, Field};
use mm_kernel::seams::Clock;
use mm_mailbox::command_ports::Tokens;
use mm_mailbox::gates::{Gates, Priority, Refused, Wait, on_gate};
use mm_mailbox::session::{self, MailServer, MailServers, Search, ServerError};
use mm_store::Database;
use mm_store::curation::CurationStore;
use serde_json::Value;
use sha2::{Digest, Sha256};

use crate::command_wiring::RandomTokens;

const TAG: &str = "cleanup";
/// How long a preview waits its turn on the account's gate to read one message's headers (`UNSUB_READ_WAIT`).
const UNSUB_READ_WAIT: Duration = Duration::from_secs(5);
/// How long a sweep's session waits on each answer from a server.
const SESSION_TIMEOUT: u64 = 60;

/// One stored message's unsubscribe headers, asked of its server on its account's gate, waiting a moment at most.
#[derive(Debug)]
pub(crate) struct ServerListHeaders {
    pub(crate) servers: Arc<dyn MailServers>,
    pub(crate) gates: Arc<dyn Gates>,
    pub(crate) directory: Arc<Directory>,
}

impl ServerListHeaders {
    fn read_one(&self, row: &UnsubRow, uid: u32) -> Result<Option<Vec<u8>>, ServerError> {
        let mut server = self.servers.open(&row.account, SESSION_TIMEOUT)?;
        let read = server
            .select(&row.folder, true)
            .and_then(|_| server.headers(&[uid], &["LIST-UNSUBSCRIBE", "LIST-UNSUBSCRIBE-POST"], false));
        server.logout();
        Ok(read?.into_iter().next().map(|h| h.raw))
    }
}

impl ListHeaders for ServerListHeaders {
    fn read(&self, row: &UnsubRow) -> Option<Vec<u8>> {
        let known = self.directory.get(&row.account).ok().flatten().is_some();
        let uid = row.uid.and_then(|uid| u32::try_from(uid).ok())?;
        if !known {
            return None;
        }
        let read = on_gate(&*self.gates, &row.account, Priority::Interactive, Wait::For(UNSUB_READ_WAIT), || {
            self.read_one(row, uid)
        });
        match read {
            Ok(Ok(raw)) => raw,
            // A preview still works: it offers no unsubscribe.
            Ok(Err(e)) => {
                log::warning(TAG, "header read failed", &[(Field::Why, e.class())], None);
                None
            }
            Err(_) => {
                log::warning(TAG, "header read failed", &[(Field::Why, "Busy")], None);
                None
            }
        }
    }
}

/// RFC 8058's one-click POST, which wants an HTTPS client this server doesn't have yet.
#[derive(Debug)]
pub(crate) struct NoHttps;

impl SafeHttp for NoHttps {
    fn ready(&self) -> bool {
        false
    }

    fn one_click_post(&self, _url: &str) -> (bool, Option<i64>) {
        (false, None)
    }
}

/// A message MailMind sends for itself goes through the outbox, which isn't on this server yet.
#[derive(Debug)]
pub(crate) struct NoOutbox;

impl SystemSend for NoOutbox {
    fn ready(&self) -> bool {
        false
    }

    fn send(&self, _: &str, _: &[u8], _: &str, _: &str, _: &str, _: &str) -> Result<(), Fault> {
        Err(mm_kernel::errors::NOT_YET)
    }
}

/// HMAC-SHA256 (RFC 2104).
fn hmac_sha256(key: &[u8], message: &[u8]) -> [u8; 32] {
    const BLOCK: usize = 64;
    let mut block = [0u8; BLOCK];
    if key.len() > BLOCK {
        block[..32].copy_from_slice(&Sha256::digest(key));
    } else {
        block[..key.len()].copy_from_slice(key);
    }
    let pad = |with: u8| block.map(|b| b ^ with);
    let inner = Sha256::new().chain_update(pad(0x36)).chain_update(message).finalize();
    Sha256::new().chain_update(pad(0x5c)).chain_update(inner).finalize().into()
}

/// Confirm tokens (`perimeter.tokens`) under this run's secret, the page's CSRF token: a restart ends every one.
#[derive(Debug)]
pub(crate) struct RunTokens {
    pub(crate) secret: String,
}

impl RunTokens {
    /// The first 16 hex digits of the HMAC over `json.dumps([purpose, *subject, ts])`.
    fn mac(&self, purpose: &str, subject: &[Option<String>], ts: i64) -> String {
        let mut said = vec![Value::from(purpose)];
        said.extend(subject.iter().map(|s| s.clone().map_or(Value::Null, Value::from)));
        said.push(Value::from(ts));
        let mac = hmac_sha256(self.secret.as_bytes(), crate::json::dumps(&Value::Array(said)).as_bytes());
        mac.iter().take(8).fold(String::with_capacity(16), |mut out, b| {
            let _ = write!(out, "{b:02x}");
            out
        })
    }
}

impl ConfirmTokens for RunTokens {
    fn mint(&self, purpose: &str, subject: &[Option<String>], now: i64) -> String {
        format!("{now}.{}", self.mac(purpose, subject, now))
    }

    fn names(&self, token: &str, purpose: &str, subject: &[Option<String>]) -> bool {
        let (ts, mac) = token.split_once('.').unwrap_or((token, ""));
        let Ok(ts) = ts.parse::<i64>() else { return false };
        let want = self.mac(purpose, subject, ts);
        // Compared whole, whatever differs first.
        want.len() == mac.len() && want.bytes().zip(mac.bytes()).fold(0u8, |d, (a, b)| d | (a ^ b)) == 0
    }
}

/// Names no one can guess, from the operating system's random source.
#[derive(Debug, Default)]
pub(crate) struct RandomIds(RandomTokens);

impl Ids for RandomIds {
    fn hex(&self, n: usize) -> String {
        self.0.hex(n)
    }
}

fn live(error: ServerError) -> LiveError {
    match error {
        ServerError::Refused(class) => LiveError::Refused(class),
        ServerError::Unreachable(class) => LiveError::Lost(class),
        ServerError::Failed(fault) => LiveError::Lost(fault.class),
    }
}

/// A session on a server, as a sweep reads and moves an INBOX through it.
struct ServerSession(Box<dyn MailServer>);

impl LiveSession for ServerSession {
    fn select(&mut self, folder: &str, readonly: bool) -> Result<(), LiveError> {
        self.0.select(folder, readonly).map(|_| ()).map_err(live)
    }

    fn uids(&mut self) -> Result<Vec<u32>, LiveError> {
        self.0.search(Search::All).map_err(live)
    }

    fn uids_from(&mut self, text: &str) -> Result<Vec<u32>, LiveError> {
        self.0.search(Search::From(text)).map_err(live)
    }

    fn headers(&mut self, uids: &[u32], fields: &[&str]) -> Result<Vec<(u32, Vec<u8>)>, LiveError> {
        Ok(self.0.headers(uids, fields, false).map_err(live)?.into_iter().map(|h| (h.uid, h.raw)).collect())
    }

    fn arrivals(&mut self, uids: &[u32]) -> Result<BTreeMap<u32, i64>, LiveError> {
        self.0.arrivals(uids).map_err(live)
    }

    fn move_to(&mut self, uids: &[u32], dest: &str) -> Result<(), LiveError> {
        session::move_to(&mut *self.0, uids, dest).map_err(live)
    }

    fn close(&mut self, failed: bool) {
        if failed { self.0.shutdown() } else { self.0.logout() }
    }
}

/// The accounts a sweep goes over, each one's server, and its gate.
#[derive(Debug)]
pub(crate) struct AccountInboxes {
    pub(crate) directory: Arc<Directory>,
    pub(crate) servers: Arc<dyn MailServers>,
    pub(crate) gates: Arc<dyn Gates>,
}

impl LiveInboxes for AccountInboxes {
    fn accounts(&self) -> Result<Vec<SweptAccount>, Fault> {
        Ok(self
            .directory
            .accounts()?
            .into_iter()
            .map(|a| SweptAccount { email: a.email, kind: a.kind, trash: a.trash })
            .collect())
    }

    fn on_gate(&self, account: &str, job: &mut dyn FnMut() -> Result<Value, Fault>) -> Result<Value, Fault> {
        match on_gate(&*self.gates, account, Priority::Interactive, Wait::Ever, job) {
            Ok(done) => done,
            Err(Refused::Busy) => Err(Fault::new("Busy")),
            Err(Refused::Nested) => Err(Fault::new("Nested")),
        }
    }

    fn on_gate_unless_held(
        &self,
        account: &str,
        job: &mut dyn FnMut() -> Result<Value, Fault>,
    ) -> Result<Option<Value>, Fault> {
        match on_gate(&*self.gates, account, Priority::Maintenance, Wait::Not, job) {
            Ok(done) => done.map(Some),
            Err(Refused::Busy) => Ok(None),
            Err(Refused::Nested) => Err(Fault::new("Nested")),
        }
    }

    fn open(&self, account: &str) -> Result<Box<dyn LiveSession>, &'static str> {
        let server = self.servers.open(account, SESSION_TIMEOUT).map_err(ServerError::class)?;
        Ok(Box::new(ServerSession(server)))
    }
}

/// A sender's cleanup, a sweep and expiry, bound.
#[derive(Debug)]
pub struct Cleanups {
    pub cleaning: Cleaning,
    pub sweeps: Sweeps,
    pub expiry: Expiry,
}

/// What `Cleanups` is made of.
pub(crate) struct CleanupsParts {
    pub(crate) database: Arc<Database>,
    pub(crate) directory: Arc<Directory>,
    pub(crate) servers: Arc<dyn MailServers>,
    pub(crate) gates: Arc<dyn Gates>,
    /// What Curation asks of the Mailbox's filing, as the Screener asks it.
    pub(crate) filing: Arc<dyn mm_curation::ports::Filing>,
    pub(crate) clock: Arc<dyn Clock>,
    /// This run's secret, which the confirm tokens are keyed with.
    pub(crate) secret: String,
    pub(crate) read_only: bool,
}

impl Cleanups {
    pub(crate) fn assemble(parts: CleanupsParts) -> Cleanups {
        let CleanupsParts { database, directory, servers, gates, filing, clock, secret, read_only } = parts;
        let store = Arc::new(CurationStore::new(database, clock.clone()));
        let ids: Arc<dyn Ids> = Arc::new(RandomIds::default());
        let unsubscribing = Unsubscribing::new(UnsubscribingParts {
            mail: store.clone(),
            headers: Arc::new(ServerListHeaders {
                servers: servers.clone(),
                gates: gates.clone(),
                directory: directory.clone(),
            }),
            http: Arc::new(NoHttps),
            send: Arc::new(NoOutbox),
            ids: ids.clone(),
            clock: clock.clone(),
            writes_allowed: !read_only,
        });
        let boxes = Arc::new(AccountInboxes { directory, servers, gates });
        Cleanups {
            expiry: Expiry::new(ExpiryParts {
                rules: store.clone(),
                boxes: boxes.clone(),
                audit: store.clone(),
                rows: store.clone(),
                filing: filing.clone(),
                clock: clock.clone(),
            }),
            cleaning: Cleaning::new(CleaningParts {
                rules: store.clone(),
                classified: store.clone(),
                mail: store.clone(),
                unsubscribing,
                filing: filing.clone(),
                audit: store.clone(),
                tokens: Arc::new(RunTokens { secret }),
                ids,
                clock: clock.clone(),
            }),
            sweeps: Sweeps::new(SweepsParts {
                rules: store.clone(),
                boxes,
                audit: store.clone(),
                rows: store,
                filing,
                clock,
                writes_allowed: !read_only,
            }),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hmac_is_rfc_4231s() {
        // Test case 2 of RFC 4231.
        let mac = hmac_sha256(b"Jefe", b"what do ya want for nothing?");
        let hex: String = mac.iter().map(|b| format!("{b:02x}")).collect();
        assert_eq!(hex, "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843");
    }
}

//! Each account's connection kept between the jobs that hold its gate (#5; the Python app's `Pool` over
//! `KeptConnections`, opt-in there, always on here): a person's reads and changes, Undo, the body backfill, reconcile,
//! a sweep and the outbox's copies reuse one login rather than each making its own.
//!
//! - It is lent only to a job holding the account's gate (`Gates::holds`), so no two jobs use it at once, and only
//!   once at a time: a job's second session, and every job that holds no gate (the sync, a first scan's older mail,
//!   the folder rail), gets a connection of its own, logged out after, as before.
//! - It is checked before each lending. One that has served long enough for its provider (`Keeping`: ten minutes or
//!   200 lendings; on Proton Bridge, which allows few connections and drops them sooner, 90 seconds or 40), or that
//!   doesn't answer a NOOP within `CHECK_SECONDS`, is closed and a new one opened: a server's idle timeout, which
//!   RFC 3501 puts at 30 minutes at least, never meets a borrower.
//! - A lending starts with no folder selected: a command that needs one before the borrower selects one is refused,
//!   as a new connection's would be, so no job acts on the folder the one before it left selected. CONDSTORE is
//!   enabled as the connection opens, where the server offers it, since ENABLE can't follow a SELECT.
//! - A folder a person makes, renames or deletes goes on it too. One renamed or deleted that the connection has
//!   selected on the server, whichever lending selected it, or a folder inside that one, leaves a session servers treat
//!   differently (some end it): nothing more is asked of that folder in the lending, and the connection is logged out
//!   as it comes back rather than kept.
//! - One lost, or failing otherwise, while lent is closed and not kept.
//! - A wake or a change of network (`woke`) closes them all at once, saying nothing to servers that may be gone; a
//!   change to an account, or its removal (`forget`), closes its own; and a quit (`close`) logs each out.
//!
//! What it counts, and how each connection stands, is `stats`: counts and ages, never an account.

use std::collections::{BTreeMap, HashMap};
use std::fmt;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};

use mm_kernel::errors::Fault;
use mm_kernel::seams::Clock;

use crate::gates::Gates;
use crate::ports::{MailSession, SessionError};
use crate::roles::Folder;
use crate::session::{Headers, MailServer, MailServers, Opening, Search, Selected, ServerError};

/// What a kept connection waits for its NOOP before it is given up for a new one.
pub const CHECK_SECONDS: u64 = 10;
/// What a LOGOUT waits for its answer as a kept connection closes: a quit, or an account changed, waits no longer on a
/// server that has gone quiet.
pub const LOGOUT_SECONDS: u64 = 3;

/// What a server says to a command that needs a folder selected before one is: a refusal, which mm-imap names
/// `error`, as it names every NO and BAD.
const NO_FOLDER: ServerError = ServerError::Refused("error");

/// How long a kept connection may serve before it is replaced (`ProviderProfile.recycle_seconds` and `recycle_uses`).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Keeping {
    /// Seconds since it was opened.
    pub seconds: i64,
    /// Lendings.
    pub lendings: u32,
}

impl Keeping {
    /// Well inside a server's idle timeout.
    pub const USUAL: Keeping = Keeping { seconds: 600, lendings: 200 };
    /// Proton Bridge, or a server like it on this machine: it allows few connections and drops them sooner.
    pub const BRIDGE: Keeping = Keeping { seconds: 90, lendings: 40 };

    /// Whether a connection `age` microseconds old, lent `lendings` times, has served long enough.
    pub fn spent(self, age: i64, lendings: u32) -> bool {
        age > self.seconds.saturating_mul(1_000_000) || lendings >= self.lendings
    }
}

/// What the keeper needs to know of the accounts.
pub trait KeptAccounts: Send + Sync + fmt::Debug {
    /// How long `account`'s connection may serve; None where the configuration holds no such account, whose
    /// connection is never kept.
    fn keeping(&self, account: &str) -> Result<Option<Keeping>, Fault>;
}

/// A connection kept, and what is known of it.
struct Kept {
    server: Box<dyn MailServer>,
    /// When it was opened, in the clock's microseconds.
    born: i64,
    lendings: u32,
    /// What ENABLE CONDSTORE answered as it opened.
    condstore: bool,
    /// The wake it was opened after: one opened before the latest is closed.
    generation: u64,
    /// The folder selected on it, by whichever lending, as the server holds it until the next SELECT; None for none.
    folder: Option<String>,
}

impl Kept {
    /// Close it: a LOGOUT, waited for briefly, where the server is likely there to answer; else at once.
    fn close(mut self, politely: bool) {
        if politely {
            self.server.set_timeout(LOGOUT_SECONDS);
            self.server.logout();
        } else {
            self.server.shutdown();
        }
    }
}

/// An account's place for its kept connection.
#[derive(Default)]
struct Slot {
    /// A job has it now, or holds the place to open it.
    lent: AtomicBool,
    kept: Mutex<Option<Kept>>,
}

fn locked<T>(lock: &Mutex<T>) -> MutexGuard<'_, T> {
    // Each change under these locks is whole by the time it is let go.
    lock.lock().unwrap_or_else(PoisonError::into_inner)
}

/// What a lending's return needs to know: whether the connection may be kept.
#[derive(Default)]
struct Shared {
    slots: Mutex<HashMap<String, Arc<Slot>>>,
    generation: AtomicU64,
    closed: AtomicBool,
    counts: Mutex<Counts>,
}

/// What the keeper has done, counted (`pool.counters`).
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Counts {
    /// Connections opened to keep.
    pub created: u64,
    /// Lendings of one kept.
    pub reused: u64,
    /// Kept ones that didn't answer their NOOP.
    pub validate_fail: u64,
    /// Ones lost or failing while lent, closed rather than kept.
    pub discard_dirty: u64,
}

/// How one account's place stands, with no account named.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct SlotStats {
    /// A connection is kept there, or lent.
    pub open: bool,
    pub lendings: u32,
    /// Seconds since it was opened, when one is.
    pub age: Option<f64>,
}

/// What `stats` says.
#[derive(Clone, Debug, PartialEq)]
pub struct KeptStats {
    pub counts: Counts,
    pub slots: Vec<SlotStats>,
}

impl Shared {
    fn count(&self, what: impl FnOnce(&mut Counts)) {
        what(&mut locked(&self.counts));
    }

    /// Whether `slot` is still `account`'s: one forgotten meanwhile isn't.
    fn still(&self, account: &str, slot: &Arc<Slot>) -> bool {
        locked(&self.slots).get(account).is_some_and(|now| Arc::ptr_eq(now, slot))
    }
}

/// Sessions on each account's server, as `opener` opens them, with the connection kept between the jobs that hold
/// the account's gate.
pub struct KeptServers {
    opener: Arc<dyn MailServers>,
    gates: Arc<dyn Gates>,
    accounts: Arc<dyn KeptAccounts>,
    clock: Arc<dyn Clock>,
    shared: Arc<Shared>,
}

impl fmt::Debug for KeptServers {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("KeptServers").finish_non_exhaustive()
    }
}

/// What wires KeptServers.
#[derive(Debug)]
pub struct KeptParts {
    /// Where a new connection comes from: logged in, read-only in a process that only reads.
    pub opener: Arc<dyn MailServers>,
    pub gates: Arc<dyn Gates>,
    pub accounts: Arc<dyn KeptAccounts>,
    pub clock: Arc<dyn Clock>,
}

impl KeptServers {
    pub fn new(parts: KeptParts) -> KeptServers {
        let KeptParts { opener, gates, accounts, clock } = parts;
        KeptServers { opener, gates, accounts, clock, shared: Arc::default() }
    }

    fn slot(&self, account: &str) -> Arc<Slot> {
        locked(&self.shared.slots).entry(account.to_owned()).or_default().clone()
    }

    /// The kept connection, checked, or a new one: the caller holds the slot.
    fn take(&self, slot: &Slot, account: &str, keeping: Keeping, timeout: u64) -> Result<Kept, ServerError> {
        let now = self.clock.now().micros();
        let generation = self.shared.generation.load(Ordering::SeqCst);
        let held = locked(&slot.kept).take();
        if let Some(mut kept) = held {
            if kept.generation != generation {
                kept.close(false);
            } else if keeping.spent(now.saturating_sub(kept.born), kept.lendings) {
                kept.close(true);
            } else {
                kept.server.set_timeout(CHECK_SECONDS);
                if kept.server.noop().is_ok() {
                    kept.server.set_timeout(timeout);
                    kept.lendings += 1;
                    self.shared.count(|c| c.reused += 1);
                    return Ok(kept);
                }
                self.shared.count(|c| c.validate_fail += 1);
                kept.close(false);
            }
        }
        let mut server = self.opener.open(account, timeout)?;
        // ENABLE goes before any folder is selected, so a job that asks for CONDSTORE later finds it already.
        let condstore = match server.enable_condstore() {
            Ok(on) => on,
            Err(e) => {
                server.shutdown();
                return Err(e);
            }
        };
        self.shared.count(|c| c.created += 1);
        Ok(Kept { server, born: now, lendings: 1, condstore, generation, folder: None })
    }

    /// A wake or a change of network: every connection kept is closed now, without a LOGOUT, since its server may be
    /// gone; one lent is closed as it comes back. The next job opens a new one.
    pub fn woke(&self) {
        self.shared.generation.fetch_add(1, Ordering::SeqCst);
        let slots: Vec<Arc<Slot>> = locked(&self.shared.slots).values().cloned().collect();
        for slot in slots {
            let held = locked(&slot.kept).take();
            if let Some(kept) = held {
                kept.close(false);
            }
        }
    }

    /// `account` changed, or was removed: its connection, logged in as it was, is logged out now, or as it comes back
    /// if it is lent.
    pub fn forget(&self, account: &str) {
        let slot = locked(&self.shared.slots).remove(account);
        let held = slot.and_then(|slot| locked(&slot.kept).take());
        if let Some(kept) = held {
            kept.close(true);
        }
    }

    /// The app is closing: each connection kept is logged out, and one lent as it comes back. None is kept after.
    pub fn close(&self) {
        self.shared.closed.store(true, Ordering::SeqCst);
        let slots: Vec<Arc<Slot>> = locked(&self.shared.slots).drain().map(|(_, slot)| slot).collect();
        for slot in slots {
            let held = locked(&slot.kept).take();
            if let Some(kept) = held {
                kept.close(true);
            }
        }
    }

    /// What it has done, and how each account's connection stands, in the accounts' order of address.
    pub fn stats(&self) -> KeptStats {
        let now = self.clock.now().micros();
        let slots: BTreeMap<String, Arc<Slot>> =
            locked(&self.shared.slots).iter().map(|(account, slot)| (account.clone(), slot.clone())).collect();
        let slots = slots
            .values()
            .map(|slot| {
                let lent = slot.lent.load(Ordering::SeqCst);
                let kept = locked(&slot.kept);
                match kept.as_ref() {
                    Some(kept) => SlotStats {
                        open: true,
                        lendings: kept.lendings,
                        age: Some(now.saturating_sub(kept.born) as f64 / 1e6),
                    },
                    None => SlotStats { open: lent, lendings: 0, age: None },
                }
            })
            .collect();
        KeptStats { counts: *locked(&self.shared.counts), slots }
    }
}

impl MailServers for KeptServers {
    fn open(&self, account: &str, timeout_seconds: u64) -> Result<Box<dyn MailServer>, ServerError> {
        if self.shared.closed.load(Ordering::SeqCst) || !self.gates.holds(account) {
            return self.opener.open(account, timeout_seconds);
        }
        let Some(keeping) = self.accounts.keeping(account).map_err(ServerError::Failed)? else {
            return self.opener.open(account, timeout_seconds);
        };
        let slot = self.slot(account);
        if slot.lent.swap(true, Ordering::SeqCst) {
            // This job has it already: a session of its own beside it.
            return self.opener.open(account, timeout_seconds);
        }
        match self.take(&slot, account, keeping, timeout_seconds) {
            Ok(kept) => Ok(Box::new(Lent {
                kept: Some(kept),
                account: account.to_owned(),
                slot,
                shared: self.shared.clone(),
                selected: false,
                dirty: false,
                retire: false,
            })),
            Err(e) => {
                slot.lent.store(false, Ordering::SeqCst);
                Err(e)
            }
        }
    }
}

/// The kept connection, lent to one job until it logs out or drops it.
struct Lent {
    /// None once it has gone back.
    kept: Option<Kept>,
    account: String,
    slot: Arc<Slot>,
    shared: Arc<Shared>,
    /// A folder is selected in this lending.
    selected: bool,
    /// It was lost, or failed otherwise: it is closed, not kept.
    dirty: bool,
    /// A folder it had selected was renamed or deleted: it is logged out, not kept.
    retire: bool,
}

/// What a lending's end does when nothing is left to lend.
const GONE: ServerError = ServerError::Unreachable("ConnectionLost");

impl Lent {
    fn server(&mut self) -> Result<&mut Box<dyn MailServer>, ServerError> {
        self.kept.as_mut().map(|kept| &mut kept.server).ok_or(GONE)
    }

    /// A command's answer, watched: one that lost the connection, or failed otherwise, means it isn't kept.
    fn watch<T>(&mut self, answer: Result<T, ServerError>) -> Result<T, ServerError> {
        if matches!(answer, Err(ServerError::Unreachable(_) | ServerError::Failed(_))) {
            self.dirty = true;
        }
        answer
    }

    /// The same, for a command on the selected folder: refused as a new connection would refuse it, before a folder
    /// is selected in this lending.
    fn in_folder<T>(
        &mut self,
        command: impl FnOnce(&mut dyn MailServer) -> Result<T, ServerError>,
    ) -> Result<T, ServerError> {
        if !self.selected {
            return Err(NO_FOLDER);
        }
        let answer = command(&mut **self.server()?);
        self.watch(answer)
    }

    /// After `name` was renamed or deleted on the server: where that is the folder the connection has selected, or
    /// one whose name begins with it (a folder inside it), nothing more is asked of it in this lending, and the
    /// connection isn't kept.
    fn folder_went(&mut self, name: &str) {
        let held = self.kept.as_ref().and_then(|kept| kept.folder.as_deref());
        if held.is_some_and(|held| held.starts_with(name)) {
            self.selected = false;
            self.retire = true;
        }
    }

    /// Give it back: kept for the next job if it may be, else closed.
    fn give_back(&mut self) {
        let Some(kept) = self.kept.take() else { return };
        let stale = kept.generation != self.shared.generation.load(Ordering::SeqCst);
        if self.dirty || std::thread::panicking() {
            self.shared.count(|c| c.discard_dirty += 1);
            kept.close(false);
        } else if stale {
            kept.close(false);
        } else if self.retire
            || self.shared.closed.load(Ordering::SeqCst)
            || !self.shared.still(&self.account, &self.slot)
        {
            kept.close(true);
        } else {
            *locked(&self.slot.kept) = Some(kept);
        }
        self.slot.lent.store(false, Ordering::SeqCst);
    }
}

impl Drop for Lent {
    fn drop(&mut self) {
        self.give_back();
    }
}

impl MailSession for Lent {
    fn folders(&mut self) -> Result<Vec<Folder>, SessionError> {
        let answer = self.server()?.folders();
        if matches!(answer, Err(SessionError::Unreachable(_))) {
            self.dirty = true;
        }
        answer
    }

    fn status(&mut self, folder: &str) -> Result<(i64, i64), SessionError> {
        let answer = self.server()?.status(folder);
        if matches!(answer, Err(SessionError::Unreachable(_))) {
            self.dirty = true;
        }
        answer
    }

    /// No LOGOUT: the connection goes back to be kept.
    fn logout(&mut self) {
        self.give_back();
    }
}

impl MailServer for Lent {
    fn capabilities(&self) -> Vec<String> {
        self.kept.as_ref().map(|kept| kept.server.capabilities()).unwrap_or_default()
    }

    fn select(&mut self, folder: &str, readonly: bool) -> Result<Selected, ServerError> {
        let answer = self.server()?.select(folder, readonly);
        // A SELECT that fails leaves no folder selected (RFC 3501, 6.3.1).
        self.selected = answer.is_ok();
        if let Some(kept) = self.kept.as_mut() {
            kept.folder = answer.is_ok().then(|| folder.to_owned());
        }
        self.watch(answer)
    }

    fn search(&mut self, criteria: Search<'_>) -> Result<Vec<u32>, ServerError> {
        self.in_folder(|s| s.search(criteria))
    }

    fn flags(&mut self, uids: &[u32]) -> Result<BTreeMap<u32, Vec<String>>, ServerError> {
        self.in_folder(|s| s.flags(uids))
    }

    fn enable_condstore(&mut self) -> Result<bool, ServerError> {
        self.kept.as_ref().map(|kept| kept.condstore).ok_or(GONE)
    }

    fn headers(&mut self, uids: &[u32], fields: &[&str], gmail: bool) -> Result<Vec<Headers>, ServerError> {
        self.in_folder(|s| s.headers(uids, fields, gmail))
    }

    fn body(&mut self, uid: u32) -> Result<Option<Vec<u8>>, ServerError> {
        self.in_folder(|s| s.body(uid))
    }

    fn heads(&mut self, uids: &[u32], bytes: u32) -> Result<BTreeMap<u32, Vec<u8>>, ServerError> {
        self.in_folder(|s| s.heads(uids, bytes))
    }

    fn openings(&mut self, uids: &[u32], bytes: u32) -> Result<BTreeMap<u32, Opening>, ServerError> {
        self.in_folder(|s| s.openings(uids, bytes))
    }

    fn bodies(&mut self, uids: &[u32]) -> Result<BTreeMap<u32, Vec<u8>>, ServerError> {
        self.in_folder(|s| s.bodies(uids))
    }

    fn arrivals(&mut self, uids: &[u32]) -> Result<BTreeMap<u32, i64>, ServerError> {
        self.in_folder(|s| s.arrivals(uids))
    }

    fn store_flags(&mut self, uids: &[u32], add: bool, flags: &[&str]) -> Result<(), ServerError> {
        self.in_folder(|s| s.store_flags(uids, add, flags))
    }

    fn store_labels(&mut self, uids: &[u32], add: bool, labels: &[&str]) -> Result<(), ServerError> {
        self.in_folder(|s| s.store_labels(uids, add, labels))
    }

    fn copy(&mut self, uids: &[u32], dest: &str) -> Result<(), ServerError> {
        self.in_folder(|s| s.copy(uids, dest))
    }

    fn uid_move(&mut self, uids: &[u32], dest: &str) -> Result<(), ServerError> {
        self.in_folder(|s| s.uid_move(uids, dest))
    }

    fn expunge(&mut self, uids: &[u32]) -> Result<(), ServerError> {
        self.in_folder(|s| s.expunge(uids))
    }

    fn append(
        &mut self,
        folder: &str,
        raw: &[u8],
        flags: &[&str],
        when: Option<&str>,
    ) -> Result<Option<u32>, ServerError> {
        let answer = self.server()?.append(folder, raw, flags, when);
        self.watch(answer)
    }

    /// The borrower gave up on the server: it is closed, not kept.
    fn shutdown(&mut self) {
        self.dirty = true;
        self.give_back();
    }

    fn noop(&mut self) -> Result<(), ServerError> {
        let answer = self.server()?.noop();
        self.watch(answer)
    }

    fn set_timeout(&mut self, seconds: u64) {
        if let Ok(server) = self.server() {
            server.set_timeout(seconds);
        }
    }

    fn create_folder(&mut self, name: &str) -> Result<(), ServerError> {
        let answer = self.server()?.create_folder(name);
        self.watch(answer)
    }

    fn rename_folder(&mut self, name: &str, to: &str) -> Result<(), ServerError> {
        let answer = self.server()?.rename_folder(name, to);
        if answer.is_ok() {
            self.folder_went(name);
        }
        self.watch(answer)
    }

    fn delete_folder(&mut self, name: &str) -> Result<(), ServerError> {
        let answer = self.server()?.delete_folder(name);
        if answer.is_ok() {
            self.folder_went(name);
        }
        self.watch(answer)
    }
}

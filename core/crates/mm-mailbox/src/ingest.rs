//! Mail into the database, whole or by UID watermark (`mailbox.application.sync.Ingest`): fetch the headers,
//! classify, store with the message's identity and index it, read the body text of the Inbox's mail that isn't junk,
//! read again the flags of mail already stored, and thread what arrived.
//!
//! No transaction stays open while the server is asked: each store call is its own. What isn't here yet: a person's
//! arrival filters (Curation's `arrival_flags`), the invite a message carries kept with its body (Calendar's), and a
//! login a server refused held back (`accounts.logins`).

use std::sync::Arc;

use mm_content::mime;
use mm_kernel::errors::Fault;
use mm_kernel::log;
use mm_kernel::seams::Clock;
use mm_kernel::when;

use crate::session::{HEADER_FIELDS, MailServer, MailServers, ServerError, found_by_message_id};
use crate::sync_plan::{self, FLAG_REFRESH, WHOLE};
use crate::sync_ports::{
    Classifier, Envelope, Events, FlagRow, Incoming, MailboxEvent, SyncAccount, SyncAccounts, SyncStore,
};

const TAG: &str = "ingest";
/// What a session waits for each answer (`open_session`'s default).
const TIMEOUT_SECONDS: u64 = 60;
/// How many UIDs one flag read names (`FLAG_BATCH`).
pub const FLAG_BATCH: usize = 500;

/// Why a sync didn't finish: the server, or the store.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SyncError {
    Server(ServerError),
    Store(Fault),
    UnknownAccount,
}

impl SyncError {
    /// The class a log line or a queue row names it by.
    pub fn class(self) -> &'static str {
        match self {
            SyncError::Server(e) => e.class(),
            SyncError::Store(fault) => fault.class,
            SyncError::UnknownAccount => "UnknownAccount",
        }
    }
}

impl From<ServerError> for SyncError {
    fn from(e: ServerError) -> SyncError {
        SyncError::Server(e)
    }
}

impl From<Fault> for SyncError {
    fn from(fault: Fault) -> SyncError {
        SyncError::Store(fault)
    }
}

/// What a pass stored: (not junk, junk, the highest UID it saw).
pub type Stored = (usize, usize, u32);

#[derive(Debug)]
pub struct Ingest {
    accounts: Arc<dyn SyncAccounts>,
    servers: Arc<dyn MailServers>,
    store: Arc<dyn SyncStore>,
    classifier: Arc<dyn Classifier>,
    events: Arc<dyn Events>,
    clock: Arc<dyn Clock>,
}

/// A session that logs out however the work on it ends.
struct Open(Box<dyn MailServer>);

impl Drop for Open {
    fn drop(&mut self) {
        self.0.logout();
    }
}

impl Ingest {
    pub fn new(
        accounts: Arc<dyn SyncAccounts>,
        servers: Arc<dyn MailServers>,
        store: Arc<dyn SyncStore>,
        classifier: Arc<dyn Classifier>,
        events: Arc<dyn Events>,
        clock: Arc<dyn Clock>,
    ) -> Ingest {
        Ingest { accounts, servers, store, classifier, events, clock }
    }

    fn account(&self, account: &str) -> Result<SyncAccount, SyncError> {
        self.accounts.sync_account(account)?.ok_or(SyncError::UnknownAccount)
    }

    fn now(&self) -> String {
        when::iso(self.clock.now())
    }

    /// Sync one folder, UIDVALIDITY checked: `full` scans it whole, else only UIDs above its watermark. `recent`
    /// bounds a first or whole scan to the newest N UIDs. The folder's scan_state says what the scans so far read.
    pub fn sync_folder(
        &self,
        account: &str,
        folder: &str,
        full: bool,
        recent: Option<usize>,
    ) -> Result<Stored, SyncError> {
        let acct = self.account(account)?;
        let mut s = Open(self.servers.open(&acct.email, TIMEOUT_SECONDS)?);
        let condstore = s.0.enable_condstore()?;
        let selected = s.0.select(folder, true)?;
        let modseq = if condstore { selected.highest_modseq } else { None };
        let state = self.store.folder_state(&acct.email, folder)?;
        let plan = sync_plan::plan_folder(selected.uidvalidity, state.uidvalidity, state.watermark, full, recent);
        if plan.reset {
            self.store.reset_folder(&acct.email, folder, self.clock.now().micros().div_euclid(1_000_000))?;
        }
        if let Some(uidvalidity) = selected.uidvalidity.filter(|v| *v != 0) {
            self.store.set_uidvalidity(&acct.email, folder, uidvalidity)?;
        }
        let (uids, scan) = plan.to_fetch(s.0.search(plan.search())?);
        let mut stored = Vec::new();
        let with_body = sync_plan::reads_bodies(folder);
        let result = self.process(&mut *s.0, &acct, &uids, folder, with_body, true, &mut stored)?;
        if let Some(scan) = scan {
            self.store.set_scan_state(&acct.email, folder, scan)?;
        }
        if plan.read_flags {
            self.refresh_flags(&mut *s.0, &acct, folder, modseq)?;
        }
        let mark = if scan == Some(WHOLE) { modseq.filter(|m| *m != 0) } else { None };
        self.store.settle(&acct.email, folder, mark, &stored, &self.now())?;
        Ok(result)
    }

    /// Ingest the one message whose Message-ID is `message_id` into `folder`, past the watermark, which stays where
    /// it was: Undo's way to bring back a message whose UID a sync wouldn't fetch again. How many it stored.
    pub fn ingest_message(&self, account: &str, folder: &str, message_id: &str) -> Result<usize, SyncError> {
        if message_id.is_empty() {
            return Ok(0);
        }
        let acct = self.account(account)?;
        let mut s = Open(self.servers.open(&acct.email, TIMEOUT_SECONDS)?);
        s.0.select(folder, true)?;
        let Some(uid) = found_by_message_id(&mut *s.0, message_id)? else { return Ok(0) };
        let mut stored = Vec::new();
        self.process(&mut *s.0, &acct, &[uid], folder, sync_plan::reads_bodies(folder), false, &mut stored)?;
        self.store.settle(&acct.email, folder, None, &stored, &self.now())?;
        Ok(1)
    }

    /// Store every one of `uids` in the selected `folder`, junk kept as a badge. The Inbox's bodies are read for
    /// their text; other folders' wait to be opened. `advance` false stores them without touching the watermark.
    #[allow(clippy::too_many_arguments)]
    fn process(
        &self,
        s: &mut dyn MailServer,
        acct: &SyncAccount,
        uids: &[u32],
        folder: &str,
        with_body: bool,
        advance: bool,
        stored: &mut Vec<i64>,
    ) -> Result<Stored, SyncError> {
        let (mut max_uid, mut kept, mut junk) = (0u32, 0usize, 0usize);
        let mut rows: Vec<Incoming> = Vec::new();
        if !uids.is_empty() {
            let fetched = s.headers(uids, &HEADER_FIELDS, acct.gmail_threads)?;
            let fields: Vec<mime::Fields> = fetched.iter().map(|h| mime::fields(&h.raw)).collect();
            let envelopes: Vec<Envelope<'_>> = fields
                .iter()
                .map(|f| Envelope {
                    from_addr: &f.from_addr,
                    subject: &f.subject,
                    from_name: &f.from_name,
                    has_list_unsub: f.has_list_unsub,
                })
                .collect();
            let verdicts = self.classifier.classify(&envelopes)?;
            for ((h, fields), verdict) in fetched.into_iter().zip(fields).zip(verdicts) {
                max_uid = max_uid.max(h.uid);
                if verdict.is_junk {
                    junk += 1
                } else {
                    kept += 1
                }
                rows.push(Incoming {
                    uid: h.uid,
                    flags: sync_plan::columns_of(&h.flags),
                    fields,
                    gmail_thread: h.gmail_thread,
                    gmail_labels: h.gmail_labels,
                    verdict,
                    snippet: String::new(),
                });
            }
        }
        for mut row in rows {
            if with_body && !row.verdict.is_junk {
                row.snippet = match s.body(row.uid) {
                    Ok(raw) => mime::text_body(&raw.unwrap_or_default()),
                    Err(e) => {
                        if !e.is_unreachable() {
                            log::warning(TAG, "a body fetch was refused", &[], Some(e.class()));
                        }
                        String::new()
                    }
                };
            }
            let (pk, threads) = self.store.store_copy(acct, folder, &row)?;
            if threads {
                stored.push(pk);
            }
        }
        if advance {
            self.store.advance(&acct.email, folder, max_uid, &self.now())?;
        }
        Ok((kept, junk, max_uid))
    }

    /// Read the flags of the folder's mail again and store what changed, so mail read, starred or answered in
    /// another client shows so here. A row with a flag change still queued, or on its way out by a queued move,
    /// keeps its own, and the folder's mark stays behind it. How many rows changed.
    fn refresh_flags(
        &self,
        s: &mut dyn MailServer,
        acct: &SyncAccount,
        folder: &str,
        modseq: Option<u64>,
    ) -> Result<usize, SyncError> {
        let email = acct.email.as_str();
        let since = if modseq.is_some() {
            self.store.folder_state(email, folder)?.modseq.and_then(|m| u64::try_from(m).ok())
        } else {
            None
        };
        let plan = sync_plan::plan_flags(modseq, since);
        if plan.unchanged {
            return Ok(0);
        }
        let mut uids: Option<Vec<u32>> = None;
        if let Some(changed_since) = plan.changed_since {
            match s.search(crate::session::Search::ChangedSince(changed_since)) {
                Ok(found) => uids = Some(found),
                Err(e) if e.is_unreachable() => return Err(e.into()),
                Err(e) => log::warning(TAG, "SEARCH MODSEQ refused: reading the newest instead", &[], Some(e.class())),
            }
        }
        let (rows, waiting) = self.store.flag_rows(email, folder, uids.as_deref(), FLAG_REFRESH)?;
        let mut now = std::collections::BTreeMap::new();
        for batch in rows.chunks(FLAG_BATCH) {
            let asked: Vec<u32> = batch.iter().map(|r| r.uid).collect();
            match s.flags(&asked) {
                Ok(found) => now.extend(found),
                Err(e) if e.is_unreachable() => return Err(e.into()),
                Err(_) => return Ok(0),
            }
        }
        let held_back = rows.iter().any(|r| r.pending_removal || waiting.contains(&r.message_id));
        let changed: Vec<(FlagRow, [i64; 5])> = rows
            .into_iter()
            .filter(|r| !(r.pending_removal || waiting.contains(&r.message_id)))
            .filter_map(|r| {
                let new = sync_plan::columns_of(now.get(&r.uid)?);
                (new != r.flags).then_some((r, new))
            })
            .collect();
        let mark = plan.mark(held_back, uids.is_some());
        if changed.is_empty() && mark.is_none() {
            return Ok(0);
        }
        let count = self.store.write_flags(email, folder, &changed, mark)?;
        if count > 0 {
            self.events.publish(MailboxEvent::FlagsChanged {
                account: email.to_owned(),
                folder: folder.to_owned(),
                changed: count,
            });
        }
        Ok(count)
    }
}

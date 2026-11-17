//! Mail into the database, whole or by UID watermark (`mailbox.application.sync.Ingest`): fetch the headers,
//! classify, store with the message's identity and index it, read the opening of the Inbox's mail that isn't junk for
//! its text, read again the flags of mail already stored, and thread what arrived.
//!
//! What a sync stores goes in steps (`sync_plan::Step`), each one transaction with how far the sync got. A first scan
//! stores the newest step and leaves the folder's older mail to `read_older`, which the background calls a few steps
//! at a time, newest first, each step giving way to a sync that read the folder afresh meanwhile.
//!
//! A message's text comes from its first `HEAD_BYTES` (`BODY.PEEK[]<0.65536>`), so no attachment is downloaded for a
//! snippet. An opening that holds the whole message is kept as its body, with its row in the step's transaction, as
//! opening it would keep it: search finds what it says, opening it asks no server, and the body backfill never fetches
//! it again. One cut off keeps only its snippet, and waits for the backfill or to be opened. The whole of one cut off
//! is fetched only for an invite, which is kept as its body so Today & next finds it. A message whose opening the
//! server won't send costs only its own text, and one whose headers it won't send is left out of a read of older mail;
//! a connection lost part way stores nothing of the step, which is read again.
//!
//! Mail new to the Inbox that MailMind has never seen before is judged by the person's filters as it is stored, and
//! what they ask is queued for the server (`arrival_flags`).
//!
//! No transaction stays open while the server is asked: each store call is its own.
//!
//! Each sync of an account is a flight (`flights`): one whose account is removed stops before it stores its next step.

use std::collections::BTreeMap;
use std::sync::{Arc, OnceLock};

use mm_content::mime;
use mm_kernel::errors::Fault;
use mm_kernel::events::Topic;
use mm_kernel::log::{self, Field};
use mm_kernel::seams::Clock;
use mm_kernel::when;
use serde_json::json;

use crate::arrival_ports::ArrivalFilters;
use crate::fetch_ports::KeptBody;
use crate::flights::{Flight, Flights};
use crate::session::{
    HEADER_BATCH, HEADER_FIELDS, Headers, MailServer, MailServers, Search, ServerError, found_by_message_id,
};
use crate::sync_plan::{self, FLAG_REFRESH, Scan, Step};
use crate::sync_ports::{
    Classifier, Envelope, Events, FlagRow, Incoming, MailboxEvent, Place, SyncAccount, SyncAccounts, SyncStore,
};

const TAG: &str = "ingest";
/// What a session waits for each answer (`open_session`'s default).
const TIMEOUT_SECONDS: u64 = 60;
/// How many UIDs one flag read names (`FLAG_BATCH`).
pub const FLAG_BATCH: usize = 500;
/// How much of a message a sync reads for its text: the rest, attachments mostly, waits to be opened.
pub const HEAD_BYTES: u32 = 65_536;

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

/// How far a folder's first scan has got: of the messages the folder holds, how many it has stored.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Progress {
    pub done: usize,
    pub total: usize,
}

/// What a sync of one folder came to.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Synced {
    /// How many messages it stored, not junk and junk.
    pub kept: usize,
    pub junk: usize,
    /// The newest UID it kept, junk aside, 0 for none: the message new-mail names.
    pub newest_kept: u32,
    /// It read the folder afresh, a first scan or one the server renumbered: what it stored was there before, so it
    /// isn't new mail.
    pub afresh: bool,
    /// How far the folder's first scan has got, while older mail is still to read in the background.
    pub reading: Option<Progress>,
}

/// A sync whose account was removed goes no further: there is no account to read for.
fn going_on(flight: &Flight<'_>) -> Result<(), SyncError> {
    if flight.stopped() { Err(SyncError::UnknownAccount) } else { Ok(()) }
}

#[derive(Debug)]
pub struct Ingest {
    accounts: Arc<dyn SyncAccounts>,
    servers: Arc<dyn MailServers>,
    store: Arc<dyn SyncStore>,
    classifier: Arc<dyn Classifier>,
    events: Arc<dyn Events>,
    clock: Arc<dyn Clock>,
    /// The person's filters, heard once what queues their flags is built (it files through this sync).
    arrivals: OnceLock<Arc<dyn ArrivalFilters>>,
    /// Each account's syncs now, which its removal stops.
    flights: Arc<Flights>,
}

/// A session that logs out however the work on it ends.
struct Open(Box<dyn MailServer>);

impl Drop for Open {
    fn drop(&mut self) {
        self.0.logout();
    }
}

/// How a pass over one folder stores what it fetches.
#[derive(Clone, Copy, Debug)]
struct Pass<'a> {
    folder: &'a str,
    /// The Inbox's: each message's text is read from its opening.
    with_body: bool,
    /// What it finds is new to the folder, so the person's filters speak for what MailMind has never seen.
    arriving: bool,
    /// It reads a first scan's older mail: a message whose headers the server won't send is left out, so the scan
    /// still reaches the folder's oldest.
    older: bool,
}

/// Whether the opening of a message that was cut off may carry an invite further on: it names a calendar part.
fn may_hold_invite(head: &[u8]) -> bool {
    let lower = head.to_ascii_lowercase();
    [&b"text/calendar"[..], b"application/ics", b".ics"]
        .iter()
        .any(|said| lower.windows(said.len()).any(|w| w == *said))
}

/// The headers of `uids` a server will send, for a read of older mail: a FETCH it refuses is asked again a message at a
/// time, and one it still refuses is left out, so a message it can't send never holds the scan on its step. A step
/// none of whose messages it sends gives way, rather than leave the folder read whole when it isn't.
fn headers_sent(s: &mut dyn MailServer, acct: &SyncAccount, uids: &[u32]) -> Result<Vec<Headers>, ServerError> {
    let mut out = Vec::with_capacity(uids.len());
    let (mut refused, mut left_out) = (None, 0usize);
    for batch in uids.chunks(HEADER_BATCH) {
        match s.headers(batch, &HEADER_FIELDS, acct.gmail_threads) {
            Ok(found) => out.extend(found),
            Err(e) if e.is_unreachable() => return Err(e),
            Err(e) => {
                refused = Some(e);
                for uid in batch {
                    match s.headers(&[*uid], &HEADER_FIELDS, acct.gmail_threads) {
                        Ok(found) => out.extend(found),
                        Err(e) if e.is_unreachable() => return Err(e),
                        Err(_) => left_out += 1,
                    }
                }
            }
        }
    }
    if let Some(e) = refused {
        if out.is_empty() {
            return Err(e);
        }
        if left_out > 0 {
            let count = left_out.to_string();
            log::warning(
                TAG,
                "%(count)s messages the server wouldn't send were left out",
                &[(Field::Count, &count)],
                Some(e.class()),
            );
        }
    }
    out.sort_by_key(|h| h.uid);
    Ok(out)
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
        let flights = Arc::new(Flights::default());
        Ingest { accounts, servers, store, classifier, events, clock, arrivals: OnceLock::new(), flights }
    }

    /// The same, its syncs in `flights`, which a removal stops through.
    #[must_use]
    pub fn flying_in(self, flights: Arc<Flights>) -> Ingest {
        Ingest { flights, ..self }
    }

    /// Each account's syncs now.
    pub fn flights(&self) -> &Flights {
        &self.flights
    }

    /// From now on, new Inbox mail is judged by the person's filters as it is stored. The first call binds them; a
    /// later one changes nothing.
    pub fn hear_arrivals(&self, filters: Arc<dyn ArrivalFilters>) {
        _ = self.arrivals.set(filters);
    }

    fn account(&self, account: &str) -> Result<SyncAccount, SyncError> {
        self.accounts.sync_account(account)?.ok_or(SyncError::UnknownAccount)
    }

    fn now(&self) -> String {
        when::iso(self.clock.now())
    }

    /// Sync one folder, UIDVALIDITY checked: `full` scans it whole, else only UIDs above its watermark. `recent`
    /// bounds a first or whole scan to the newest N UIDs. A first scan stores the folder's newest messages and
    /// leaves the rest to `read_older`. The folder's scan_state says what the scans so far read.
    pub fn sync_folder(
        &self,
        account: &str,
        folder: &str,
        full: bool,
        recent: Option<usize>,
    ) -> Result<Synced, SyncError> {
        let flight = self.flights.fly(account);
        let acct = self.account(account)?;
        let mut s = Open(self.servers.open(&acct.email, TIMEOUT_SECONDS)?);
        let condstore = s.0.enable_condstore()?;
        let selected = s.0.select(folder, true)?;
        let modseq = if condstore { selected.highest_modseq } else { None };
        let state = self.store.folder_state(&acct.email, folder)?;
        let plan =
            sync_plan::plan_folder(selected.uidvalidity, state.uidvalidity, state.watermark, state.scan, full, recent);
        if plan.reset {
            self.store.reset_folder(&acct.email, folder, self.clock.now().micros().div_euclid(1_000_000))?;
        }
        if let Some(uidvalidity) = selected.uidvalidity.filter(|v| *v != 0) {
            self.store.set_uidvalidity(&acct.email, folder, uidvalidity)?;
        }
        let found = s.0.search(plan.search())?;
        let total = found.len();
        let steps = plan.steps(found);
        let pass = Pass {
            folder,
            with_body: sync_plan::reads_bodies(folder),
            arriving: plan.arrives_in(folder),
            older: false,
        };
        // A folder read whole before and read whole again (one that was empty) finds what is new to it.
        let mut synced = Synced { afresh: plan.whole && plan.scan != Scan::Whole, ..Synced::default() };
        let mut stored = Vec::new();
        for step in &steps {
            let Some((kept, junk, newest_kept)) =
                self.store_step(&mut *s.0, &acct, pass, step, &mut stored, &flight)?
            else {
                break;
            };
            synced.kept += kept;
            synced.junk += junk;
            synced.newest_kept = synced.newest_kept.max(newest_kept);
        }
        let leaves = steps.last().and_then(|step| step.leaves);
        if let Some(Scan::Down(_)) = leaves {
            synced.reading = Some(Progress { done: steps.iter().map(|step| step.uids.len()).sum(), total });
        }
        going_on(&flight)?;
        if plan.read_flags {
            self.refresh_flags(&mut *s.0, &acct, folder, modseq)?;
        }
        // A whole read of the folder's flags, or of its newest with the rest read after this mark, leaves the mark.
        let mark = if plan.whole && leaves != Some(Scan::Cut) { modseq.filter(|m| *m != 0) } else { None };
        self.store.settle(&acct.email, folder, mark, &stored, &self.now())?;
        Ok(synced)
    }

    /// Read older mail of a folder whose first scan left some (`Scan::Down`), newest first, at most `chunks` steps,
    /// each in one transaction and said on the event stream as `folder-synced` once it is stored; `heard` hears how
    /// far the scan has got after each. How far it got, or None once the folder is read whole, or when a sync read
    /// it afresh meanwhile and this gave way.
    pub fn read_older(
        &self,
        account: &str,
        folder: &str,
        chunks: usize,
        heard: &mut dyn FnMut(Option<Progress>),
    ) -> Result<Option<Progress>, SyncError> {
        let flight = self.flights.fly(account);
        let acct = self.account(account)?;
        let Scan::Down(floor) = self.store.folder_state(&acct.email, folder)?.scan else { return Ok(None) };
        let mut s = Open(self.servers.open(&acct.email, TIMEOUT_SECONDS)?);
        let selected = s.0.select(folder, true)?;
        let state = self.store.folder_state(&acct.email, folder)?;
        let renumbered = matches!((selected.uidvalidity, state.uidvalidity), (Some(now), Some(known))
            if now != 0 && known != 0 && i64::from(now) != known);
        if renumbered || state.scan != Scan::Down(floor) {
            // The next sync reads it afresh.
            return Ok(None);
        }
        let below = s.0.search(Search::Below(floor))?;
        let total = usize::try_from(selected.exists).unwrap_or(usize::MAX).max(below.len());
        let mut left = below.len();
        let pass = Pass { folder, with_body: sync_plan::reads_bodies(folder), arriving: false, older: true };
        let mut reached = None;
        for step in sync_plan::older_steps(floor, &below, chunks) {
            let mut stored = Vec::new();
            let Some((kept, junk, _)) = self.store_step(&mut *s.0, &acct, pass, &step, &mut stored, &flight)? else {
                return Ok(None);
            };
            self.store.settle(&acct.email, folder, None, &stored, &self.now())?;
            left = left.saturating_sub(step.uids.len());
            reached = match step.leaves {
                Some(Scan::Down(_)) => Some(Progress { done: total.saturating_sub(left), total }),
                _ => None,
            };
            heard(reached);
            let data = json!({"account": acct.email, "folder": folder, "ok": true, "new": kept + junk});
            self.events.publish(MailboxEvent::Page { topic: Topic::FolderSynced, data });
        }
        Ok(reached)
    }

    /// Each account and folder whose first scan has older mail still to read.
    pub fn reading_older(&self) -> Result<Vec<(String, String)>, Fault> {
        Ok(self.store.reading_older()?.into_iter().map(|(account, folder, _)| (account, folder)).collect())
    }

    /// Ingest the one message whose Message-ID is `message_id` into `folder`, past the watermark, which stays where
    /// it was: Undo's way to bring back a message whose UID a sync wouldn't fetch again. How many it stored.
    pub fn ingest_message(&self, account: &str, folder: &str, message_id: &str) -> Result<usize, SyncError> {
        self.ingest_messages(account, folder, &[message_id.to_owned()])
    }

    /// The same for each of `message_ids`, on one session: Undo's way to bring back a sweep's hundreds at once. How
    /// many it stored.
    pub fn ingest_messages(&self, account: &str, folder: &str, message_ids: &[String]) -> Result<usize, SyncError> {
        if message_ids.iter().all(String::is_empty) {
            return Ok(0);
        }
        let flight = self.flights.fly(account);
        let acct = self.account(account)?;
        let mut s = Open(self.servers.open(&acct.email, TIMEOUT_SECONDS)?);
        s.0.select(folder, true)?;
        let mut uids = Vec::with_capacity(message_ids.len());
        for mid in message_ids.iter().filter(|mid| !mid.is_empty()) {
            uids.extend(found_by_message_id(&mut *s.0, mid)?);
        }
        uids.sort_unstable();
        uids.dedup();
        if uids.is_empty() {
            return Ok(0);
        }
        let pass = Pass { folder, with_body: sync_plan::reads_bodies(folder), arriving: false, older: false };
        let fetched = self.fetch(&mut *s.0, &acct, &uids, pass)?;
        let mut stored = Vec::new();
        for row in &fetched {
            going_on(&flight)?;
            let (pk, threads) = self.store.store_copy(&acct, folder, row)?;
            if threads {
                stored.push(pk);
            }
        }
        self.store.settle(&acct.email, folder, None, &stored, &self.now())?;
        Ok(uids.len())
    }

    /// The messages `uids` names in the selected folder: their headers, classified, and the Inbox's text read from
    /// the opening of each that isn't junk, with the whole body where the sync read it whole, oldest first.
    fn fetch(
        &self,
        s: &mut dyn MailServer,
        acct: &SyncAccount,
        uids: &[u32],
        pass: Pass<'_>,
    ) -> Result<Vec<Incoming>, SyncError> {
        if uids.is_empty() {
            return Ok(Vec::new());
        }
        let headers = if pass.older {
            headers_sent(s, acct, uids)?
        } else {
            s.headers(uids, &HEADER_FIELDS, acct.gmail_threads)?
        };
        let fields: Vec<mime::Fields> = headers.iter().map(|h| mime::fields(&h.raw)).collect();
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
        let mut fetched: Vec<Incoming> = headers
            .into_iter()
            .zip(fields)
            .zip(verdicts)
            .map(|((h, fields), verdict)| Incoming {
                uid: h.uid,
                flags: sync_plan::columns_of(&h.flags),
                fields,
                gmail_thread: h.gmail_thread,
                gmail_labels: h.gmail_labels,
                verdict,
                snippet: String::new(),
                body: None,
            })
            .collect();
        if !pass.with_body {
            return Ok(fetched);
        }
        let wanted: Vec<u32> = fetched.iter().filter(|f| !f.verdict.is_junk).map(|f| f.uid).collect();
        let mut heads = match s.heads(&wanted, HEAD_BYTES) {
            Ok(heads) => heads,
            // A connection lost part way stores nothing of the step, which is read again, rather than its mail with
            // no text.
            Err(e) if e.is_unreachable() => return Err(e.into()),
            Err(e) => {
                log::warning(TAG, "a body fetch was refused", &[], Some(e.class()));
                BTreeMap::new()
            }
        };
        let now = self.now();
        for f in &mut fetched {
            let Some(head) = heads.remove(&f.uid) else { continue };
            f.snippet = mime::text_body(&head);
            f.body = self.body_of(s, f.uid, head)?.map(|raw| KeptBody::of(raw, now.clone()));
        }
        Ok(fetched)
    }

    /// The whole message to keep as its body, or None: its opening, where that held all of it; or, where it was cut
    /// off naming a calendar part, the whole message fetched for the invite it carries.
    fn body_of(&self, s: &mut dyn MailServer, uid: u32, head: Vec<u8>) -> Result<Option<Vec<u8>>, SyncError> {
        if head.len() < usize::try_from(HEAD_BYTES).unwrap_or(usize::MAX) {
            return Ok((!head.is_empty()).then_some(head));
        }
        if !(mime::has_invite(&head) || may_hold_invite(&head)) {
            return Ok(None);
        }
        match s.body(uid) {
            Ok(raw) => Ok(raw.filter(|raw| mime::has_invite(raw))),
            Err(e) if e.is_unreachable() => Err(e.into()),
            Err(e) => {
                log::warning(TAG, "a body fetch was refused", &[], Some(e.class()));
                Ok(None)
            }
        }
    }

    /// Store one step of a pass, junk kept as a badge, in one transaction with where it leaves the folder; `stored`
    /// gets what threading has yet to hear of. (not junk, junk, the newest UID kept, junk aside), or None when the
    /// folder was read afresh meanwhile and the step gave way. Its account removed (`flight` stopped), it stores
    /// nothing more.
    fn store_step(
        &self,
        s: &mut dyn MailServer,
        acct: &SyncAccount,
        pass: Pass<'_>,
        step: &Step,
        stored: &mut Vec<i64>,
        flight: &Flight<'_>,
    ) -> Result<Option<(usize, usize, u32)>, SyncError> {
        let filters = self.arrivals.get().filter(|_| pass.arriving);
        let fetched = self.fetch(s, acct, &step.uids, pass)?;
        going_on(flight)?;
        let junk = fetched.iter().filter(|f| f.verdict.is_junk).count();
        let newest_kept = fetched.iter().filter(|f| !f.verdict.is_junk).map(|f| f.uid).max().unwrap_or(0);
        let news: Vec<bool> = match filters {
            Some(_) => fetched
                .iter()
                .map(|f| self.store.is_known(&acct.email, &f.fields).map(|k| !k))
                .collect::<Result<_, _>>()?,
            None => vec![false; fetched.len()],
        };
        let place = Place { watermark: step.watermark, leaves: step.leaves, holds: step.holds, when: self.now() };
        let Some(pks) = self.store.store_step(acct, pass.folder, &fetched, &place)? else { return Ok(None) };
        let mut queued = false;
        for ((pk, threads), news) in pks.into_iter().zip(news) {
            if threads {
                stored.push(pk);
            }
            if let Some(filters) = filters.filter(|_| news) {
                let wanted = filters.arrival_flags(pk);
                if !wanted.is_empty() && self.store.queue_filtered(pk, &wanted, &self.now())? {
                    queued = true;
                }
            }
        }
        if let Some(filters) = filters.filter(|_| queued) {
            filters.flags_queued();
        }
        Ok(Some((fetched.len() - junk, junk, newest_kept)))
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
            match s.search(Search::ChangedSince(changed_since)) {
                Ok(found) => uids = Some(found),
                Err(e) if e.is_unreachable() => return Err(e.into()),
                Err(e) => log::warning(TAG, "SEARCH MODSEQ refused: reading the newest instead", &[], Some(e.class())),
            }
        }
        let (rows, waiting) = self.store.flag_rows(email, folder, uids.as_deref(), FLAG_REFRESH)?;
        let mut now = BTreeMap::new();
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

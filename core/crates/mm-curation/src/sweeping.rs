//! Sweeping an inbox (`mailmind.curation.application.sweep`, `domain.live` and `mailbox.application.bulk_moves`):
//! classify it as the server shows it now, then move its junk to Trash, verified and audited. Mail is read live before
//! anything moves, so a stored row can't send the wrong message anywhere. Each account's work is on its own gate.

use std::collections::HashSet;
use std::sync::Arc;

use mm_kernel::errors::{Failure, Fault, Problem};
use mm_kernel::log::{self, Field};
use mm_kernel::seams::Clock;
use serde_json::{Map, Value, json};

use crate::classifier::{self, Bucket};
use crate::cleanup_ports::{Audited, LiveError, LiveInboxes, LiveSession, SweepTrail, SweptRows};
use crate::ports::{Filing, RuleBook};
use crate::rules::RuleSets;
use crate::sweep_plan::tally;

const TAG: &str = "sweep";
/// What a sweep asks the server for of each message.
pub const READ_FIELDS: [&str; 5] = ["FROM", "SUBJECT", "MESSAGE-ID", "LIST-UNSUBSCRIBE", "LIST-ID"];
/// UIDs to a header fetch, so no command line grows too long.
const HEADER_BATCH: usize = 300;
/// UIDs to one MOVE.
pub const MOVE_BATCH: usize = 200;
/// What a read-only process answers a sweep that would move mail: it never runs.
pub const READ_ONLY: Fault = Fault::new("ReadOnlyError");

/// A message of the INBOX as the server shows it now, with what the classifier wants of its headers. Either List
/// header says it's bulk.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Read {
    pub uid: String,
    pub from: String,
    pub name: String,
    pub subject: String,
    pub mid: String,
    pub bulk: bool,
}

/// A message as a sweep reads it, from its `READ_FIELDS`.
pub fn read_from_headers(uid: u32, raw: &[u8]) -> Read {
    let fields = mm_content::mime::fields(raw);
    Read {
        uid: uid.to_string(),
        from: fields.from_addr,
        name: fields.from_name,
        subject: fields.subject,
        mid: fields.message_id,
        bulk: fields.has_list_unsub,
    }
}

/// What the classifier made of a message read live.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Classified {
    pub read: Read,
    pub bucket: Bucket,
    pub reason: String,
}

pub fn classified(rows: Vec<Read>, rules: &RuleSets) -> Vec<Classified> {
    rows.into_iter()
        .map(|read| {
            let said = classifier::classify(&read.from, &read.subject, &read.name, read.bulk, rules);
            Classified { bucket: said.bucket, reason: said.reason.into_owned(), read }
        })
        .collect()
}

/// The INBOX as `s` shows it now: every message's `READ_FIELDS`, in UID order.
fn fetch_headers(s: &mut dyn LiveSession) -> Result<Vec<Read>, LiveError> {
    s.select("INBOX", true)?;
    let uids = s.uids()?;
    let mut out = Vec::with_capacity(uids.len());
    for batch in uids.chunks(HEADER_BATCH) {
        out.extend(s.headers(batch, &READ_FIELDS)?.into_iter().map(|(uid, raw)| read_from_headers(uid, &raw)));
    }
    Ok(out)
}

fn still_in_inbox(s: &mut dyn LiveSession) -> Result<HashSet<String>, LiveError> {
    s.select("INBOX", true)?;
    Ok(s.uids()?.into_iter().map(|u| u.to_string()).collect())
}

fn numbers(uids: &[String]) -> Vec<u32> {
    uids.iter().filter_map(|u| u.parse().ok()).collect()
}

/// One message alone: a refusal leaves it for the verification pass, and a lost connection ends the work.
fn move_one(s: &mut dyn LiveSession, uid: &str, dest: &str) -> Result<(), LiveError> {
    match s.move_to(&numbers(&[uid.to_owned()]), dest) {
        Err(LiveError::Lost(class)) => Err(LiveError::Lost(class)),
        _ => Ok(()),
    }
}

/// Move the UIDs to `dest`, then look at the INBOX again and try once more each one that didn't leave: (how many
/// moved, the stragglers). They go `MOVE_BATCH` at a time, and a refused batch goes one message at a time, so one bad
/// UID can't sink the rest.
pub fn move_verified(s: &mut dyn LiveSession, uids: &[String], dest: &str) -> Result<(usize, Vec<String>), LiveError> {
    s.select("INBOX", false)?;
    for chunk in uids.chunks(MOVE_BATCH) {
        match s.move_to(&numbers(chunk), dest) {
            Ok(()) => {}
            Err(LiveError::Lost(class)) => return Err(LiveError::Lost(class)),
            Err(LiveError::Refused(_)) => {
                for uid in chunk {
                    move_one(s, uid, dest)?;
                }
            }
        }
    }
    let present = still_in_inbox(s)?;
    let mut remaining: Vec<String> = uids.iter().filter(|u| present.contains(*u)).cloned().collect();
    if !remaining.is_empty() {
        s.select("INBOX", false)?;
        for uid in &remaining {
            move_one(s, uid, dest)?;
        }
        let present = still_in_inbox(s)?;
        remaining.retain(|u| present.contains(u));
    }
    Ok((uids.len() - remaining.len(), remaining))
}

/// What a sweep of an account that failed answers: the class of what went wrong, never what a server said.
fn failed(account: &str, class: &str) -> Value {
    json!({"account": account, "error": class, "planned": 0})
}

/// What the parts of a sweep are.
#[derive(Debug)]
pub struct SweepsParts {
    pub rules: Arc<dyn RuleBook>,
    pub boxes: Arc<dyn LiveInboxes>,
    pub audit: Arc<dyn SweepTrail>,
    pub rows: Arc<dyn SweptRows>,
    pub filing: Arc<dyn Filing>,
    pub clock: Arc<dyn Clock>,
    /// False in a read-only process, which moves nothing.
    pub writes_allowed: bool,
}

#[derive(Debug)]
pub struct Sweeps {
    rules: Arc<dyn RuleBook>,
    boxes: Arc<dyn LiveInboxes>,
    audit: Arc<dyn SweepTrail>,
    rows: Arc<dyn SweptRows>,
    filing: Arc<dyn Filing>,
    clock: Arc<dyn Clock>,
    writes_allowed: bool,
}

impl Sweeps {
    pub fn new(parts: SweepsParts) -> Sweeps {
        let SweepsParts { rules, boxes, audit, rows, filing, clock, writes_allowed } = parts;
        Sweeps { rules, boxes, audit, rows, filing, clock, writes_allowed }
    }

    /// A dry run's plan, or a verified move of an account's junk buckets to Trash, with the stored rows of what moved
    /// forgotten. What went wrong with the account is in its answer, as the class alone; a sweep trail that can't be
    /// written fails it before anything moves.
    fn sweep(&self, account: &str, trash: &str, dry: bool, rules: &RuleSets) -> Result<Value, Fault> {
        let mut s = match self.boxes.open(account) {
            Ok(s) => s,
            Err(class) => {
                log::warning(TAG, "an account failed", &[(Field::Why, class)], None);
                return Ok(failed(account, class));
            }
        };
        let read = match fetch_headers(&mut *s) {
            Ok(read) => read,
            Err(e) => {
                log::warning(TAG, "an account failed", &[(Field::Why, e.class())], None);
                s.close(matches!(e, LiveError::Lost(_)));
                return Ok(failed(account, e.class()));
            }
        };
        let rows = classified(read, rules);
        let plan = tally(rows.iter().map(|r| (r.bucket, r.read.from.clone())));
        let junk: Vec<&Classified> = rows.iter().filter(|r| r.bucket.is_junk()).collect();
        let senders: HashSet<&str> = junk.iter().map(|r| r.read.from.as_str()).collect();
        let mut out = Map::new();
        out.insert("account".into(), json!(account));
        out.insert("planned".into(), json!(junk.len()));
        out.insert("by_bucket".into(), plan.get("by_bucket").cloned().unwrap_or_else(|| json!({})));
        out.insert("senders".into(), json!(senders.len()));
        out.insert("moved".into(), json!(0));
        out.insert("stragglers".into(), json!([]));
        out.insert("error".into(), Value::Null);
        if dry || junk.is_empty() {
            s.close(false);
            return Ok(Value::Object(out));
        }
        let audited: Vec<Audited> = junk
            .iter()
            .map(|r| Audited {
                uid: r.read.uid.clone(),
                from: r.read.from.clone(),
                subject: r.read.subject.clone(),
                bucket: r.bucket.name().to_owned(),
                reason: r.reason.clone(),
            })
            .collect();
        // Before anything moves, so a crash still records what it meant to do.
        if let Err(fault) = self.audit.swept(account, &audited) {
            s.close(false);
            return Err(fault);
        }
        let uids: Vec<String> = junk.iter().map(|r| r.read.uid.clone()).collect();
        let (moved, stragglers) = match move_verified(&mut *s, &uids, trash) {
            Ok(done) => done,
            Err(e) => {
                log::warning(TAG, "an account failed", &[(Field::Why, e.class())], None);
                s.close(matches!(e, LiveError::Lost(_)));
                out.insert("error".into(), json!(e.class()));
                return Ok(Value::Object(out));
            }
        };
        s.close(false);
        out.insert("moved".into(), json!(moved));
        out.insert("stragglers".into(), json!(stragglers));
        // Swept mail isn't a ghost for the next reconcile to find.
        let gone: Vec<(String, String)> = junk.iter().map(|r| (r.read.uid.clone(), r.read.mid.clone())).collect();
        self.rows.forget_moved(account, &gone, &stragglers)?;
        Ok(Value::Object(out))
    }

    /// `POST /api/sweep`: a sweep a person asked for, of the account named or of every one (`all`, or none named), each
    /// on its own account's gate alone: one account's slow server holds up only its own turn.
    pub fn run(&self, account: Option<&str>, dry_run: bool) -> Result<Value, Failure> {
        if !dry_run && !self.writes_allowed {
            // It moves mail, and its moves can't wait in a queue.
            return Err(READ_ONLY.into());
        }
        let accounts = self.boxes.accounts()?;
        let named = match account.filter(|a| !a.is_empty() && *a != "all") {
            Some(account) => vec![
                accounts.into_iter().find(|a| a.email == account).ok_or_else(|| Problem::refused("unknown account"))?,
            ],
            None => accounts,
        };
        let rules = self.rules.load()?.sets();
        let mut results = Vec::with_capacity(named.len());
        for each in &named {
            let mut job = || self.sweep(&each.email, &each.trash, dry_run, &rules);
            results.push(self.boxes.on_gate(&each.email, &mut job)?);
        }
        if !dry_run {
            self.filing.stats_changed();
        }
        Ok(json!({"ok": true, "dry_run": dry_run, "results": results,
                  "generated": mm_kernel::when::iso(self.clock.now())}))
    }
}

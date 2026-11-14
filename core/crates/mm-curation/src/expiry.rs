//! Shelf life (`mailmind.curation.application.expiry` and `domain.expiry`): INBOX mail past its expiry rule's age
//! moves to Trash. Ages come from the live server, so the answer is right whether or not the store is synced; the move
//! is verified, and audited before it starts. Strictly past the age, and an unknown age never expires.

use std::collections::BTreeSet;
use std::sync::Arc;

use mm_kernel::errors::Fault;
use mm_kernel::log::{self, Field};
use mm_kernel::seams::Clock;
use serde_json::{Value, json};

use crate::cleanup_ports::{Audited, LiveError, LiveInboxes, LiveSession, SweepTrail, SweptAccount, SweptRows};
use crate::ports::{Filing, RuleBook};
use crate::rulebook::is_domain;
use crate::rules::domain_covers;
use crate::sweeping::move_verified;

const TAG: &str = "expire";
/// What expiry asks the server for of each message.
pub const AGE_FIELDS: [&str; 4] = ["FROM", "SUBJECT", "MESSAGE-ID", "DATE"];

/// Hours after which mail from `addr` expires under `expire`, or None. An exact sender's entry wins over a domain's,
/// and a domain covers its sub-domains; of the domains that cover it, the shortest life wins.
pub fn expiry_hours(addr: &str, expire: &[(String, i64)]) -> Option<i64> {
    let addr = addr.to_lowercase();
    if let Some((_, hours)) = expire.iter().find(|(token, _)| *token == addr) {
        return Some(*hours);
    }
    // What follows the first @, as `str.partition` gives it: none, with no @.
    let domain = addr.split_once('@').map_or("", |(_, domain)| domain);
    expire.iter().filter(|(token, _)| is_domain(token) && domain_covers(domain, token)).map(|(_, hours)| *hours).min()
}

/// A message of the INBOX as expiry reads it: when it arrived in Unix seconds, None when that isn't known.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Aged {
    pub uid: String,
    pub from: String,
    pub subject: String,
    pub mid: String,
    pub ts: Option<i64>,
}

/// A message past its rule's age, with that rule's hours.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Expired {
    pub aged: Aged,
    pub hours: i64,
}

/// A message as expiry reads it, from its `AGE_FIELDS` and when the server says it arrived: that, else its Date
/// header, else unknown.
pub fn aged_from_headers(uid: u32, raw: &[u8], arrived: Option<i64>) -> Aged {
    let fields = mm_content::mime::fields(raw);
    let dated = fields.date_iso.is_some().then_some(fields.ts);
    Aged {
        uid: uid.to_string(),
        from: fields.from_addr,
        subject: fields.subject,
        mid: fields.message_id,
        ts: arrived.or(dated),
    }
}

/// The rows whose sender has an expiry rule and that arrived longer ago than it, `now` in seconds.
pub fn select_expired(rows: Vec<Aged>, expire: &[(String, i64)], now: f64) -> Vec<Expired> {
    rows.into_iter()
        .filter_map(|aged| {
            let hours = expiry_hours(&aged.from, expire).filter(|h| *h != 0)?;
            let ts = aged.ts?;
            (now - ts as f64 > (hours * 3600) as f64).then_some(Expired { aged, hours })
        })
        .collect()
}

/// The audit's line for each message expiry takes, written before any of them moves.
pub fn audited(targets: &[Expired]) -> Vec<Audited> {
    targets
        .iter()
        .map(|t| Audited {
            uid: t.aged.uid.clone(),
            from: t.aged.from.clone(),
            subject: t.aged.subject.clone(),
            bucket: "expired".to_owned(),
            reason: format!("expire rule: older than {}h", t.hours),
        })
        .collect()
}

/// What the parts of expiry are.
#[derive(Debug)]
pub struct ExpiryParts {
    pub rules: Arc<dyn RuleBook>,
    pub boxes: Arc<dyn LiveInboxes>,
    pub audit: Arc<dyn SweepTrail>,
    pub rows: Arc<dyn SweptRows>,
    pub filing: Arc<dyn Filing>,
    pub clock: Arc<dyn Clock>,
}

#[derive(Debug)]
pub struct Expiry {
    rules: Arc<dyn RuleBook>,
    boxes: Arc<dyn LiveInboxes>,
    audit: Arc<dyn SweepTrail>,
    rows: Arc<dyn SweptRows>,
    filing: Arc<dyn Filing>,
    clock: Arc<dyn Clock>,
}

/// The INBOX's mail under a rule, read live and aged: what is past its rule's age at `now`, in seconds.
fn expired(s: &mut dyn LiveSession, expire: &[(String, i64)], now: f64) -> Result<Vec<Expired>, LiveError> {
    s.select("INBOX", true)?;
    let mut uids = BTreeSet::new();
    for (token, _) in expire {
        match s.uids_from(token) {
            Ok(found) => uids.extend(found),
            // A refused search finds nothing; the rule matches exactly what the others found.
            Err(LiveError::Refused(_)) => {}
            Err(lost) => return Err(lost),
        }
    }
    if uids.is_empty() {
        return Ok(Vec::new());
    }
    let uids: Vec<u32> = uids.into_iter().collect();
    let arrivals = s.arrivals(&uids)?;
    let rows = s.headers(&uids, &AGE_FIELDS)?;
    let rows = rows.into_iter().map(|(uid, raw)| aged_from_headers(uid, &raw, arrivals.get(&uid).copied()));
    Ok(select_expired(rows.collect(), expire, now))
}

/// What one account's pass answers: the class of what went wrong, never what a server said.
fn answer(account: &str, planned: usize, moved: usize, stragglers: &[String], error: Option<&str>) -> Value {
    json!({"account": account, "planned": planned, "moved": moved, "stragglers": stragglers, "error": error})
}

/// A pass the server ended: the session hung up where the connection went, else logged out.
fn failed(s: &mut dyn LiveSession, account: &str, planned: usize, e: LiveError) -> Value {
    log::warning(TAG, "an account failed", &[(Field::Why, e.class())], None);
    s.close(matches!(e, LiveError::Lost(_)));
    answer(account, planned, 0, &[], Some(e.class()))
}

impl Expiry {
    pub fn new(parts: ExpiryParts) -> Expiry {
        let ExpiryParts { rules, boxes, audit, rows, filing, clock } = parts;
        Expiry { rules, boxes, audit, rows, filing, clock }
    }

    /// One account's pass: its INBOX mail past an expiry rule moved to Trash, verified and audited first, and the
    /// stored rows of what moved forgotten by Message-ID. What went wrong with the server, or with the audit, is in the
    /// answer, as its class alone; a dry run moves nothing.
    pub fn account(&self, account: &SweptAccount, dry: bool) -> Result<Value, Fault> {
        let email = account.email.as_str();
        let expire = self.rules.load()?.expire_hours;
        if expire.is_empty() {
            return Ok(answer(email, 0, 0, &[], None));
        }
        let mut s = match self.boxes.open(email) {
            Ok(s) => s,
            Err(class) => {
                log::warning(TAG, "an account failed", &[(Field::Why, class)], None);
                return Ok(answer(email, 0, 0, &[], Some(class)));
            }
        };
        let targets = match expired(&mut *s, &expire, self.clock.now().seconds()) {
            Ok(targets) => targets,
            Err(e) => return Ok(failed(&mut *s, email, 0, e)),
        };
        let planned = targets.len();
        if dry || targets.is_empty() {
            s.close(false);
            return Ok(answer(email, planned, 0, &[], None));
        }
        // Before anything moves, so a crash still records what it meant to do.
        if let Err(fault) = self.audit.swept(email, &audited(&targets)) {
            log::warning(TAG, "an account failed", &[(Field::Why, fault.class)], None);
            s.close(false);
            return Ok(answer(email, planned, 0, &[], Some(fault.class)));
        }
        let uids: Vec<String> = targets.iter().map(|t| t.aged.uid.clone()).collect();
        let (moved, stragglers) = match move_verified(&mut *s, &uids, &account.trash) {
            Ok(done) => done,
            Err(e) => return Ok(failed(&mut *s, email, planned, e)),
        };
        s.close(false);
        // Expired mail isn't a ghost for the next reconcile to find.
        let gone: Vec<(String, String)> = targets.into_iter().map(|t| (t.aged.uid, t.aged.mid)).collect();
        self.rows.forget_expired(email, &gone, &stragglers)?;
        Ok(answer(email, planned, moved, &stragglers, None))
    }

    /// One account's turn in the background round, on its gate as maintenance: it gives way to anyone holding the
    /// gate, and tries again next round. What moved changes the folders' counts.
    pub fn auto_one(&self, account: &SweptAccount) -> Result<(), Fault> {
        let Some(res) = self.boxes.on_gate_unless_held(&account.email, &mut || self.account(account, false))? else {
            return Ok(());
        };
        if let Some(class) = res.get("error").and_then(Value::as_str) {
            log::warning(TAG, "an account's turn failed", &[(Field::Why, class)], None);
            return Ok(());
        }
        let moved = res.get("moved").and_then(Value::as_u64).unwrap_or(0);
        if moved == 0 {
            return Ok(());
        }
        let moved = usize::try_from(moved).unwrap_or(usize::MAX);
        self.filing.folders_changed(&account.email, moved)?;
        log::info(TAG, "moved %(count)s expired message(s) to Trash", &[(Field::Count, &moved.to_string())]);
        Ok(())
    }

    /// One pass of the expiry rules over every account, when there are any: one account failing stops none of the
    /// others.
    pub fn round(&self) -> Result<(), Fault> {
        if self.rules.load()?.expire_hours.is_empty() {
            return Ok(());
        }
        for account in self.boxes.accounts()? {
            if let Err(fault) = self.auto_one(&account) {
                log::warning(TAG, "loop error", &[], Some(fault.class));
            }
        }
        Ok(())
    }
}

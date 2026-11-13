//! Cleaning up after one sender, or many (`mailmind.curation.application.cleanup` and `domain.cleanup_plan`): what it
//! would do, and doing it. The block and the sweep can be undone, so they stand whatever comes of the unsubscribe,
//! which can't be and wants a preview's token. The sweep goes through the Mailbox's filing, a message at a time as a
//! click on it would, so a server that's away queues it.

use std::collections::HashMap;
use std::sync::Arc;

use mm_kernel::errors::{Failure, Fault, NOT_YET, Problem};
use mm_kernel::log;
use mm_kernel::seams::Clock;
use serde_json::{Value, json};

use crate::cleanup_ports::{CleanupFiling, CleanupMail, ConfirmTokens, Ids, NamedCopy, SweepTrail, Target};
use crate::ports::{RuleBook, SenderMail};
use crate::rulebook::{domain_of, token_of};
use crate::screener::reclassify;
use crate::unsubscribe::{self, TOKEN_LIFE, TOKEN_PURPOSE, Unsubscribing};

const TAG: &str = "cleanup";
/// A sender's sweep moves at most this many: a click waits on them.
pub const SWEEP_CAP: usize = 200;
/// Senders one batch may name: what the list of them shows.
pub const BATCH_MAX: usize = 40;
/// Moves one batch makes, in all: its undo group is rewritten with each, and a click waits on it.
pub const BATCH_SWEEP_CAP: usize = 500;
/// How far a block goes: this sender, or their whole domain.
pub const SENDER: &str = "sender";
pub const DOMAIN: &str = "domain";

/// What a cleanup can take back.
pub fn reversible() -> Value {
    json!({"block": true, "sweep": true, "unsubscribe": false})
}

/// The sender a request names, as rules and rows go by it. None if that's no address.
pub fn sender_named(raw: Option<&str>) -> Option<String> {
    let addr = token_of(raw.unwrap_or_default());
    addr.contains('@').then_some(addr)
}

/// The distinct senders a batch names, as rules and rows go by them, or None if it names none, too many, or anything
/// that isn't an address.
pub fn senders_named(raw: Option<&[String]>) -> Option<Vec<String>> {
    let raw = raw.filter(|r| !r.is_empty() && r.len() <= BATCH_MAX)?;
    let mut named: Vec<String> = Vec::with_capacity(raw.len());
    for one in raw {
        let addr = sender_named(Some(one))?;
        if !named.contains(&addr) {
            named.push(addr);
        }
    }
    Some(named)
}

/// What a sweep takes of `read`, which asked for one past the cap, and whether that leaves any behind.
pub fn within_cap<T>(mut read: Vec<T>) -> (Vec<T>, bool) {
    let capped = read.len() > SWEEP_CAP;
    read.truncate(SWEEP_CAP);
    (read, capped)
}

/// A sender, and the INBOX copies of theirs a batch sweeps.
pub type Swept = (String, Vec<Target>);

/// What a batch sweeps of each sender, from each one's own `within_cap`, and whether anything was left behind. The
/// moves are shared out in the order the senders came.
pub fn within_batch<T>(read: Vec<(String, Vec<T>, bool)>) -> (Vec<(String, Vec<T>)>, bool) {
    let mut left = BATCH_SWEEP_CAP;
    let mut capped = false;
    let mut taken = Vec::with_capacity(read.len());
    for (addr, mut targets, hit_own_cap) in read {
        let had = targets.len();
        targets.truncate(left);
        capped = capped || hit_own_cap || targets.len() < had;
        left -= targets.len();
        taken.push((addr, targets));
    }
    (taken, capped)
}

/// What a person confirmed for one sender. `scope` says how far the block goes.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Plan {
    pub from_addr: String,
    pub scope: &'static str,
    pub do_unsubscribe: bool,
    pub confirm_token: String,
}

impl Plan {
    /// What the block names: the sender, or their domain.
    pub fn block_token(&self) -> String {
        if self.scope == SENDER { self.from_addr.clone() } else { domain_of(&self.from_addr).to_owned() }
    }
}

/// What a cleanup's request says (`CleanupBody`).
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Asked {
    pub from_addr: Option<String>,
    pub block_scope: Option<String>,
    pub do_unsubscribe: bool,
    pub confirm_token: Option<String>,
    pub undo_group: Option<String>,
}

/// A request as a plan, or None if it names no sender. Only asking for the domain widens a block.
pub fn plan_from(asked: &Asked) -> Option<Plan> {
    Some(Plan {
        from_addr: sender_named(asked.from_addr.as_deref())?,
        scope: if asked.block_scope.as_deref() == Some(DOMAIN) { DOMAIN } else { SENDER },
        do_unsubscribe: asked.do_unsubscribe,
        confirm_token: asked.confirm_token.clone().unwrap_or_default(),
    })
}

/// What filing each message came to. A queued one counts as swept: it has left the lists, and the worker has it.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Tally {
    pub moved: usize,
    pub queued: usize,
    pub failed: usize,
}

impl Tally {
    pub fn swept(self) -> usize {
        self.moved + self.queued
    }
}

/// Each answer filing gave, one a message. One that failed is no answer, and counts as failed.
pub fn tally(answers: impl IntoIterator<Item = Value>) -> Tally {
    let mut tally = Tally::default();
    for answer in answers {
        if answer.get("status").and_then(Value::as_str) == Some("queued") {
            tally.queued += 1;
        } else if answer.get("ok").is_some_and(truthy) {
            tally.moved += 1;
        } else {
            tally.failed += 1;
        }
    }
    tally
}

fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// Is this a token at all, and of the last `life` seconds (`tokens.in_date`)? Asked first, as going on costs something.
pub fn in_date(token: &str, now: i64, life: i64) -> bool {
    let ts = token.split_once('.').map_or(token, |(ts, _)| ts);
    if ts.is_empty() || ts.len() > 12 || !ts.bytes().all(|b| b.is_ascii_digit()) {
        return false;
    }
    ts.parse::<i64>().is_ok_and(|ts| (0..=life).contains(&(now - ts)))
}

/// What a sender's cleanup is made of.
#[derive(Debug)]
pub struct CleaningParts {
    pub rules: Arc<dyn RuleBook>,
    /// Stored mail as a rule change classifies it again.
    pub classified: Arc<dyn SenderMail>,
    pub mail: Arc<dyn CleanupMail>,
    pub unsubscribing: Unsubscribing,
    pub filing: Arc<dyn CleanupFiling>,
    pub audit: Arc<dyn SweepTrail>,
    pub tokens: Arc<dyn ConfirmTokens>,
    pub ids: Arc<dyn Ids>,
    pub clock: Arc<dyn Clock>,
}

#[derive(Debug)]
pub struct Cleaning {
    rules: Arc<dyn RuleBook>,
    classified: Arc<dyn SenderMail>,
    mail: Arc<dyn CleanupMail>,
    unsubscribing: Unsubscribing,
    filing: Arc<dyn CleanupFiling>,
    audit: Arc<dyn SweepTrail>,
    tokens: Arc<dyn ConfirmTokens>,
    ids: Arc<dyn Ids>,
    clock: Arc<dyn Clock>,
}

impl Cleaning {
    pub fn new(parts: CleaningParts) -> Cleaning {
        let CleaningParts { rules, classified, mail, unsubscribing, filing, audit, tokens, ids, clock } = parts;
        Cleaning { rules, classified, mail, unsubscribing, filing, audit, tokens, ids, clock }
    }

    /// An undo group of a cleanup's own, never shared with an earlier one.
    fn group(&self, asked: Option<&str>) -> String {
        match asked {
            Some(group) if !group.is_empty() => group.to_owned(),
            _ => format!("cleanup:{}", self.ids.hex(16)),
        }
    }

    fn block(&self, token: &str, label: &str, group: &str) -> Result<(), Failure> {
        let token = token_of(token);
        self.rules.change(&mut |rules| {
            rules.add(&token, "block");
            Ok(())
        })?;
        self.filing.undo_push(&json!({"kind": "unblock", "token": token, "label": label}), group)?;
        Ok(())
    }

    /// A cleanup's record on the trail. One that can't be written stops nothing.
    fn cleaned_up(&self, from_addr: &str, token: &str, swept: usize, status: &str) {
        if let Err(fault) = self.audit.cleaned_up(from_addr, token, swept, status) {
            log::warning(TAG, "a cleanup's record wasn't written", &[], Some(fault.class));
        }
    }

    fn targets(&self, addr: &str) -> Result<(Vec<Target>, bool), Fault> {
        Ok(within_cap(self.mail.sweep_targets(addr, SWEEP_CAP + 1)?))
    }

    /// `GET /api/cleanup/sender`: the preview a cleanup must come from, for one sender: the way out of its list, how
    /// many INBOX messages a sweep would move, and the confirm token the cleanup wants. An address that won't read is
    /// refused, and a sender with no mail stored isn't found.
    pub fn sender(&self, addr: Option<&str>) -> Result<Value, Failure> {
        let addr = sender_named(addr).ok_or_else(|| Problem::refused("bad address"))?;
        let (count, unseen) = self.mail.counts(&addr)?;
        if count == 0 {
            return Err(Problem::not_found("no such sender").into());
        }
        let (targets, capped) = self.targets(&addr)?;
        let (info, accounts) = self.unsubscribing.resolve(&addr)?;
        let token =
            self.tokens.mint(TOKEN_PURPOSE, &unsubscribe::preview_said(&addr, &info), self.clock.now().whole_seconds());
        Ok(json!({
            "ok": true, "from_addr": addr, "count": count, "unseen": unseen, "accounts": accounts,
            "domain": domain_of(&addr), "unsub": unsubscribe::preview(&info), "sweep_count": targets.len(),
            "sweep_capped": capped, "reversible": reversible(), "confirm_token": token,
        }))
    }

    /// `POST /api/cleanup/execute`: the cleanup of one sender. First, and reversible: a block rule, and the sender's
    /// INBOX mail to Trash, under one undo group, so one Undo takes both back. Then, best-effort and only with a good
    /// token from the preview, the unsubscribe, which can't be taken back. What it would send, this server must be
    /// able to send before anything changes; otherwise nothing does.
    pub fn execute(&self, asked: &Asked) -> Result<Value, Failure> {
        let plan = plan_from(asked).ok_or_else(|| Problem::refused("bad address"))?;
        let from = plan.from_addr.as_str();
        let block_token = plan.block_token();
        let group = self.group(asked.undo_group.as_deref());
        // The way out is read from the sender's stored mail, so before the sweep, which deletes the rows it moves.
        let now = self.clock.now().whole_seconds();
        let resolved = if plan.do_unsubscribe && in_date(&plan.confirm_token, now, TOKEN_LIFE) {
            Some(self.unsubscribing.resolve(from)?)
        } else {
            None
        };
        let next = match &resolved {
            Some(resolved)
                if self.tokens.names(
                    &plan.confirm_token,
                    TOKEN_PURPOSE,
                    &unsubscribe::preview_said(from, &resolved.0),
                ) =>
            {
                Some(self.unsubscribing.next(from, resolved)?)
            }
            _ => None,
        };
        if next.as_ref().is_some_and(|next| !self.unsubscribing.ready(next)) {
            return Err(NOT_YET.into());
        }
        self.block(&block_token, &format!("Cleaned up {from}"), &group)?;
        let (targets, capped) = self.targets(from)?;
        let swept = tally(targets.iter().map(|t| self.filing.trash(t.pk, &group).unwrap_or(Value::Null)));
        // What stays, in other folders, reads as blocked too.
        reclassify(&*self.rules, &*self.classified, &block_token)?;
        self.filing.stats_changed();
        let tried = match (next, &resolved) {
            (Some(next), Some(resolved)) => self.unsubscribing.attempt(from, resolved, next)?,
            _ => unsubscribe::not_tried(plan.do_unsubscribe),
        };
        self.cleaned_up(from, &block_token, swept.swept(), &tried.status);
        Ok(json!({
            "ok": true, "status": tried.status, "method": tried.method, "blocked_token": block_token,
            "block_scope": plan.scope, "swept": swept.swept(), "sweep_failed": swept.failed,
            "sweep_queued": swept.queued, "sweep_capped": capped, "browser_url": tried.browser_url,
            "http_status": tried.http_status, "undo_group": group, "reversible": reversible(),
        }))
    }

    /// What a batch of these senders would sweep of each, and whether any of it is left behind.
    fn batch_sweeps(&self, addrs: &[String]) -> Result<(Vec<Swept>, bool), Fault> {
        let mut read = Vec::with_capacity(addrs.len());
        for addr in addrs {
            let (targets, capped) = self.targets(addr)?;
            read.push((addr.clone(), targets, capped));
        }
        Ok(within_batch(read))
    }

    /// `POST /api/cleanup/batch/preview`: what cleaning up these senders together would do, before anyone says yes:
    /// each one's mail and how many of its INBOX copies go.
    pub fn batch_preview(&self, senders: Option<&[String]>) -> Result<Value, Failure> {
        let addrs = senders_named(senders).ok_or_else(|| Problem::refused("bad senders"))?;
        let (sweeps, capped) = self.batch_sweeps(&addrs)?;
        let mut shown = Vec::new();
        let mut sweep_count = 0;
        for (addr, targets) in &sweeps {
            let (count, unseen) = self.mail.counts(addr)?;
            if count != 0 {
                sweep_count += targets.len();
                shown.push(json!({"from_addr": addr, "count": count, "unseen": unseen, "sweep_count": targets.len()}));
            }
        }
        if shown.is_empty() {
            return Err(Problem::not_found("no such sender").into());
        }
        Ok(json!({"ok": true, "senders": shown, "sweep_count": sweep_count, "sweep_capped": capped,
                  "reversible": reversible()}))
    }

    /// `POST /api/cleanup/batch`: many senders at once: each one's block, then every sweep, under one undo group, so a
    /// single Undo takes the whole batch back. Only the reversible half: an unsubscribe can't be taken back, and each
    /// wants its own preview's token.
    pub fn batch_execute(&self, senders: Option<&[String]>, undo_group: Option<&str>) -> Result<Value, Failure> {
        let addrs = senders_named(senders).ok_or_else(|| Problem::refused("bad senders"))?;
        let group = self.group(undo_group);
        let (sweeps, capped) = self.batch_sweeps(&addrs)?;
        let label = format!("Cleaned up {} senders", addrs.len());
        for (addr, _) in &sweeps {
            self.block(addr, &label, &group)?;
        }
        // Each copy, by its key, folder and uid, and whose it is: a copy two senders' lists both hold is the later's.
        let mut copies: Vec<NamedCopy> = Vec::new();
        let mut sender_of: HashMap<NamedCopy, String> = HashMap::new();
        for (addr, targets) in &sweeps {
            for t in targets {
                let Some(key) = t.key else { continue };
                let copy = (key, t.folder.clone(), t.uid);
                if sender_of.insert(copy.clone(), addr.clone()).is_none() {
                    copies.push(copy);
                }
            }
        }
        let said = self.sweep_many(&copies, &group);
        for (addr, _) in &sweeps {
            reclassify(&*self.rules, &*self.classified, addr)?;
        }
        self.filing.stats_changed();
        let mut swept_of: HashMap<&str, usize> = HashMap::new();
        for filed in said.get("filed").and_then(Value::as_array).into_iter().flatten() {
            let copy = (
                filed.get("key").and_then(Value::as_i64).unwrap_or_default(),
                filed.get("folder").and_then(Value::as_str).unwrap_or_default().to_owned(),
                filed.get("uid").and_then(Value::as_i64),
            );
            if let Some(addr) = sender_of.get(&copy) {
                *swept_of.entry(addr.as_str()).or_default() += 1;
            }
        }
        for (addr, _) in &sweeps {
            self.cleaned_up(addr, addr, swept_of.get(addr.as_str()).copied().unwrap_or(0), "skipped");
        }
        let number = |name: &str| said.get(name).cloned().unwrap_or(json!(0));
        Ok(json!({
            "ok": true, "blocked": addrs, "swept": number("count"), "sweep_failed": number("failed"),
            "sweep_queued": number("queued"), "sweep_capped": capped, "undo_group": group, "reversible": reversible(),
        }))
    }

    /// Trash these copies as filing marked rows does: one transaction accepts them all under the group, then each is
    /// tried. Where that fails, none of them went: each is where it was.
    fn sweep_many(&self, copies: &[NamedCopy], group: &str) -> Value {
        if copies.is_empty() {
            return json!({"count": 0, "failed": 0, "queued": 0, "filed": []});
        }
        self.filing
            .trash_many(copies, group)
            .unwrap_or_else(|_| json!({"count": 0, "failed": copies.len(), "queued": 0, "filed": []}))
    }
}

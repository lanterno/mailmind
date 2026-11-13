//! Who to clean up after first (`mailmind.curation.domain.noise`, `application.cleanup.Cleanup.senders`): the senders
//! of the most unread recent marketing and notifications, which no rule decides yet. The database alone: no AI, no
//! mail server.

use std::sync::Arc;

use mm_kernel::errors::Failure;
use mm_kernel::relative::rel_time;
use mm_kernel::seams::Clock;
use serde_json::{Value, json};

use crate::classifier::Bucket;
use crate::ports::{NoisySender, RuleBook, SenderMail};
use crate::rulebook::{decided, domain_of};

/// Whose mail is worth asking about.
pub const BUCKETS: [&str; 2] = [Bucket::Marketing.name(), Bucket::Notifications.name()];
/// How many messages make a candidate.
pub const MIN_COUNT: i64 = 3;
/// How many are listed.
pub const LIMIT: usize = 40;
const MIN_SCORE: f64 = 1.5;

/// More mail, more of it unread and more recent all score higher.
pub fn noise_score(count: i64, unseen: i64, age_days: f64) -> f64 {
    let ratio = if count != 0 { unseen as f64 / count as f64 } else { 0.0 };
    count as f64 * (0.3 + 0.7 * ratio) * (-age_days / 30.0).exp()
}

/// How long since a sender last wrote. With no date, or one ahead of now, it counts as today.
pub fn age_in_days(last_ts: Option<i64>, now: f64) -> f64 {
    let last = match last_ts {
        Some(ts) if ts != 0 => ts as f64,
        _ => now,
    };
    ((now - last) / 86_400.0).max(0.0)
}

/// `round(x, 2)`: the double nearest the decimal `x` rounds to.
fn round2(x: f64) -> f64 {
    format!("{x:.2}").parse().unwrap_or(x)
}

/// A sender worth cleaning up after, and the score its numbers come to.
#[derive(Clone, Debug, PartialEq)]
pub struct Candidate {
    pub addr: String,
    pub name: String,
    pub domain: String,
    pub count: i64,
    pub unseen: i64,
    pub unseen_ratio: f64,
    pub last_iso: Option<String>,
    pub bucket: Option<String>,
    /// A newsletter most likely offers a way out, before anyone has looked.
    pub list_likely: bool,
    pub score: f64,
}

fn candidate(row: &NoisySender, now: f64) -> Option<Candidate> {
    let (n, unseen) = (row.n, row.unseen);
    let score = noise_score(n, unseen, age_in_days(row.last_ts, now));
    if score < MIN_SCORE {
        return None;
    }
    Some(Candidate {
        addr: row.addr.clone(),
        name: row.name.clone().filter(|n| !n.is_empty()).unwrap_or_else(|| row.addr.clone()),
        domain: domain_of(&row.addr).to_owned(),
        count: n,
        unseen,
        unseen_ratio: round2(if n != 0 { unseen as f64 / n as f64 } else { 0.0 }),
        last_iso: row.last_iso.clone(),
        bucket: row.bucket.clone(),
        list_likely: row.bucket.as_deref() == Some(Bucket::Marketing.name()),
        score: round2(score),
    })
}

/// The noisiest of `rows`, down to MIN_SCORE, by the score as shown, and those it ties with in the order they came.
/// A score just under MIN_SCORE stays out, though it would round up to it.
pub fn ranked(rows: &[NoisySender], now: f64, limit: usize) -> Vec<Candidate> {
    let mut candidates: Vec<Candidate> = rows.iter().filter_map(|r| candidate(r, now)).collect();
    candidates.sort_by(|a, b| b.score.total_cmp(&a.score));
    candidates.truncate(limit);
    candidates
}

#[derive(Debug)]
pub struct Cleanup {
    rules: Arc<dyn RuleBook>,
    mail: Arc<dyn SenderMail>,
    clock: Arc<dyn Clock>,
}

impl Cleanup {
    pub fn new(rules: Arc<dyn RuleBook>, mail: Arc<dyn SenderMail>, clock: Arc<dyn Clock>) -> Cleanup {
        Cleanup { rules, mail, clock }
    }

    /// `GET /api/cleanup/senders`: the ranked candidates, each with how much of its mail each account holds.
    pub fn senders(&self) -> Result<Value, Failure> {
        let sets = self.rules.load()?.sets();
        let rows: Vec<NoisySender> =
            self.mail.noisy_senders(&BUCKETS, MIN_COUNT)?.into_iter().filter(|r| !decided(&r.addr, &sets)).collect();
        let now = self.clock.now();
        let ranked = ranked(&rows, now.seconds(), LIMIT);
        let addrs: Vec<String> = ranked.iter().map(|c| c.addr.clone()).collect();
        let by_account = if addrs.is_empty() { Default::default() } else { self.mail.accounts_by_sender(&addrs)? };
        let senders: Vec<Value> = ranked
            .iter()
            .map(|c| {
                let accounts: Vec<Value> = by_account
                    .get(&c.addr)
                    .map(|held| held.iter().map(|(email, count)| json!({"email": email, "count": count})).collect())
                    .unwrap_or_default();
                json!({
                    "from_addr": c.addr, "from_name": c.name, "domain": c.domain, "count": c.count, "unseen": c.unseen,
                    "unseen_ratio": c.unseen_ratio, "last_seen": rel_time(c.last_iso.as_deref(), now),
                    "bucket": c.bucket, "lu_likely": c.list_likely, "noise_score": c.score, "accounts": accounts,
                })
            })
            .collect();
        Ok(json!({"ok": true, "generated": mm_kernel::when::iso(now), "senders": senders}))
    }
}

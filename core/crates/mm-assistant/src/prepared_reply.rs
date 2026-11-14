//! A reply made ready ahead of time (`domain.prepared_reply`): which one it is, which mail may have one, when a stored
//! one may be served, and how drafting ahead backs off. The rules alone, with no I/O.
//!
//! A stored row is served only for the thread as it is now and while it is young. Its fingerprint is the correctness
//! gate, and it is taken before the generation, so a thread that changes meanwhile costs another generation and never
//! a stale serve.

use crate::ports::{Message, StoredDraft};
use crate::prompts;
use crate::text::strip;

/// Servable age: the fingerprint is the correctness gate, this is hygiene against drift.
pub const TTL: i64 = 24 * 3600;
/// After a failure, opening the message won't ask again for this long.
pub const FAIL_TTL: i64 = 60;
/// The background round's backoff after the first failure...
pub const FAIL_BASE: i64 = 300;
/// ...doubling per failure in a row, up to six hours.
pub const FAIL_CAP: i64 = 6 * 3600;
/// Generations in a row that failed, for the round's breaker...
pub const TRIP: u32 = 3;
/// ...which stays open for half an hour.
pub const SUSPEND: i64 = 1800;

/// A prepared reply's identity, (account, `t:<thread>` or `m:<Message-ID>`), which survives a folder being
/// renumbered. None for mail with neither: nothing is drafted ahead for it.
pub fn key_of(row: &Message) -> Option<(String, String)> {
    let tid = strip(row.thread_id.as_deref().unwrap_or_default());
    if !tid.is_empty() {
        return Some((row.account.clone(), format!("t:{tid}")));
    }
    let mid = strip(row.message_id.as_deref().unwrap_or_default());
    (!mid.is_empty()).then(|| (row.account.clone(), format!("m:{mid}")))
}

/// Replyable to a real human, and not already handled. `looks_human` is Curation's word on a sender's local part.
pub fn eligible(row: &Message, looks_human: impl Fn(&str) -> bool) -> bool {
    if row.is_junk || row.answered || row.snoozed {
        return false;
    }
    let from = row.from_addr.as_deref().unwrap_or_default().to_lowercase();
    if from == row.account.to_lowercase() || row.folder.as_deref().unwrap_or_default().to_lowercase().contains("sent") {
        return false;
    }
    let local = from.split('@').next().unwrap_or_default();
    !local.is_empty() && looks_human(local)
}

/// A thread's prepared reply answers its anchor, so it never pairs with a composer quoting an older message.
pub fn for_the_anchor(pk: i64, thread_id: Option<&str>, anchor_pk: Option<i64>) -> bool {
    thread_id.is_none_or(str::is_empty) || anchor_pk == Some(pk)
}

/// A stored draft for the mail as it is now: it has a body, the fingerprint matches, and it is under TTL old.
pub fn servable(stored: Option<&StoredDraft>, fp: &str, now: i64) -> bool {
    stored.is_some_and(|s| {
        s.body.as_deref().is_some_and(|b| !b.is_empty()) && s.fp.as_deref() == Some(fp) && now - s.created_ts < TTL
    })
}

/// Opening the message shouldn't ask again so soon after a failure.
pub fn failed_lately(stored: Option<&StoredDraft>, now: i64) -> bool {
    stored.is_some_and(|s| s.fail_ts != 0 && now - s.fail_ts < FAIL_TTL)
}

/// The background round's backoff, which outlives a restart: 5 minutes after one failure, doubling to 6 hours.
pub fn backing_off(stored: Option<&StoredDraft>, now: i64) -> bool {
    let Some(s) = stored.filter(|s| s.fail_ts != 0) else { return false };
    let doublings = u32::try_from((s.fail_count - 1).clamp(0, 20)).unwrap_or(0);
    let wait = FAIL_CAP.min(FAIL_BASE * 2_i64.pow(doublings));
    now - s.fail_ts < wait
}

/// A reply asked for with no instruction and the default tone: what a prepared one is.
pub fn default_shaped(instruction: &str, tone: &str) -> bool {
    strip(instruction).is_empty() && tone == prompts::DEFAULT_TONE
}

/// The background round's three strikes: generations it led that failed, in a row, stop it for SUSPEND seconds.
#[derive(Clone, Copy, Debug, Default)]
pub struct Breaker {
    fails: u32,
    open_until: f64,
}

impl Breaker {
    pub fn is_open(&self, now: f64) -> bool {
        now < self.open_until
    }

    /// What a generation came to: Some(true) led and worked, Some(false) led and failed, None neither. True when this
    /// one tripped the breaker.
    pub fn heard(&mut self, outcome: Option<bool>, now: f64) -> bool {
        match outcome {
            Some(true) => self.fails = 0,
            Some(false) => self.fails += 1,
            None => {}
        }
        if self.fails < TRIP {
            return false;
        }
        self.open_until = now + SUSPEND as f64;
        self.fails = 0;
        true
    }
}

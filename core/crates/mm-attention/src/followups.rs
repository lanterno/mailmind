//! A follow-up: a thread that waits on someone, and how long it may (`mailmind.attention.domain.followups`), and the
//! use cases over them (`application.followups`): the minute's round, a dismissal, Reply Later and who you wait on.
//!
//! A thread has at most one, kept by account and thread id, live or not. Its kind says who owes what: `sent` (you
//! wrote and they haven't answered), `received` (they wrote and you haven't) or `replylater` (you pinned it to
//! answer). It is `pending`, then `due`, unless a person has `dismissed` it, and a dismissed one stays so. The round's
//! rules run as SQL over every thread at once, in the store.

use std::sync::Arc;

use mm_kernel::errors::{Failure, Problem};
use mm_kernel::relative::{ago, rel_time};
use mm_kernel::seams::Clock;
use serde_json::{Value, json};

use crate::ports::{Accounts, AttentionBook, FollowUpChange, WaitingRow};
use crate::threads::{Named, Threads};

/// The grace before a sent-no-reply or unanswered thread surfaces as Waiting, in days.
pub const GRACE_DAYS: i64 = 3;
/// How long a received nudge nags, in days: a reply from another client is invisible here.
pub const EXPIRE_DAYS: i64 = 14;
const DAY: i64 = 86_400;

pub const SENT: &str = "sent";
pub const RECEIVED: &str = "received";
pub const REPLY_LATER: &str = "replylater";
pub const PENDING: &str = "pending";
pub const DUE: &str = "due";
pub const DISMISSED: &str = "dismissed";

pub fn days_before(now: i64, days: i64) -> i64 {
    now - days * DAY
}

/// One thread's row.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct FollowUp {
    pub kind: String,
    pub state: String,
    pub first_sent_ts: Option<i64>,
    pub last_msg_ts: Option<i64>,
    pub due_ts: Option<i64>,
    pub created_at: Option<String>,
    pub resolved_at: Option<String>,
}

/// The thread pinned to Reply Later. That supersedes any nudge, and re-activates a dismissal, which only a person
/// asking may. What it says of when a send went out and came due stays.
pub fn pinned(current: Option<FollowUp>, last_msg_ts: Option<i64>, when: &str) -> FollowUp {
    let base = current.unwrap_or(FollowUp {
        kind: REPLY_LATER.to_owned(),
        state: DUE.to_owned(),
        first_sent_ts: None,
        last_msg_ts: None,
        due_ts: None,
        created_at: None,
        resolved_at: None,
    });
    FollowUp {
        kind: REPLY_LATER.to_owned(),
        state: DUE.to_owned(),
        last_msg_ts,
        created_at: Some(when.to_owned()),
        resolved_at: None,
        ..base
    }
}

/// A person dismissed it. The row stays, so that nothing starts another on this thread. With none, nothing to write.
pub fn dismissed(current: Option<FollowUp>, when: &str) -> Option<FollowUp> {
    current.map(|f| FollowUp { state: DISMISSED.to_owned(), resolved_at: Some(when.to_owned()), ..f })
}

/// Un-pinning ends only a pin: a nudge on the thread isn't the stack's to take.
pub fn unpinning_ends(current: Option<&FollowUp>) -> bool {
    current.is_some_and(|f| f.kind == REPLY_LATER)
}

/// A thread waiting on someone: on the other side if it was sent (who is the first address it went to), else on you
/// (`mailbox.domain.listing.waiting_item`).
pub fn waiting_item(r: &WaitingRow, now: mm_kernel::seams::Moment) -> Value {
    let when = rel_time(r.date_iso.as_deref(), now);
    let label = if r.kind == SENT {
        format!("Sent {} ago · no reply", ago(r.first_sent, now))
    } else {
        format!("Awaiting your reply · {when}")
    };
    let from_name = r.from_name.as_deref().filter(|n| !n.is_empty()).or(r.from_addr.as_deref());
    let subject = r.subject.as_deref().filter(|s| !s.is_empty()).unwrap_or("(no subject)");
    let snippet: String = r.snippet.as_deref().unwrap_or_default().chars().take(200).collect();
    let to = r.recipients.as_deref().unwrap_or_default().split(',').next().unwrap_or_default();
    json!({
        "key": r.key, "folder": r.folder, "uid": r.uid, "from_name": from_name, "from_addr": r.from_addr,
        "subject": subject, "when": when, "snippet": snippet, "seen": r.seen, "flagged": r.flagged,
        "has_attach": r.has_attach, "is_junk": r.is_junk, "bucket": r.bucket, "thread_id": r.thread_id, "ts": r.ts,
        "count": r.thread_count, "waiting": true, "account": r.account, "waiting_label": label, "kind": r.kind,
        "to": mm_kernel::text::strip(to),
    })
}

#[derive(Debug)]
pub struct FollowUps {
    book: Arc<dyn AttentionBook>,
    threads: Threads,
    accounts: Arc<dyn Accounts>,
    clock: Arc<dyn Clock>,
}

impl FollowUps {
    pub fn new(book: Arc<dyn AttentionBook>, accounts: Arc<dyn Accounts>, clock: Arc<dyn Clock>) -> FollowUps {
        FollowUps { threads: Threads::new(book.clone(), accounts.clone()), book, accounts, clock }
    }

    fn now_iso(&self) -> String {
        mm_kernel::when::iso(self.clock.now())
    }

    /// Resolve met commitments, expire stale ones, then promote and detect Waiting threads. The database alone, so it
    /// runs read-only too. Whether the due ones changed.
    pub fn tick(&self) -> Result<bool, Failure> {
        let mut accounts = self.accounts.emails()?;
        accounts.sort();
        accounts.dedup();
        let now = self.clock.now().whole_seconds();
        Ok(self.book.follow_up_round(&accounts, now, &self.now_iso())?)
    }

    /// `POST /api/followups/dismiss`.
    pub fn dismiss(&self, named: &Named) -> Result<Value, Failure> {
        let Some((account, thread_id)) = self.threads.target(named)? else {
            return Err(Problem::refused("bad target").into());
        };
        let when = self.now_iso();
        self.book.follow_up(&account, &thread_id, &mut |current, _| match dismissed(current, &when) {
            Some(f) => FollowUpChange::Settle(f),
            None => FollowUpChange::Nothing,
        })?;
        Ok(json!({"ok": true, "thread_id": thread_id}))
    }

    /// Pin a thread to the Reply Later stack, or with `on` false take it off.
    pub fn reply_later(&self, named: &Named, on: bool) -> Result<Value, Failure> {
        let Some((account, thread_id)) = self.threads.target(named)? else {
            return Err(Problem::refused("bad target").into());
        };
        let when = self.now_iso();
        self.book.follow_up(&account, &thread_id, &mut |current, last_ts| {
            if on {
                FollowUpChange::Put(pinned(current, last_ts, &when))
            } else if unpinning_ends(current.as_ref()) {
                FollowUpChange::Delete
            } else {
                FollowUpChange::Nothing
            }
        })?;
        Ok(json!({"ok": true, "thread_id": thread_id, "on": on}))
    }

    /// Every account's sent threads still with no reply, the longest wait first.
    pub fn waiting_on_others(&self) -> Result<Value, Failure> {
        let accounts = self.accounts.emails()?;
        let now = self.clock.now();
        let rows = self.book.waiting_on_others(&accounts, 100)?;
        Ok(json!({"ok": true, "items": rows.iter().map(|r| waiting_item(r, now)).collect::<Vec<_>>()}))
    }
}

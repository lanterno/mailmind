//! What a draft still asks of its server (`compose.domain.draft_journal`, decision D5): a save is local, and a push
//! follows it on close, on an explicit save and at most every PUSH_EVERY seconds while someone types.

use crate::sending_ports::Waiting;

pub const PUSH_EVERY: i64 = 60;
/// The push job looks at least this often.
pub const PUSH_TICK: i64 = 10;
pub const BACKOFF_FIRST: i64 = 20;
pub const BACKOFF_MOST: i64 = 900;
/// The due time of a save a read-only process made: kept on this Mac, it never comes due, so no push job sends it to
/// its server. A later save in a full run gives it a due time again.
pub const KEPT_HERE: i64 = i64::MAX;

/// When an autosave's copy may go: PUSH_EVERY after the last push, or after this first save.
pub fn due_after_typing(now: i64, pushed_at: Option<i64>) -> i64 {
    match pushed_at.filter(|p| *p != 0) {
        Some(pushed) => now.max(pushed + PUSH_EVERY),
        None => now + PUSH_EVERY,
    }
}

/// A push that failed for the `attempts`-th time goes again later and later, from BACKOFF_FIRST.
pub fn due_after_failing(now: i64, attempts: i64) -> i64 {
    let shift = u32::try_from((attempts - 1).clamp(0, 10)).unwrap_or(10);
    now + BACKOFF_MOST.min(BACKOFF_FIRST << shift)
}

/// (When the push is due, the tries it has had), for a save replacing what `waiting` asked (`after_save`). An
/// autosave changes neither; any other save goes now, afresh. A read-only process (`keep`) keeps it here
/// (`KEPT_HERE`), and an autosave in a full run over one kept here goes as a first save would.
pub fn after_save(
    autosave: bool,
    now: i64,
    waiting: Option<&Waiting>,
    pushed_at: Option<i64>,
    keep: bool,
) -> (i64, i64) {
    if keep {
        return (KEPT_HERE, 0);
    }
    if !autosave {
        return (now, 0);
    }
    match waiting.filter(|w| w.due_at != KEPT_HERE) {
        Some(w) => (w.due_at, w.attempts),
        None => (due_after_typing(now, pushed_at), 0),
    }
}

/// All it asks is that the copy goes: a later save is a draft of its own.
pub fn asks_removal(j: Option<&Waiting>) -> bool {
    j.is_some_and(|j| j.discard)
}

pub fn holds_a_save(j: Option<&Waiting>) -> bool {
    j.is_some_and(|j| j.raw.as_ref().is_some_and(|r| !r.is_empty()))
}

/// The Message-IDs of the copies tries since the last push that landed may have left, oldest first. `tried` keeps each,
/// between spaces, which no Message-ID holds: a try that fails after its APPEND, and another after it, may each have
/// left one, and the next try, or a discard, takes all of them.
pub fn tried_ids(j: Option<&Waiting>) -> Vec<&str> {
    j.and_then(|j| j.tried.as_deref()).map(|t| t.split_whitespace().collect()).unwrap_or_default()
}

/// What `tried` keeps once a try at this save's `message_id` may reach the server: every copy before it too.
pub fn trying(j: &Waiting, message_id: &str) -> String {
    let mut ids = tried_ids(Some(j));
    if !message_id.is_empty() && !ids.contains(&message_id) {
        ids.push(message_id);
    }
    ids.join(" ")
}

/// Is there a copy for a discard to wait on: the one the row tracks, or one a try may have left?
pub fn copy_may_be_there(uid: Option<i64>, j: Option<&Waiting>) -> bool {
    uid.unwrap_or(0) != 0 || !tried_ids(j).is_empty()
}

/// The Message-IDs of the copies tries at this draft may have left in `account`.
pub fn left_by_a_try<'a>(j: Option<&'a Waiting>, account: &str) -> Vec<&'a str> {
    tried_ids(j.filter(|j| j.account == account))
}

/// Seconds until the push job looks again, `due` the next save to come due.
pub fn next_round_in(now: i64, due: Option<i64>) -> i64 {
    match due {
        Some(due) if due > now => PUSH_TICK.min(due - now),
        _ => PUSH_TICK,
    }
}

//! What a draft still asks of its server (`compose.domain.draft_journal`, decision D5): a save is local, and a push
//! follows it on close, on an explicit save and at most every PUSH_EVERY seconds while someone types.

use crate::sending_ports::Waiting;

pub const PUSH_EVERY: i64 = 60;
/// The push job looks at least this often.
pub const PUSH_TICK: i64 = 10;
pub const BACKOFF_FIRST: i64 = 20;
pub const BACKOFF_MOST: i64 = 900;

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
/// autosave changes neither; any other save goes now, afresh.
pub fn after_save(autosave: bool, now: i64, waiting: Option<&Waiting>, pushed_at: Option<i64>) -> (i64, i64) {
    if !autosave {
        return (now, 0);
    }
    match waiting {
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

/// Is there a copy for a discard to wait on: the one the row tracks, or one a try may have left?
pub fn copy_may_be_there(uid: Option<i64>, j: Option<&Waiting>) -> bool {
    uid.unwrap_or(0) != 0 || j.is_some_and(|j| j.tried.as_deref().is_some_and(|t| !t.is_empty()))
}

/// The Message-ID of a copy a try at this save may have left in `account`.
pub fn left_by_a_try<'a>(j: Option<&'a Waiting>, account: &str) -> Option<&'a str> {
    j.filter(|j| j.account == account).and_then(|j| j.tried.as_deref()).filter(|t| !t.is_empty())
}

/// Seconds until the push job looks again, `due` the next save to come due.
pub fn next_round_in(now: i64, due: Option<i64>) -> i64 {
    match due {
        Some(due) if due > now => PUSH_TICK.min(due - now),
        _ => PUSH_TICK,
    }
}

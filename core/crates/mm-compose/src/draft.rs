//! A draft's rules (`compose.domain.draft`), which the Drafts and the Outbox use cases go by: what makes a save,
//! whether one may go in, which copy is ours to remove. Values in and values out; where a fact comes from a port it
//! is asked for through the function given, and only if the rule gets that far.

use crate::sending_ports::{DraftRow, InPlace};

pub const HELD: &str = "This draft is in the outbox, waiting to be sent. Cancel it there first to change it.";
pub const DISCARDED: &str = "You discarded this draft. It leaves the server as soon as the server can be reached.";
pub const CHANGED_ELSEWHERE: &str = "This draft changed in another window.";

/// The composer's idempotency key, as a save or a send gives it (`composer_key`): stripped, at most 200 characters.
pub fn composer_key(given: Option<&str>) -> Option<String> {
    let key: String = mm_kernel::text::strip(given.unwrap_or_default()).chars().take(200).collect();
    if key.is_empty() { None } else { Some(key) }
}

/// Nothing in it to keep (`is_empty`): it is never saved, so Drafts doesn't fill with empty messages.
pub fn is_empty(subject: &str, text: &str, to: &[String], cc: &[String], bcc: &[String], files: usize) -> bool {
    mm_kernel::text::strip(subject).is_empty()
        && mm_kernel::text::strip(text).is_empty()
        && to.is_empty()
        && cc.is_empty()
        && bcc.is_empty()
        && files == 0
}

/// A save that never saw the latest version may not overwrite it, unless the last save was this composer's, whose
/// answer was lost. One that names none overwrites (`is_stale`).
pub fn is_stale(prev: &DraftRow, base_version: Option<i64>, key: Option<&str>) -> bool {
    base_version.is_some_and(|base| base != prev.version) && key.is_none_or(|k| prev.saved_by.as_deref() != Some(k))
}

pub fn next_version(prev: Option<&DraftRow>) -> i64 {
    prev.map_or(1, |p| p.version + 1)
}

/// May this draft's copy be removed from the server (`copy_is_ours`)? A removal has no Undo, so only one MailMind
/// saved, one the server flags \Draft, or one in a folder named Drafts.
pub fn copy_is_ours(d: &DraftRow, names_drafts: impl Fn(&str) -> bool, flagged: impl FnOnce() -> bool) -> bool {
    if d.uid.unwrap_or(0) == 0 {
        return false;
    }
    d.raw_sha.as_deref().is_some_and(|s| !s.is_empty())
        || names_drafts(d.folder.as_deref().unwrap_or_default())
        || flagged()
}

/// An old copy in another account (From changed) is removed there too, so the push spans both (`spans_accounts`).
pub fn spans_accounts(row: &DraftRow, waiting_account: &str) -> bool {
    row.account != waiting_account && row.uid.unwrap_or(0) != 0
}

/// The copy this push's APPEND replaces in its own mailbox (`replaced_in_place`).
pub fn replaced_in_place(prev: &DraftRow, account: &str) -> InPlace {
    if prev.account != account {
        return InPlace::default();
    }
    InPlace {
        prev_uid: prev.uid,
        prev_uidvalidity: prev.uidvalidity,
        prev_msgid: prev.message_id.clone(),
        prev_folder: prev.folder.clone(),
    }
}

/// Is the copy this push replaced somewhere its APPEND couldn't reach, now the new copy is safe in `folder`
/// (`left_elsewhere`)?
pub fn left_elsewhere(prev: &DraftRow, account: &str, folder: &str) -> bool {
    let moved = prev.account != account || prev.folder.as_deref().is_some_and(|f| !f.is_empty() && f != folder);
    prev.uid.unwrap_or(0) != 0 && prev.uidvalidity.is_some() && moved
}

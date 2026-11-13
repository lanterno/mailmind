//! What the AI's verdict on a message asks of you (`mailmind.kernel.attention`). Each question has its one answer
//! here: the lists and the routes ask these, and the page reads what they say instead of working it out.
//!
//! A verdict is the importance (1 to 5), the category and the next step the AI wrote. A message with no verdict yet
//! has none of them, and asks nothing of you.

use serde_json::{Map, Value};

pub const ACTION_NEEDED: &str = "action-needed";

fn has_step(action: Option<&str>) -> bool {
    !crate::text::strip(action.unwrap_or_default()).is_empty()
}

/// Worth drafting a reply to ahead of time. A next step alone isn't: it never spends a call.
pub fn reply_worthy(importance: Option<i64>, category: Option<&str>) -> bool {
    importance.unwrap_or(0) >= 4 || category == Some(ACTION_NEEDED)
}

/// The to-do test: a next step, an action-needed category, or importance 4 and up.
pub fn needs_you(importance: Option<i64>, category: Option<&str>, action: Option<&str>) -> bool {
    has_step(action) || reply_worthy(importance, category)
}

/// A next step that belongs on the agenda, not every low-priority nudge.
pub fn commitment_worthy(importance: Option<i64>, category: Option<&str>, action: Option<&str>) -> bool {
    has_step(action) && (importance.unwrap_or(0) >= 3 || category == Some(ACTION_NEEDED))
}

/// Noise Tidy offers to file: low importance, no next step, plain info or other. A missing importance counts as low,
/// since filing is reversible.
pub fn safe_to_file(importance: Option<i64>, category: Option<&str>, action: Option<&str>) -> bool {
    // `importance or 1`: a 0 counts as missing.
    importance.filter(|i| *i != 0).unwrap_or(1) <= 2 && !has_step(action) && matches!(category, Some("info" | "other"))
}

/// All four, as a list row in the API carries them, added to `row`.
pub fn answers(row: &mut Map<String, Value>, importance: Option<i64>, category: Option<&str>, action: Option<&str>) {
    row.insert("needs_you".into(), needs_you(importance, category, action).into());
    row.insert("reply_worthy".into(), reply_worthy(importance, category).into());
    row.insert("commitment_worthy".into(), commitment_worthy(importance, category, action).into());
    row.insert("safe_to_file".into(), safe_to_file(importance, category, action).into());
}

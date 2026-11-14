//! What keeps a bulk command from reaching nearly everything (`domain.guard`). A filter must narrow
//! (`QuerySpec::narrows`), and whatever it says, one that still finds most of the mail is a whole-mailbox sweep.

/// The share of all the mail at which a filter is a sweep of the whole mailbox.
pub const BROAD_FRACTION: f64 = 0.6;

pub fn sweeps_whole_mailbox(matched: i64, total: i64) -> bool {
    total != 0 && matched as f64 >= BROAD_FRACTION * total as f64
}

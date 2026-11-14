//! What a sync of one folder asks the server for (`mailbox.domain.sync_plan`), and a message's flags as the columns
//! hold them (`mailbox.domain.flags`). Values in and values out: `ingest` does the asking, and the storing.
//!
//! The new mail is a UID range above the folder's watermark, or the whole folder on a first scan, a full one, or once
//! the server has renumbered it (UIDVALIDITY changed). The flags of mail already stored go by the folder's
//! HIGHESTMODSEQ where the server keeps one (CONDSTORE); with no mark to compare, no modseq, or a server that won't
//! say, the `FLAG_REFRESH` newest stored are read.

use crate::session::Search;

/// The newest stored UIDs whose flags an incremental sync reads, with nothing better to go by.
pub const FLAG_REFRESH: usize = 500;
/// The scan_state a whole scan leaves.
pub const WHOLE: &str = "synced";
/// The scan_state a scan cut to the newest N leaves.
pub const CUT: &str = "recent";

/// The flag columns, in the order every `[i64; 5]` here holds them.
pub const COLUMNS: [&str; 5] = ["seen", "flagged", "answered", "draft", "deleted"];
/// Each column's IMAP flag.
pub const IMAP_NAMES: [&str; 5] = ["\\Seen", "\\Flagged", "\\Answered", "\\Draft", "\\Deleted"];

/// The flag columns from a message's IMAP flags, whatever case the server wrote them in (`columns_of`).
pub fn columns_of(flags: &[String]) -> [i64; 5] {
    let said: Vec<String> = flags.iter().map(|f| f.to_lowercase()).collect();
    IMAP_NAMES.map(|name| i64::from(said.iter().any(|f| *f == name.to_lowercase())))
}

/// Of the flags `wanted`, those a row holding `held` doesn't hold already (`still_to_say`): what it came with needs
/// saying to no one.
pub fn still_to_say(wanted: &[(&'static str, bool)], held: &[i64; 5]) -> Vec<(&'static str, bool)> {
    let holds = |name: &str| COLUMNS.iter().position(|c| *c == name).is_some_and(|i| held[i] != 0);
    wanted.iter().copied().filter(|(name, on)| holds(name) != *on).collect()
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct FolderPlan {
    /// The server renumbered the folder: what is stored of it goes first.
    pub reset: bool,
    /// Every UID, not only those above `above`.
    pub whole: bool,
    /// The folder's watermark.
    pub above: u32,
    /// A whole scan keeps only the newest this many.
    pub recent: Option<usize>,
    /// Stored mail is known, so its flags may have changed since.
    pub read_flags: bool,
}

impl FolderPlan {
    /// Whether what this finds is new to the folder, so a person's filters speak for it.
    pub fn arrives_in(&self, folder: &str) -> bool {
        !self.whole && reads_bodies(folder)
    }

    /// The UID SEARCH that names the mail to fetch.
    pub fn search(&self) -> Search<'static> {
        if self.whole { Search::All } else { Search::Above(self.above) }
    }

    /// Of the UIDs `search` found: which to fetch, and the scan_state fetching them leaves, or None to leave it be.
    pub fn to_fetch(&self, found: Vec<u32>) -> (Vec<u32>, Option<&'static str>) {
        if !self.whole {
            // N:* always matches the last UID, even one below N.
            return (found.into_iter().filter(|u| *u > self.above).collect(), None);
        }
        match self.recent {
            Some(recent) if recent > 0 && found.len() > recent => (found[found.len() - recent..].to_vec(), Some(CUT)),
            _ => (found, Some(WHOLE)),
        }
    }
}

/// Whether a sync of this folder fetches bodies as it goes: the Inbox's, for the AI's snippet.
pub fn reads_bodies(folder: &str) -> bool {
    folder.eq_ignore_ascii_case("INBOX")
}

/// Whether opening a folder already read should catch it up: not INBOX, which the background sync keeps, and not
/// again within `every` seconds of the last that started (`since`, None when none has). An open while one runs waits
/// for it too, or it would answer with what that one is about to change.
pub fn catch_up_due(folder: &str, running: bool, since: Option<f64>, every: f64) -> bool {
    if folder.to_uppercase() == "INBOX" {
        return false;
    }
    running || since.is_none_or(|since| since >= every)
}

/// What to fetch of a folder, from the UIDVALIDITY its select said and what is stored of it.
pub fn plan_folder(
    uidvalidity: Option<u32>,
    known_uidvalidity: Option<i64>,
    watermark: i64,
    full: bool,
    recent: Option<usize>,
) -> FolderPlan {
    let reset = matches!((uidvalidity, known_uidvalidity), (Some(now), Some(known)) if now != 0 && known != 0
        && i64::from(now) != known);
    let whole = full || reset || watermark == 0;
    let above = u32::try_from(watermark).unwrap_or(0);
    FolderPlan { reset, whole, above, recent, read_flags: !whole }
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct FlagPlan {
    /// The HIGHESTMODSEQ the select said, if the server keeps one.
    pub modseq: Option<u64>,
    /// It matches the mark, so there is nothing to read.
    pub unchanged: bool,
    /// Ask the server what changed since this; None reads the newest stored.
    pub changed_since: Option<u64>,
}

impl FlagPlan {
    /// What this read leaves as the folder's mark, or None to leave it: it stays behind anything the read skipped
    /// (`held_back`), and behind a search that was asked for and refused (`searched` false).
    pub fn mark(&self, held_back: bool, searched: bool) -> Option<u64> {
        let modseq = self.modseq.filter(|m| *m != 0)?;
        if held_back || (self.changed_since.is_some() && !searched) {
            return None;
        }
        Some(modseq)
    }
}

/// How to read the flags of stored mail, from the `modseq` the select just said and the `mark` the last read left.
pub fn plan_flags(modseq: Option<u64>, mark: Option<u64>) -> FlagPlan {
    let Some(modseq) = modseq.filter(|m| *m != 0) else { return FlagPlan::default() };
    if mark == Some(modseq) {
        return FlagPlan { modseq: Some(modseq), unchanged: true, changed_since: None };
    }
    let changed_since = mark.filter(|m| *m != 0 && *m < modseq);
    FlagPlan { modseq: Some(modseq), unchanged: false, changed_since }
}

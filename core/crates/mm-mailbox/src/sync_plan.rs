//! What a sync of one folder asks the server for (`mailbox.domain.sync_plan`), and a message's flags as the columns
//! hold them (`mailbox.domain.flags`). Values in and values out: `ingest` does the asking, and the storing.
//!
//! The new mail is a UID range above the folder's watermark, or the whole folder on a first scan, a full one, or once
//! the server has renumbered it (UIDVALIDITY changed). The flags of mail already stored go by the folder's
//! HIGHESTMODSEQ where the server keeps one (CONDSTORE); with no mark to compare, no modseq, or a server that won't
//! say, the `FLAG_REFRESH` newest stored are read.
//!
//! What a sync stores goes in steps of at most `CHUNK` messages, each one transaction that also says how far the sync
//! got, so a quit or a crash goes on from there. New mail goes oldest first, the watermark rising with each step. A
//! first scan stores the newest `CHUNK` and says what is left below them (`Scan::Down`); the background then reads
//! the rest, newest first, a step at a time (`older_steps`), so the newest mail shows first and a big folder's first
//! scan holds nothing up for long.

use crate::session::Search;

/// The newest stored UIDs whose flags an incremental sync reads, with nothing better to go by.
pub const FLAG_REFRESH: usize = 500;
/// The scan_state a whole scan leaves.
pub const WHOLE: &str = "synced";
/// The scan_state a scan cut to the newest N leaves.
pub const CUT: &str = "recent";
/// The scan_state a first scan leaves while older mail is still to read: this, then the lowest UID it has read.
pub const DOWN_TO: &str = "down-to:";
/// How many messages a sync stores in one step, in one transaction.
pub const CHUNK: usize = 500;

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

/// What the scans of a folder have read of it so far (`sync_state.scan_state`).
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Scan {
    /// Nothing yet (`never`).
    #[default]
    Never,
    /// Only its newest, as warming a folder reads it (`recent`).
    Cut,
    /// Every message from this UID up; those below it are still to read (`down-to:N`).
    Down(u32),
    /// The whole folder (`synced`).
    Whole,
}

impl Scan {
    /// The state as the column holds it; one it doesn't know reads as never read.
    pub fn read(state: Option<&str>) -> Scan {
        match state {
            Some(WHOLE) => Scan::Whole,
            Some(CUT) => Scan::Cut,
            Some(state) => state.strip_prefix(DOWN_TO).and_then(|uid| uid.parse().ok()).map_or(Scan::Never, Scan::Down),
            None => Scan::Never,
        }
    }

    /// The state as the column holds it.
    pub fn state(self) -> String {
        match self {
            Scan::Never => "never".to_owned(),
            Scan::Cut => CUT.to_owned(),
            Scan::Down(uid) => format!("{DOWN_TO}{uid}"),
            Scan::Whole => WHOLE.to_owned(),
        }
    }

    /// Whether a first scan has stored the folder's newest mail; what is older is read in the background.
    pub fn begun(self) -> bool {
        matches!(self, Scan::Down(_) | Scan::Whole)
    }
}

/// One store of a sync: these UIDs, in one transaction with where the sync leaves the folder.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Step {
    /// Oldest first.
    pub uids: Vec<u32>,
    /// The watermark rises to this; 0 leaves it where it is.
    pub watermark: u32,
    /// What the folder's scans have read once it is stored, or None to leave that as it is.
    pub leaves: Option<Scan>,
    /// What the folder's scans must still have read for the step to be kept: a read of older mail gives way to a
    /// sync that read the folder afresh meanwhile.
    pub holds: Option<Scan>,
}

/// UIDs (in order) in steps of `CHUNK`, the newest step first, each oldest first.
fn newest_first(uids: &[u32]) -> Vec<Vec<u32>> {
    uids.rchunks(CHUNK).map(<[u32]>::to_vec).collect()
}

/// The steps that read a folder's older mail, below `floor`, the newest first: no more than `chunks` of them, of the
/// UIDs a search found below it. Each holds the folder to where the step before left it, and the one that reaches
/// the bottom leaves it read whole.
pub fn older_steps(floor: u32, below: &[u32], chunks: usize) -> Vec<Step> {
    let mut below: Vec<u32> = below.iter().copied().filter(|uid| *uid < floor).collect();
    below.sort_unstable();
    below.dedup();
    let Some(&bottom) = below.first() else {
        return vec![Step { leaves: Some(Scan::Whole), holds: Some(Scan::Down(floor)), ..Step::default() }];
    };
    let mut held = floor;
    let mut steps = Vec::new();
    for uids in newest_first(&below).into_iter().take(chunks.max(1)) {
        let lowest = uids[0];
        let leaves = if lowest == bottom { Scan::Whole } else { Scan::Down(lowest) };
        steps.push(Step { uids, watermark: 0, leaves: Some(leaves), holds: Some(Scan::Down(held)) });
        held = lowest;
    }
    steps
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
    /// What the scans so far have read of it: never, once the server renumbered it.
    pub scan: Scan,
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

    /// Of the UIDs `search` found: what to store now, in steps, each one transaction. New mail goes oldest first, the
    /// watermark rising with each step, in one step at least, so the sync's time is kept. A first scan stores the
    /// newest `CHUNK` now and leaves the rest to the background (`Scan::Down`). A scan cut to the newest N, and a
    /// whole one of a folder read whole before, store all of theirs now, the newest first.
    pub fn steps(&self, found: Vec<u32>) -> Vec<Step> {
        let (mut found, cut) = self.to_fetch(found);
        found.sort_unstable();
        found.dedup();
        if !self.whole {
            let mut steps: Vec<Step> = found
                .chunks(CHUNK)
                .map(|uids| Step { uids: uids.to_vec(), watermark: uids[uids.len() - 1], ..Step::default() })
                .collect();
            if steps.is_empty() {
                steps.push(Step::default());
            }
            return steps;
        }
        let (Some(&bottom), Some(&top)) = (found.first(), found.last()) else {
            return vec![Step { leaves: Some(Scan::Whole), ..Step::default() }];
        };
        let bounded = self.recent.is_some_and(|recent| recent > 0);
        if !bounded && self.scan != Scan::Whole {
            // A first scan: the newest now, the rest in the background.
            let newest = newest_first(&found).swap_remove(0);
            let leaves = if newest[0] == bottom { Scan::Whole } else { Scan::Down(newest[0]) };
            return vec![Step { uids: newest, watermark: top, leaves: Some(leaves), holds: None }];
        }
        let chunks = newest_first(&found);
        let last = chunks.len() - 1;
        chunks
            .into_iter()
            .enumerate()
            .map(|(i, uids)| {
                let leaves = match cut {
                    _ if self.scan == Scan::Whole => None,
                    Some(CUT) => Some(Scan::Cut),
                    _ if i == last => Some(Scan::Whole),
                    _ => Some(Scan::Cut),
                };
                Step { uids, watermark: top, leaves, holds: None }
            })
            .collect()
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

/// What to fetch of a folder, from the UIDVALIDITY its select said and what is stored of it: its watermark, and what
/// its scans have read. A full sync of a folder whose first scan is still reading its older mail reads only what is
/// new: the background reads the rest. A folder no scan has begun is read as a first scan, whatever its watermark: one
/// left with a watermark by a quit part way, as before a step stored both at once, would else be read above it for
/// good, and its first open wait on a sync each time.
pub fn plan_folder(
    uidvalidity: Option<u32>,
    known_uidvalidity: Option<i64>,
    watermark: i64,
    scan: Scan,
    full: bool,
    recent: Option<usize>,
) -> FolderPlan {
    let reset = matches!((uidvalidity, known_uidvalidity), (Some(now), Some(known)) if now != 0 && known != 0
        && i64::from(now) != known);
    let scan = if reset { Scan::Never } else { scan };
    let whole = reset || watermark == 0 || scan == Scan::Never || (full && !matches!(scan, Scan::Down(_)));
    let above = u32::try_from(watermark).unwrap_or(0);
    FolderPlan { reset, whole, above, recent, read_flags: !whole, scan }
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

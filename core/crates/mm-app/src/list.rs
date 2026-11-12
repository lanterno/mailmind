//! The message list as a view: the core holds the order, the shell holds only the rows near the screen.
//!
//! A shell says which rows are visible with `set_visible`. The core answers with a `ViewBatch` holding the rows
//! the shell does not have yet, and the range it should keep. Rows load a page at a time, with one page kept on
//! each side, so a scroll of a few rows sends nothing and a shell's memory does not grow with the mailbox.
//! Calls that arrive faster than the core can answer collapse into one: only the latest window is served.

use std::sync::{Arc, Mutex, Weak, mpsc};

use mm_kernel::{MessageKey, Refusal};
use mm_store::ListRow;
use serde::{Deserialize, Serialize};

use crate::{Inner, Job, Runner, lock};

/// Which messages a list shows.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[cfg_attr(feature = "ts", derive(ts_rs::TS), ts(export))]
pub struct ListQuery {
    /// The accounts whose mail is merged into the list.
    pub accounts: Vec<String>,
    pub folder: String,
}

/// One row as a list shows it. What every platform would have to agree on is decided here: which name stands
/// for the sender, and how much of the text shows.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[cfg_attr(feature = "ts", derive(ts_rs::TS), ts(export))]
pub struct MessageRow {
    pub key: MessageKey,
    pub sender: String,
    pub subject: String,
    pub snippet: String,
    /// Seconds since 1970 UTC; 0 when the message has no date. The shell formats it.
    #[cfg_attr(feature = "ts", ts(type = "number"))]
    pub date: i64,
    pub unread: bool,
    pub flagged: bool,
    pub attachment: bool,
}

/// Rows that start at one position in the list.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[cfg_attr(feature = "ts", derive(ts_rs::TS), ts(export))]
pub struct RowRange {
    pub first: u32,
    pub rows: Vec<MessageRow>,
}

/// What a view sends a shell: the rows it lacks, and which to keep.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[cfg_attr(feature = "ts", derive(ts_rs::TS), ts(export))]
pub struct ViewBatch {
    /// Rows in the whole list.
    pub total: u32,
    /// The shell keeps rows from `held_first` for `held_count` rows, and drops any others it has.
    pub held_first: u32,
    pub held_count: u32,
    /// Rows the shell does not have yet, all inside the held range.
    pub ranges: Vec<RowRange>,
}

/// What a shell gives a view to hear from it. Calls come on the core's thread: the shell moves to its own.
pub trait ViewObserver: Send + Sync {
    fn on_batch(&self, batch: ViewBatch);
    fn on_refusal(&self, refusal: Refusal);
}

/// An open list. Dropping the last handle to it cancels whatever it was about to do.
pub struct ListView {
    order: Vec<i64>,
    observer: Arc<dyn ViewObserver>,
    state: Mutex<State>,
    serving: Mutex<()>,
    session: Weak<Inner>,
    me: Weak<ListView>,
}

impl std::fmt::Debug for ListView {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ListView").field("total", &self.order.len()).finish_non_exhaustive()
    }
}

#[derive(Default)]
struct State {
    wanted: Option<(u32, u32)>,
    queued: bool,
    held: (u32, u32),
    sent: bool,
}

/// Snippets are cut to this many characters: a list never shows more.
const SNIPPET_CHARS: usize = 140;

impl ListView {
    pub(crate) fn new(order: Vec<i64>, observer: Arc<dyn ViewObserver>, inner: &Arc<Inner>) -> Arc<ListView> {
        Arc::new_cyclic(|me| ListView {
            order,
            observer,
            state: Mutex::new(State::default()),
            serving: Mutex::new(()),
            session: Arc::downgrade(inner),
            me: me.clone(),
        })
    }

    /// Rows in the whole list.
    pub fn total(&self) -> u32 {
        u32::try_from(self.order.len()).unwrap_or(u32::MAX)
    }

    /// The shell shows rows from `first`, `count` of them. The answer comes through the observer.
    pub fn set_visible(&self, first: u32, count: u32) {
        let Some(inner) = self.session.upgrade() else {
            return;
        };
        {
            let mut state = lock(&self.state);
            state.wanted = Some((first, count));
            if state.queued {
                return;
            }
            state.queued = true;
        }
        match &inner.runner {
            Runner::Inline => self.serve(&inner),
            Runner::Thread(worker) => {
                if worker.send(Job::Serve(self.me.clone())).is_err() {
                    lock(&self.state).queued = false;
                }
            }
        }
    }

    fn serve(&self, inner: &Inner) {
        let _one_at_a_time = lock(&self.serving);
        let (first, count, held, sent) = {
            let mut state = lock(&self.state);
            state.queued = false;
            let Some((first, count)) = state.wanted.take() else {
                return;
            };
            (first, count, state.held, state.sent)
        };
        let total = self.total();
        let wanted = held_range(first, count, total, inner.page);
        if sent && wanted == held {
            return;
        }
        let mut ranges = Vec::new();
        for (start, end) in missing(wanted, held) {
            match self.load(inner, start, end) {
                Ok(rows) => ranges.push(RowRange { first: start, rows }),
                Err(refusal) => return self.observer.on_refusal(refusal),
            }
        }
        {
            let mut state = lock(&self.state);
            state.held = wanted;
            state.sent = true;
        }
        self.observer.on_batch(ViewBatch { total, held_first: wanted.0, held_count: wanted.1 - wanted.0, ranges });
    }

    fn load(&self, inner: &Inner, start: u32, end: u32) -> Result<Vec<MessageRow>, Refusal> {
        let pks = &self.order[start as usize..end as usize];
        let found = lock(&inner.store).rows(pks)?;
        found
            .into_iter()
            .map(|row| row.map(shown).ok_or_else(|| Refusal::not_found("a message in this list")))
            .collect()
    }
}

/// The thread that serves views for a session. It ends when the session does.
pub(crate) fn spawn_worker() -> Result<mpsc::Sender<Job>, Refusal> {
    let (sender, receiver) = mpsc::channel::<Job>();
    std::thread::Builder::new()
        .name("mm-views".into())
        .spawn(move || {
            for job in receiver {
                match job {
                    Job::Serve(view) => {
                        let Some(view) = view.upgrade() else { continue };
                        let Some(inner) = view.session.upgrade() else {
                            continue;
                        };
                        view.serve(&inner);
                    }
                    Job::Settle(done) => {
                        let _ = done.send(());
                    }
                }
            }
        })
        .map_err(|_| Refusal::storage("no thread could be started for views"))?;
    Ok(sender)
}

/// The rows to hold for a window: whole pages, from the page before it to the page after it.
pub(crate) fn held_range(first: u32, count: u32, total: u32, page: u32) -> (u32, u32) {
    let first = first.min(total);
    let end = first.saturating_add(count).min(total);
    let start = (first / page).saturating_sub(1) * page;
    let stop = (end.div_ceil(page).saturating_add(1)).saturating_mul(page).min(total);
    (start.min(stop), stop)
}

/// The parts of `wanted` that are not in `held`.
pub(crate) fn missing(wanted: (u32, u32), held: (u32, u32)) -> Vec<(u32, u32)> {
    let (lo, hi) = wanted;
    let (old_lo, old_hi) = held;
    if lo >= hi {
        return Vec::new();
    }
    if old_lo >= old_hi || hi <= old_lo || lo >= old_hi {
        return vec![(lo, hi)];
    }
    let mut parts = Vec::new();
    if lo < old_lo {
        parts.push((lo, old_lo));
    }
    if hi > old_hi {
        parts.push((old_hi, hi));
    }
    parts
}

fn shown(row: ListRow) -> MessageRow {
    let sender = match (row.from_name, row.from_addr) {
        (Some(name), _) if !name.trim().is_empty() => name.trim().to_owned(),
        (_, Some(addr)) => addr.trim().to_owned(),
        _ => String::new(),
    };
    MessageRow {
        key: row.key,
        sender,
        subject: row.subject.unwrap_or_default(),
        snippet: row.snippet.as_deref().map(snippet).unwrap_or_default(),
        date: row.ts.unwrap_or(0),
        unread: !row.seen,
        flagged: row.flagged,
        attachment: row.has_attach,
    }
}

/// The text a list shows: whitespace folded to single spaces, cut at a character boundary.
fn snippet(text: &str) -> String {
    let mut out = String::with_capacity(SNIPPET_CHARS + 4);
    let mut chars = 0;
    for word in text.split_whitespace() {
        if chars > 0 {
            out.push(' ');
            chars += 1;
        }
        for c in word.chars() {
            if chars == SNIPPET_CHARS {
                return out;
            }
            out.push(c);
            chars += 1;
        }
        if chars >= SNIPPET_CHARS {
            return out;
        }
    }
    out
}

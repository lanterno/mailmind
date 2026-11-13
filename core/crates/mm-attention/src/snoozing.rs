//! A snooze: a message hidden until it wakes (`mailmind.attention.domain.snooze`), and the use cases that set one,
//! lift one and wake what is due (`application.snoozing`). A preset reads in the person's own zone: the server is
//! their machine.

use std::sync::Arc;

use mm_kernel::errors::{Failure, Problem};
use mm_kernel::isoformat::{self, Wall};
use mm_kernel::seams::{Clock, Moment};
use mm_kernel::when::days_from_civil;
use mm_kernel::zone::{self, Zone};
use serde_json::{Value, json};

use crate::ports::{AttentionBook, Copy, Flags, Message, Undo};

/// The most copies one request may name: more is refused, never cut short, and the page sends more as more requests.
pub const BULK_CAP: usize = 500;
/// How far ahead a time someone picks may lie.
pub const MAX_AHEAD_DAYS: i64 = 366;
/// The presets the page offers (`kernel.snoozed.SnoozePreset`).
pub const PRESETS: [&str; 4] = ["later_today", "tomorrow", "weekend", "next_week"];

const DAY: i64 = 86_400;

/// A wake time a request gives: seconds since the epoch, or text, which may still be a number or an ISO time.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Until {
    Seconds(i64),
    Text(String),
}

/// A time someone picked has to lie ahead, and within a year: (now, now + 366 days].
pub fn in_window(until: i64, now: f64) -> bool {
    let until = until as f64;
    now < until && until <= now + (MAX_AHEAD_DAYS * DAY) as f64
}

/// The wall-clock time `local` (seconds, counted as though it were UTC) as the moment it names in `zone`.
fn placed(zone: &dyn Zone, local: i64) -> i64 {
    zone::place(zone, local)
}

fn wall_seconds(wall: Wall) -> i64 {
    let days = days_from_civil(i64::from(wall.year), wall.month, wall.day);
    days * DAY + i64::from(wall.hour * 3600 + wall.minute * 60 + wall.second)
}

/// `int(text)` as Python reads a string: blanks around it, a sign, and digits with single underscores between. None
/// where it doesn't read, or is past what a moment holds (which no window takes).
fn python_int(text: &str) -> Option<Result<i64, ()>> {
    let s = mm_kernel::text::strip(text);
    let digits = s.strip_prefix(['+', '-']).unwrap_or(s);
    if digits.is_empty()
        || digits.starts_with('_')
        || digits.ends_with('_')
        || digits.contains("__")
        || !digits.chars().all(|c| c.is_ascii_digit() || c == '_')
    {
        return None;
    }
    let n: String = s.chars().filter(|c| *c != '_').collect();
    Some(n.parse::<i64>().map_err(|_| ()))
}

/// The wake time a preset or `until` gives, in seconds since the epoch, or None where it gives none that will do.
///
/// A preset's day and hour are the person's wall clock: tomorrow at eight is eight o'clock tomorrow whatever the
/// clocks did overnight. (The Python app read them at the offset in force now, so across a change of the clocks it
/// woke an hour off.)
pub fn wake_time(preset: Option<&str>, until: Option<&Until>, now: Moment, zone: &dyn Zone) -> Option<i64> {
    let now_s = now.whole_seconds();
    let local_now = zone::local(zone, now_s);
    let today = local_now.div_euclid(DAY);
    let at_eight = |days_on: i64| placed(zone, (today + days_on) * DAY + 8 * 3600);
    if let Some(preset) = preset.filter(|p| !p.is_empty()) {
        // 1970-01-01 was a Thursday; Monday is 0, as `weekday()` counts.
        let weekday = (today + 3).rem_euclid(7);
        return match preset {
            "later_today" => {
                let later = zone::local(zone, now_s + 3 * 3600);
                if later.div_euclid(DAY) > today {
                    // Three hours on runs past midnight, and later today shouldn't wake you at two: the next morning.
                    Some(at_eight(1))
                } else {
                    Some(placed(zone, later - later.rem_euclid(3600)))
                }
            }
            "tomorrow" => Some(at_eight(1)),
            "weekend" => Some(at_eight(match (5 - weekday).rem_euclid(7) {
                0 => 7,
                d => d,
            })),
            "next_week" => Some(at_eight(match (7 - weekday).rem_euclid(7) {
                0 => 7,
                d => d,
            })),
            _ => None,
        };
    }
    let u = match until? {
        Until::Seconds(u) => *u,
        Until::Text(text) => match python_int(text) {
            Some(Ok(u)) => u,
            Some(Err(())) => return None,
            None => isoformat::seconds(text, |wall| Some(placed(zone, wall_seconds(wall))))?,
        },
    };
    in_window(u, now.seconds()).then_some(u)
}

/// What undoing a snooze needs of each message, as its row had it before: when it was to wake, and where it is,
/// since a pk isn't stable and the undo stack outlives it. A pk with no row has neither.
pub fn undo_entry(snoozed: &[(i64, Option<Message>)]) -> Value {
    json!({
        "kind": "snooze",
        "pks": snoozed.iter().map(|(pk, _)| *pk).collect::<Vec<_>>(),
        "where": snoozed.iter().map(|(_, r)| r.as_ref().map(|r| json!([r.key, r.folder]))).collect::<Vec<_>>(),
        "prev_until": snoozed.iter().map(|(_, r)| r.as_ref().and_then(|r| r.snooze_until)).collect::<Vec<_>>(),
        "label": "Snoozed",
    })
}

/// What a snooze or an unsnooze names: a whole thread, by its account and id, goes before any copies.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Target {
    pub account: Option<String>,
    pub thread_id: Option<String>,
    /// The copies `copies` named that are still there, where it named a list.
    pub pks: Option<Vec<i64>>,
    /// The copy `key`, `folder` and `uid` name, where it is still there.
    pub pk: Option<i64>,
}

impl Target {
    fn thread(&self) -> Option<(&str, &str)> {
        match (self.account.as_deref(), self.thread_id.as_deref()) {
            (Some(a), Some(t)) if !a.is_empty() && !t.is_empty() => Some((a, t)),
            _ => None,
        }
    }

    /// The messages it names by pk. More than BULK_CAP is refused whole: cutting it short would leave the rest as
    /// they were while whoever asked takes them all for done.
    fn pks(&self) -> Result<Vec<i64>, Problem> {
        match (&self.pks, self.pk) {
            (Some(pks), _) if pks.len() > BULK_CAP => Err(Problem::bad_request()),
            (Some(pks), _) => Ok(pks.clone()),
            (None, Some(pk)) => Ok(vec![pk]),
            (None, None) => Ok(Vec::new()),
        }
    }
}

/// What a round of waking came to.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Woken {
    pub count: usize,
    /// Of those, the ones that should come back unread on their server too, which this server can't ask yet.
    pub left_read: usize,
}

#[derive(Debug)]
pub struct Snoozing {
    book: Arc<dyn AttentionBook>,
    undo: Arc<dyn Undo>,
    flags: Arc<dyn Flags>,
    clock: Arc<dyn Clock>,
    zone: Arc<dyn Zone>,
}

impl Snoozing {
    pub fn new(
        book: Arc<dyn AttentionBook>,
        undo: Arc<dyn Undo>,
        flags: Arc<dyn Flags>,
        clock: Arc<dyn Clock>,
        zone: Arc<dyn Zone>,
    ) -> Snoozing {
        Snoozing { book, undo, flags, clock, zone }
    }

    /// `POST /api/snooze`.
    pub fn snooze(
        &self,
        target: &Target,
        preset: Option<&str>,
        until: Option<&Until>,
        undo_group: Option<&str>,
    ) -> Result<Value, Failure> {
        let Some(until) = wake_time(preset, until, self.clock.now(), &*self.zone) else {
            return Err(Problem::refused("bad snooze time").into());
        };
        let pks = match target.thread() {
            Some(_) => Vec::new(),
            None => target.pks()?,
        };
        let snoozed = self.book.snooze(target.thread(), &pks, until)?;
        if !snoozed.is_empty() {
            self.undo.push(&undo_entry(&snoozed), undo_group)?;
        }
        Ok(json!({"ok": true, "count": snoozed.len(), "until": until}))
    }

    /// `POST /api/unsnooze`.
    pub fn unsnooze(&self, target: &Target) -> Result<Value, Failure> {
        let pks = match target.thread() {
            Some(_) => Vec::new(),
            None => target.pks()?,
        };
        let count = self.book.unsnooze(target.thread(), &pks)?;
        Ok(json!({"ok": true, "count": count}))
    }

    /// Bring back what is due: the snooze ends, and what was read comes back unread, on its server too, or the next
    /// sync that read its flags would take that back.
    pub fn wake_due(&self) -> Result<Woken, Failure> {
        let due = self.book.wake_due(self.clock.now().whole_seconds())?;
        let read: Vec<Copy> =
            due.iter().filter(|r| r.seen).map(|r| Copy { key: r.key, folder: r.folder.clone() }).collect();
        let left_read = if read.is_empty() || self.flags.mark_seen(&read, false)?.is_some() { 0 } else { read.len() };
        Ok(Woken { count: due.len(), left_read })
    }
}

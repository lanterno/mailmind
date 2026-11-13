//! The tasks a person has confirmed from their mail (`mailmind.attention.domain.tasks`, `application.tasks`): kept,
//! listed where Commitments are, marked done and removed. What a person confirmed is read as their input
//! (`kernel.proposed`), so a date that won't do is refused and said so.

use std::sync::Arc;

use mm_kernel::errors::{Failure, Problem};
use mm_kernel::isoformat;
use mm_kernel::seams::Clock;
use mm_kernel::when::{civil, days_from_civil};
use mm_kernel::zone::{self, Zone};
use serde_json::{Value, json};

use crate::ports::{Accounts, AttentionBook, TaskRow};

/// The longest title a task keeps, in characters.
pub const TITLE_MAX: usize = 200;
/// How far behind today a date may lie: a message read late can still name a task overdue.
const BEHIND_DAYS: i64 = 366;
/// How far ahead.
const AHEAD_DAYS: i64 = 366 * 5;

/// What ends a line somewhere, and what no line should hold.
fn breaks(c: char) -> bool {
    matches!(c, '\u{0}'..='\u{1f}' | '\u{7f}'..='\u{9f}' | '\u{2028}' | '\u{2029}')
}

/// Text as one line, within `cap` characters (`proposed.one_line`).
pub fn one_line(value: Option<&str>, cap: usize) -> String {
    let Some(value) = value else { return String::new() };
    let spaced: String = value.chars().map(|c| if breaks(c) { ' ' } else { c }).collect();
    let joined = spaced.split(mm_kernel::text::is_space).filter(|w| !w.is_empty()).collect::<Vec<_>>().join(" ");
    joined.chars().take(cap).collect()
}

/// Whether `s` is `\d{4}-\d{2}-\d{2}` whole.
fn is_day(b: &[u8]) -> bool {
    b.len() == 10 && b.iter().enumerate().all(|(i, c)| if i == 4 || i == 7 { *c == b'-' } else { c.is_ascii_digit() })
}

/// Whether `s` is a time as `proposed._TIME` has one, whole:
/// `\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}(?::?\d{2})?)?`.
fn is_time(s: &str) -> bool {
    let b = s.as_bytes();
    let digits = |from: usize, n: usize| b.len() >= from + n && b[from..from + n].iter().all(u8::is_ascii_digit);
    if b.len() < 16 || !is_day(&b[..10]) || !matches!(b[10], b'T' | b' ') || !digits(11, 2) || b[13] != b':' {
        return false;
    }
    if !digits(14, 2) {
        return false;
    }
    let mut i = 16;
    if b.get(i) == Some(&b':') && digits(i + 1, 2) {
        i += 3;
        if b.get(i) == Some(&b'.') {
            let n = b[i + 1..].iter().take(6).take_while(|c| c.is_ascii_digit()).count();
            if n == 0 {
                return false;
            }
            i += 1 + n;
        }
    }
    match b.get(i) {
        None => true,
        Some(b'Z') => i + 1 == b.len(),
        Some(b'+' | b'-') if digits(i + 1, 2) => {
            let rest = &b[i + 3..];
            rest.is_empty()
                || (rest.len() == 2 && rest.iter().all(u8::is_ascii_digit))
                || (rest.len() == 3 && rest[0] == b':' && rest[1..].iter().all(u8::is_ascii_digit))
        }
        _ => false,
    }
}

/// A day, as days since 1970-01-01, if `y-m-d` is one.
fn day_of(year: i64, month: u32, day: u32) -> Option<i64> {
    let days = days_from_civil(year, month, day);
    (civil(days) == (year, month, day)).then_some(days)
}

/// A day as ISO text, or None, within the window around `today` (days since 1970-01-01). A time reads as its day
/// (`proposed.read_date`).
pub fn read_date(text: Option<&str>, today: i64) -> Option<String> {
    let s = mm_kernel::text::strip(text?);
    let days = if is_day(s.as_bytes()) {
        let n = |r: std::ops::Range<usize>| s[r].parse::<u32>().ok();
        let year = i64::from(n(0..4)?);
        if year < 1 {
            return None;
        }
        day_of(year, n(5..7)?, n(8..10)?)?
    } else if is_time(s) {
        let wall = isoformat::read(s)?.wall;
        day_of(i64::from(wall.year), wall.month, wall.day)?
    } else {
        return None;
    };
    if !(today - BEHIND_DAYS..=today + AHEAD_DAYS).contains(&days) {
        return None;
    }
    let (y, m, d) = civil(days);
    Some(format!("{y:04}-{m:02}-{d:02}"))
}

/// A stored task as the page reads it: the copy its message has now, by key, folder and uid, and all three null once
/// it has none left. A pk never leaves here.
pub fn as_listed(row: &TaskRow) -> Value {
    let there = row.folder.is_some();
    json!({
        "id": row.id, "account": row.account, "title": row.title, "due": row.due, "done": row.done_at.is_some(),
        "key": if there { row.key } else { None }, "folder": row.folder, "uid": row.uid, "thread_id": row.thread_id,
        "subject": row.subject, "from_name": row.from_name, "from_addr": row.from_addr,
    })
}

#[derive(Debug)]
pub struct Tasks {
    book: Arc<dyn AttentionBook>,
    accounts: Arc<dyn Accounts>,
    clock: Arc<dyn Clock>,
    zone: Arc<dyn Zone>,
}

impl Tasks {
    pub fn new(
        book: Arc<dyn AttentionBook>,
        accounts: Arc<dyn Accounts>,
        clock: Arc<dyn Clock>,
        zone: Arc<dyn Zone>,
    ) -> Tasks {
        Tasks { book, accounts, clock, zone }
    }

    fn now_iso(&self) -> String {
        mm_kernel::when::iso(self.clock.now())
    }

    /// `POST /api/tasks`: one someone confirmed, from message `pk`.
    pub fn add(&self, pk: Option<i64>, title: Option<&str>, due: Option<&str>) -> Result<Value, Failure> {
        let Some(pk) = pk else {
            return Err(Problem::refused("missing message").into());
        };
        let title = one_line(title, TITLE_MAX);
        if title.is_empty() {
            return Err(Problem::refused("a task needs a title").into());
        }
        let due = match due.filter(|d| !d.is_empty()) {
            None => None,
            Some(given) => {
                let today = zone::day_and_second(&*self.zone, self.clock.now().whole_seconds()).0;
                match read_date(Some(given), today) {
                    Some(day) => Some(day),
                    None => return Err(Problem::refused("that date isn\u{2019}t one this can use").into()),
                }
            }
        };
        let Some(task) = self.book.add_task(pk, &title, due.as_deref(), &self.now_iso())? else {
            return Err(Problem::not_found("not found").into());
        };
        Ok(json!({"ok": true, "task": as_listed(&task)}))
    }

    /// `GET /api/tasks`: every enabled account's. One whose account is off stays, for when it is back on.
    pub fn listing(&self) -> Result<Value, Failure> {
        let enabled = self.accounts.emails()?;
        let items: Vec<Value> =
            self.book.tasks()?.iter().filter(|t| enabled.contains(&t.account)).map(as_listed).collect();
        Ok(json!({"ok": true, "items": items}))
    }

    /// `POST /api/tasks/done`: done, or with `done` false open again.
    pub fn mark(&self, id: i64, done: Option<bool>) -> Result<Value, Failure> {
        let done_at = (done != Some(false)).then(|| self.now_iso());
        Self::said(self.book.set_task_done(id, done_at.as_deref())?)
    }

    /// `POST /api/tasks/remove`.
    pub fn remove(&self, id: i64) -> Result<Value, Failure> {
        Self::said(self.book.remove_task(id)?)
    }

    fn said(found: bool) -> Result<Value, Failure> {
        if !found {
            return Err(Problem::not_found("that task is gone").into());
        }
        Ok(json!({"ok": true}))
    }
}

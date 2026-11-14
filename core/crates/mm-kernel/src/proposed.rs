//! What someone proposes for a calendar or a to-do list (`mailmind.kernel.proposed`): what the model read out of a
//! message, or what a person typed over it. Both are text to be read with care. Total: nothing here panics.
//!
//! A date is ISO 8601: a day (`YYYY-MM-DD`), or a time (`YYYY-MM-DDTHH:MM`) with its offset where it has one. One that
//! won't read, or falls outside the window around today (`BEHIND`, `AHEAD`), reads as None: clamping it to the window's
//! edge would put a wrong date in front of someone, so it is dropped instead.

use crate::when::{civil, days_from_civil};

/// Days behind today a date may be: a message read late can still name last month's meeting, or a task overdue.
pub const BEHIND_DAYS: i64 = 366;
/// Days ahead of today a date may be.
pub const AHEAD_DAYS: i64 = 366 * 5;
/// What an event may run to, in seconds: past this it's a misread end.
pub const LONGEST_SECONDS: i64 = 31 * 86_400;
pub const TITLE_MAX: usize = 200;
pub const PLACE_MAX: usize = 300;

/// A day as (year, month, day).
pub type Day = (i64, u32, u32);

/// What a date read as: a day, or a time of day with its offset in seconds where it has one.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Reading {
    Day(i64),
    Time { day: i64, second: i64, micros: u32, offset: Option<i64> },
}

impl Reading {
    fn day(self) -> i64 {
        match self {
            Reading::Day(day) | Reading::Time { day, .. } => day,
        }
    }
}

/// Whether `c` ends a line somewhere, or is one no line should hold: the C0 and C1 controls and the Unicode line and
/// paragraph separators.
fn breaks(c: char) -> bool {
    matches!(c, '\u{0}'..='\u{1f}' | '\u{7f}'..='\u{9f}' | '\u{2028}' | '\u{2029}')
}

/// Text as one line, within `cap` characters. A calendar's lines are its structure, so nothing a sender wrote may break
/// one. What isn't text is "".
pub fn one_line(value: Option<&serde_json::Value>, cap: usize) -> String {
    let Some(serde_json::Value::String(text)) = value else { return String::new() };
    let spaced: String = text.chars().map(|c| if breaks(c) { ' ' } else { c }).collect();
    let words: Vec<&str> = spaced.split(crate::text::is_space).filter(|w| !w.is_empty()).collect();
    words.join(" ").chars().take(cap).collect()
}

fn digits(s: &str) -> Option<i64> {
    (!s.is_empty() && s.bytes().all(|b| b.is_ascii_digit())).then(|| s.parse().ok()).flatten()
}

fn days_in(year: i64, month: u32) -> u32 {
    match month {
        2 if (year % 4 == 0 && year % 100 != 0) || year % 400 == 0 => 29,
        2 => 28,
        4 | 6 | 9 | 11 => 30,
        _ => 31,
    }
}

/// `YYYY-MM-DD` as days from the epoch, or None where it names no day.
fn read_day(s: &str) -> Option<i64> {
    let b = s.as_bytes();
    if b.len() != 10 || b[4] != b'-' || b[7] != b'-' {
        return None;
    }
    let year = digits(&s[..4])?;
    let month = u32::try_from(digits(&s[5..7])?).ok()?;
    let day = u32::try_from(digits(&s[8..10])?).ok()?;
    if year < 1 || !(1..=12).contains(&month) || day < 1 || day > days_in(year, month) {
        return None;
    }
    Some(days_from_civil(year, month, day))
}

/// `HH`, `HHMM` or `HH:MM` after a sign, as seconds east of UTC, or None. Python's timezone takes less than a day.
fn read_offset(s: &str) -> Option<i64> {
    let (sign, rest) = match s.as_bytes().first()? {
        b'+' => (1, &s[1..]),
        b'-' => (-1, &s[1..]),
        _ => return None,
    };
    let (hours, minutes) = match rest.len() {
        2 => (digits(rest)?, 0),
        4 => (digits(&rest[..2])?, digits(&rest[2..])?),
        5 if rest.as_bytes()[2] == b':' => (digits(&rest[..2])?, digits(&rest[3..])?),
        _ => return None,
    };
    if hours >= 24 || minutes >= 60 {
        return None;
    }
    Some(sign * (hours * 3600 + minutes * 60))
}

/// A time as `datetime.fromisoformat` reads the forms `kernel.proposed` lets through.
fn read_time(s: &str) -> Option<Reading> {
    if !s.is_ascii() || s.len() < 16 {
        return None;
    }
    let day = read_day(&s[..10])?;
    if !matches!(s.as_bytes()[10], b'T' | b' ') || s.as_bytes()[13] != b':' {
        return None;
    }
    let (hour, minute) = (digits(&s[11..13])?, digits(&s[14..16])?);
    let mut rest = &s[16..];
    let (mut second, mut micros) = (0, 0u32);
    if let Some(after) = rest.strip_prefix(':') {
        second = digits(after.get(..2)?)?;
        rest = &after[2..];
        if let Some(after) = rest.strip_prefix('.') {
            let n = after.bytes().take_while(u8::is_ascii_digit).count();
            if !(1..=6).contains(&n) {
                return None;
            }
            let fraction = &after[..n];
            micros = u32::try_from(digits(fraction)? * 10_i64.pow(6 - n as u32)).ok()?;
            rest = &after[n..];
        }
    }
    if hour >= 24 || minute >= 60 || second >= 60 {
        return None;
    }
    let offset = match rest {
        "" => None,
        "Z" => Some(0),
        _ => Some(read_offset(rest)?),
    };
    Some(Reading::Time { day, second: hour * 3600 + minute * 60 + second, micros, offset })
}

fn reading(text: Option<&serde_json::Value>) -> Option<Reading> {
    let Some(serde_json::Value::String(text)) = text else { return None };
    let s = crate::text::strip(text);
    if s.len() == 10 {
        return read_day(s).map(Reading::Day);
    }
    read_time(s)
}

fn within(value: Reading, today: Day) -> bool {
    let today = days_from_civil(today.0, today.1, today.2);
    (today - BEHIND_DAYS..=today + AHEAD_DAYS).contains(&value.day())
}

fn day_text(days: i64) -> String {
    let (y, m, d) = civil(days);
    format!("{y:04}-{m:02}-{d:02}")
}

fn offset_text(offset: i64) -> String {
    let sign = if offset < 0 { '-' } else { '+' };
    let at = offset.abs();
    format!("{sign}{:02}:{:02}", at / 3600, at % 3600 / 60)
}

/// A day, or a time with its offset where it has one, as ISO text to the minute, or None.
pub fn read_moment(text: Option<&serde_json::Value>, today: Day) -> Option<String> {
    let value = reading(text).filter(|v| within(*v, today))?;
    Some(match value {
        Reading::Day(day) => day_text(day),
        Reading::Time { day, second, offset, .. } => {
            let mut out = format!("{}T{:02}:{:02}", day_text(day), second / 3600, second % 3600 / 60);
            if let Some(offset) = offset {
                out.push_str(&offset_text(offset));
            }
            out
        }
    })
}

/// A day as ISO text, or None. A time reads as its day.
pub fn read_date(text: Option<&serde_json::Value>, today: Day) -> Option<String> {
    reading(text).filter(|v| within(*v, today)).map(|v| day_text(v.day()))
}

/// Whether what `read_moment` gave is a whole day, rather than a time.
pub fn is_day(moment: &str) -> bool {
    moment.len() == 10 && read_day(moment).is_some()
}

/// Whether `end` can end an event that begins at `start`, both as `read_moment` gave them: of one kind, not before it
/// (a day may end on itself, a time may not), and no more than LONGEST past it.
pub fn ends_after(start: &str, end: &str) -> bool {
    let as_value = |s: &str| serde_json::Value::String(s.to_owned());
    let (Some(a), Some(b)) = (reading(Some(&as_value(start))), reading(Some(&as_value(end)))) else { return false };
    match (a, b) {
        (
            Reading::Time { day: da, second: sa, micros: ma, offset: oa },
            Reading::Time { day: db, second: sb, micros: mb, offset: ob },
        ) => {
            if oa.is_some() != ob.is_some() {
                return false;
            }
            let at = |day: i64, second: i64, micros: u32, offset: Option<i64>| {
                (day * 86_400 + second - offset.unwrap_or(0)) * 1_000_000 + i64::from(micros)
            };
            let gap = at(db, sb, mb, ob) - at(da, sa, ma, oa);
            0 < gap && gap <= LONGEST_SECONDS * 1_000_000
        }
        (Reading::Day(a), Reading::Day(b)) => (0..=LONGEST_SECONDS / 86_400).contains(&(b - a)),
        _ => false,
    }
}

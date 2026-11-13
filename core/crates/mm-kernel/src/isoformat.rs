//! A time written as text, read as Python's `datetime.fromisoformat` read it and counted in seconds as
//! `int(....timestamp())` counted them: what follows a search's `before:` and `after:`, a snooze's `until`, and a
//! stored `date_iso` as a list says how long ago it was.
//!
//! That reader is CPython's C code (Modules/_datetimemodule.c, 3.14), and it reads more than ISO 8601: any one
//! character between the date and the time, week dates, 24:00, an offset to the microsecond, and anything after a
//! NUL. It is copied here step for step over the text's UTF-8, which is what the C code reads, with a NUL past the
//! end as a C string has. A date that read one way in the Python app reads the same way here.

/// A wall-clock time to the second, written with no zone: what the person's own zone has to place. Python placed
/// the second and added the microseconds after, so a zone only ever sees whole seconds.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Wall {
    pub year: i32,
    pub month: u32,
    pub day: u32,
    pub hour: u32,
    pub minute: u32,
    pub second: u32,
}

/// `written` as seconds since the epoch, or None where it reads as no time at all, as
/// `int(datetime.fromisoformat(written).timestamp())` raising was.
///
/// A time written with an offset is counted from it. One written without is placed by `zone`, which gives the
/// seconds since the epoch at that wall-clock time, or None where it can't place it.
pub fn seconds(written: &str, zone: impl Fn(Wall) -> Option<i64>) -> Option<i64> {
    let time = read(written)?;
    match time.offset {
        // Python divided exactly and rounded to the nearest float, then cut the fraction off.
        Some(offset) => Some(nearest_then_truncated(time.micros_since_epoch() - offset)),
        // Python added the microseconds as a float, then cut the fraction off.
        None => Some((zone(time.wall)? as f64 + f64::from(time.microsecond) / 1e6) as i64),
    }
}

/// A time as `datetime.fromisoformat` read it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Time {
    pub wall: Wall,
    pub microsecond: u32,
    /// Microseconds east of UTC, when it was written with one.
    pub offset: Option<i64>,
}

impl Time {
    /// The wall-clock time and its microseconds, counted as though it were UTC.
    pub fn micros_since_epoch(&self) -> i64 {
        let w = self.wall;
        let days = i64::from(ordinal(w.year, w.month, w.day)) - EPOCH_ORDINAL;
        let seconds = days * 86_400 + i64::from(w.hour * 3600 + w.minute * 60 + w.second);
        seconds * MICROS + i64::from(self.microsecond)
    }
}

const MICROS: i64 = 1_000_000;
/// 1970-01-01 counted from 0001-01-01 as day 1.
const EPOCH_ORDINAL: i64 = 719_163;

/// `datetime_fromisoformat`: the time `written` says, or None where Python raised.
pub fn read(written: &str) -> Option<Time> {
    // `_sanitize_isoformat_str` turns away fewer than seven characters, counted as characters.
    if written.chars().count() < 7 {
        return None;
    }
    let s = CStr(written.as_bytes());
    let sep = separator(&s)?;
    let (mut year, mut month, mut day) = date(&s, sep)?;
    let (mut clock, offset) = if s.len() > sep {
        // The separator is any one character, however many bytes it takes.
        time(&s, sep + char_len(s.at(sep)))?
    } else {
        (Clock::default(), None)
    };
    let offset = match offset {
        None => None,
        // A zero offset is UTC, whatever microseconds were written after it.
        Some(Offset { seconds: 0, .. }) => Some(0),
        Some(Offset { seconds, micros }) => {
            let total = seconds * MICROS + micros;
            // A timezone's offset is strictly less than a day either way.
            if total.abs() >= 86_400 * MICROS {
                return None;
            }
            Some(total)
        }
    };
    // 24:00 is midnight at the end of the day, checked against the month before the date itself is: so the 0th of
    // a month and month 0 both make it through, as they did in Python.
    if clock.hour == 24 && month <= 12 {
        let days = days_in_month(year, month);
        if day <= days {
            if clock.minute != 0 || clock.second != 0 || clock.microsecond != 0 {
                return None;
            }
            clock.hour = 0;
            day += 1;
            if day > days {
                day = 1;
                month += 1;
                if month > 12 {
                    month = 1;
                    year += 1;
                }
            }
        }
    }
    let real_date =
        (1..=9999).contains(&year) && (1..=12).contains(&month) && (1..=days_in_month(year, month)).contains(&day);
    let real_time = clock.hour <= 23 && clock.minute <= 59 && clock.second <= 59;
    if !real_date || !real_time {
        return None;
    }
    let wall = Wall { year, month, day, hour: clock.hour, minute: clock.minute, second: clock.second };
    Some(Time { wall, microsecond: clock.microsecond, offset })
}

/// `_find_isoformat_datetime_separator`: where the date ends, from the shape of its first few bytes. None is its
/// -1, which no date that follows ever parses with.
fn separator(s: &CStr) -> Option<usize> {
    let len = s.len();
    if len == 7 {
        return Some(7);
    }
    if s.at(4) == b'-' {
        if s.at(5) != b'W' {
            return Some(10);
        }
        if len < 8 {
            return None;
        }
        if len > 8 && s.at(8) == b'-' {
            if len == 9 {
                return None;
            }
            return Some(if len > 10 && s.at(10).is_ascii_digit() { 8 } else { 10 });
        }
        return Some(8);
    }
    if s.at(4) == b'W' {
        let mut end = 7;
        while end < len && s.at(end).is_ascii_digit() {
            end += 1;
        }
        if end < 9 {
            return Some(end);
        }
        return Some(if end % 2 == 0 { 7 } else { 8 });
    }
    Some(8)
}

/// `parse_isoformat_date`: YYYY-MM-DD, YYYYMMDD, or a week date, YYYY-Www-D, YYYYWwwD, or either without its day.
fn date(s: &CStr, sep: usize) -> Option<(i32, u32, u32)> {
    let mut p = 0;
    let year = s.digits(&mut p, 4)?;
    let dashed = s.at(p) == b'-';
    if dashed {
        p += 1;
    }
    if s.at(p) == b'W' {
        p += 1;
        let week = s.digits(&mut p, 2)?;
        let weekday = if p < sep {
            if dashed && !s.take(&mut p, b'-') {
                return None;
            }
            s.digits(&mut p, 1)?
        } else {
            1
        };
        return from_week(year as i32, week, weekday);
    }
    let month = s.digits(&mut p, 2)?;
    if dashed && !s.take(&mut p, b'-') {
        return None;
    }
    let day = s.digits(&mut p, 2)?;
    Some((year as i32, month, day))
}

#[derive(Default)]
struct Clock {
    hour: u32,
    minute: u32,
    second: u32,
    microsecond: u32,
}

struct Offset {
    seconds: i64,
    micros: i64,
}

/// `parse_isoformat_time`: the time from `start` to the end, and the offset after it if there is one. The offset
/// starts at the first Z, + or - byte.
fn time(s: &CStr, start: usize) -> Option<(Clock, Option<Offset>)> {
    let end = s.len();
    let mut zone_at = start;
    loop {
        if matches!(s.at(zone_at), b'Z' | b'+' | b'-') {
            break;
        }
        zone_at += 1;
        if zone_at >= end {
            break;
        }
    }
    let (clock, more) = hh_mm_ss_ff(s, start, zone_at)?;
    if zone_at == end {
        return (!more).then_some((clock, None));
    }
    if s.at(zone_at) == b'Z' {
        return (s.at(zone_at + 1) == 0).then_some((clock, Some(Offset { seconds: 0, micros: 0 })));
    }
    let sign = if s.at(zone_at) == b'-' { -1 } else { 1 };
    let (zone, more) = hh_mm_ss_ff(s, zone_at + 1, end)?;
    if more {
        return None;
    }
    let seconds = sign * i64::from(zone.hour * 3600 + zone.minute * 60 + zone.second);
    Some((clock, Some(Offset { seconds, micros: sign * i64::from(zone.microsecond) })))
}

/// `parse_hh_mm_ss_ff`: HH[:MM[:SS[.ffffff]]] or the same without colons, between `start` and `end`. The flag says
/// whether anything but a NUL follows where it stopped, which is the C code's "not the end of the string".
fn hh_mm_ss_ff(s: &CStr, start: usize, end: usize) -> Option<(Clock, bool)> {
    let mut parts = [0; 3];
    let mut p = start;
    let mut colons = true;
    for (i, part) in parts.iter_mut().enumerate() {
        *part = s.digits(&mut p, 2)?;
        let c = s.at(p);
        p += 1;
        if i == 0 {
            colons = c == b':';
        }
        if p >= end {
            let [hour, minute, second] = parts;
            return Some((Clock { hour, minute, second, microsecond: 0 }, c != 0));
        }
        if colons && c == b':' {
            if i == 2 {
                return None;
            }
        } else if c == b'.' || c == b',' {
            if i < 2 {
                return None;
            }
            break;
        } else if colons {
            return None;
        } else {
            p -= 1;
        }
    }
    // Up to six digits of a fraction, scaled to microseconds; the C code skips any more.
    let count = end.saturating_sub(p).min(6);
    let digits = s.digits(&mut p, count)?;
    while s.at(p).is_ascii_digit() {
        p += 1;
    }
    let [hour, minute, second] = parts;
    let microsecond = digits * 10u32.pow(6u32.saturating_sub(count as u32));
    Some((Clock { hour, minute, second, microsecond }, s.at(p) != 0))
}

/// A string's UTF-8 as C reads it, with a NUL past the end.
struct CStr<'a>(&'a [u8]);

impl CStr<'_> {
    fn len(&self) -> usize {
        self.0.len()
    }

    fn at(&self, i: usize) -> u8 {
        self.0.get(i).copied().unwrap_or(0)
    }

    /// `parse_digits`: exactly `count` ASCII digits from `p`, which moves past them.
    fn digits(&self, p: &mut usize, count: usize) -> Option<u32> {
        let mut value = 0;
        for _ in 0..count {
            let c = self.at(*p);
            *p += 1;
            if !c.is_ascii_digit() {
                return None;
            }
            value = value * 10 + u32::from(c - b'0');
        }
        Some(value)
    }

    /// Whether the byte at `p` is `want`; `p` moves past it either way.
    fn take(&self, p: &mut usize, want: u8) -> bool {
        let c = self.at(*p);
        *p += 1;
        c == want
    }
}

/// How many bytes the C code skips for the separator, from its first.
fn char_len(lead: u8) -> usize {
    if lead & 0x80 == 0 {
        return 1;
    }
    match lead & 0xf0 {
        0xe0 => 3,
        0xf0 => 4,
        _ => 2,
    }
}

/// `int(micros / 10**6)`: Python's division rounds to the nearest float, ties to even, and `int` cuts the fraction
/// off. Far enough from 1970, the nearest float to x.99999 is x + 1, so a whole second more than the true quotient.
fn nearest_then_truncated(micros: i64) -> i64 {
    let (whole, rest) = (micros / MICROS, micros % MICROS);
    if rest == 0 || whole == 0 {
        return whole;
    }
    // Floats just below |whole| + 1 lie 2^(e - 52) apart, e being the power of two |whole| starts at. Whole + 1 is
    // the nearest when the quotient is within half that, and wins a tie, being the even one of the two.
    let gap = (MICROS - rest.abs()) as u128;
    let e = 63 - whole.unsigned_abs().leading_zeros();
    if e <= 51 && gap << (53 - e) <= MICROS as u128 { whole + whole.signum() } else { whole }
}

fn is_leap(year: i32) -> bool {
    year % 4 == 0 && (year % 100 != 0 || year % 400 == 0)
}

/// Days in a month, and 0 for month 0, as the C code's table has it.
fn days_in_month(year: i32, month: u32) -> u32 {
    const DAYS: [u32; 13] = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
    if month == 2 && is_leap(year) { 29 } else { DAYS.get(month as usize).copied().unwrap_or(0) }
}

/// Days from 0001-01-01, which is day 1. Only for a real date.
fn ordinal(year: i32, month: u32, day: u32) -> i32 {
    const BEFORE: [u32; 13] = [0, 0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334];
    let y = year - 1;
    let before_month = BEFORE[month as usize] + u32::from(month > 2 && is_leap(year));
    y * 365 + y / 4 - y / 100 + y / 400 + (before_month + day) as i32
}

/// `iso_to_ymd`: the date of a week date, or None where the week or the day doesn't exist.
fn from_week(year: i32, week: u32, weekday: u32) -> Option<(i32, u32, u32)> {
    if !(1..=9999).contains(&year) {
        return None;
    }
    if week == 0 || week >= 53 {
        // A year has a 53rd week when it starts on a Thursday, or on a Wednesday in a leap year.
        let starts = (ordinal(year, 1, 1) + 6) % 7;
        if week != 53 || !(starts == 3 || (starts == 2 && is_leap(year))) {
            return None;
        }
    }
    if weekday == 0 || weekday >= 8 {
        return None;
    }
    // The Monday of week 1, the first with a Thursday in it.
    let first = ordinal(year, 1, 1);
    let first_weekday = (first + 6) % 7;
    let monday = first - first_weekday + if first_weekday > 3 { 7 } else { 0 };
    Some(from_ordinal(monday + (week as i32 - 1) * 7 + weekday as i32 - 1))
}

/// `ord_to_ymd`: the date of a day counted from 0001-01-01 as day 1.
fn from_ordinal(ordinal: i32) -> (i32, u32, u32) {
    const DI400Y: i32 = 146_097;
    const DI100Y: i32 = 36_524;
    const DI4Y: i32 = 1_461;
    let n = ordinal - 1;
    let (n400, n) = (n / DI400Y, n % DI400Y);
    let (n100, n) = (n / DI100Y, n % DI100Y);
    let (n4, n) = (n / DI4Y, n % DI4Y);
    let (n1, n) = (n / 365, n % 365);
    let year = n400 * 400 + 1 + n100 * 100 + n4 * 4 + n1;
    if n1 == 4 || n100 == 4 {
        return (year - 1, 12, 31);
    }
    let mut month = 1;
    let mut left = n as u32;
    while left >= days_in_month(year, month) {
        left -= days_in_month(year, month);
        month += 1;
    }
    (year, month, left + 1)
}

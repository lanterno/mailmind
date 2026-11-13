//! How long ago a moment was, or how soon one comes, as the page says it (`mailmind.kernel.when`): `3h ago`,
//! `yesterday`, `Oct 27`; `5d`; `wakes tomorrow 9:00 AM`. Each is told the time now, so nothing here reads a clock.
//!
//! The Python app says a wake time in the computer's own time zone. The Rust server has no time zone yet, so it says
//! one in UTC, as the suites' clock (`TZ=UTC`) does.

use crate::seams::Moment;
use crate::when::{civil, days_from_civil};

const DAY: i64 = 86_400;
const MONTHS: [&str; 12] = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const WEEKDAYS: [&str; 7] = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/// A time as `datetime.fromisoformat` reads one: the moment, and the offset from UTC it was written in, in seconds.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Written {
    pub at: Moment,
    pub offset: i64,
}

/// `rel_time`: how long before `now` the stored ISO time `iso` was. A time that won't read is its first ten
/// characters, and none is "".
pub fn rel_time(iso: Option<&str>, now: Moment) -> String {
    let Some(iso) = iso.filter(|i| !i.is_empty()) else {
        return String::new();
    };
    let Some(written) = from_iso(iso) else {
        return iso.chars().take(10).collect();
    };
    let delta = now.micros() - written.at.micros();
    let days = delta.div_euclid(DAY * 1_000_000);
    let seconds = delta.rem_euclid(DAY * 1_000_000) / 1_000_000;
    match days {
        0 if seconds / 3600 == 0 => "just now".to_owned(),
        0 => format!("{}h ago", seconds / 3600),
        1 => "yesterday".to_owned(),
        d if d < 7 => format!("{d}d ago"),
        d if d < 30 => format!("{}w ago", d.div_euclid(7)),
        _ => {
            let local = written.at.micros().div_euclid(1_000_000) + written.offset;
            let (_, month, day) = civil(local.div_euclid(DAY));
            format!("{} {day:02}", MONTHS[month as usize - 1])
        }
    }
}

/// `ago`: how long before `now` the Unix time `ts` was, as `3d`, `5h` or `12m`. None, or 0, is "a while".
pub fn ago(ts: Option<i64>, now: Moment) -> String {
    let Some(ts) = ts.filter(|t| *t != 0) else {
        return "a while".to_owned();
    };
    let s = (now.whole_seconds() - ts).max(0);
    if s < 3600 {
        format!("{}m", s / 60)
    } else if s < DAY {
        format!("{}h", s / 3600)
    } else {
        format!("{}d", s / DAY)
    }
}

/// `rel_time_future`: when the Unix time `ts` comes, after `now`: `soon` once it is past, then `in 12m`,
/// `today 3:05 PM`, `tomorrow 9:00 AM`, `Fri 9:00 AM` within the week and `Nov 3` after.
pub fn rel_time_future(ts: i64, now: Moment) -> String {
    let secs = (ts * 1_000_000 - now.micros()) as f64 / 1_000_000.0;
    if secs < 0.0 {
        return "soon".to_owned();
    }
    if secs < 3600.0 {
        return format!("in {}m", (secs / 60.0).floor() as i64);
    }
    let day = ts.div_euclid(DAY);
    let days_on = day - now.whole_seconds().div_euclid(DAY);
    let of_day = ts.rem_euclid(DAY);
    let (hour, minute) = (of_day / 3600, of_day % 3600 / 60);
    let clock = format!("{}:{minute:02} {}", (hour + 11) % 12 + 1, if hour < 12 { "AM" } else { "PM" });
    match days_on {
        0 => format!("today {clock}"),
        1 => format!("tomorrow {clock}"),
        d if d < 7 => format!("{} {clock}", WEEKDAYS[(day + 3).rem_euclid(7) as usize]),
        _ => {
            let (_, month, date) = civil(day);
            format!("{} {date}", MONTHS[month as usize - 1])
        }
    }
}

/// `datetime.fromisoformat(text)`, as far as stored times go: a date (`YYYY-MM-DD` or `YYYYMMDD`), then any one
/// character and a time (`HH[:MM[:SS[.ffffff]]]`, or the same without colons), and an offset (`Z`, `±HH[:MM[:SS]]`
/// or `±HHMM`). One with no offset is UTC, as the Python app takes it.
pub fn from_iso(text: &str) -> Option<Written> {
    let chars: Vec<char> = text.chars().collect();
    let (date_len, year, month, day) = if chars.get(4) == Some(&'-') {
        if chars.len() < 10 || chars[7] != '-' {
            return None;
        }
        (10, digits(&chars[0..4])?, digits(&chars[5..7])?, digits(&chars[8..10])?)
    } else {
        if chars.len() < 8 {
            return None;
        }
        (8, digits(&chars[0..4])?, digits(&chars[4..6])?, digits(&chars[6..8])?)
    };
    if year < 1 || !(1..=12).contains(&month) || day < 1 || day > days_in(year, month) {
        return None;
    }
    let days = days_from_civil(year, month as u32, day as u32);
    let rest = &chars[date_len..];
    let (of_day, micros, offset) = if rest.is_empty() { (0, 0, 0) } else { time(&rest[1..])? };
    let local = days * DAY + of_day;
    Some(Written { at: Moment::from_micros((local - offset) * 1_000_000 + micros), offset })
}

fn time(text: &[char]) -> Option<(i64, i64, i64)> {
    let split = text.iter().position(|c| matches!(c, '+' | '-' | 'Z')).unwrap_or(text.len());
    let (clock, zone) = text.split_at(split);
    let (whole, fraction) = match clock.iter().position(|c| matches!(c, '.' | ',')) {
        Some(i) => (&clock[..i], &clock[i + 1..]),
        None => (clock, &[][..]),
    };
    let [hour, minute, second] = fields(whole)?;
    if hour > 23 || minute > 59 || second > 59 {
        return None;
    }
    let micros = if fraction.is_empty() {
        if clock.len() != whole.len() {
            return None;
        }
        0
    } else {
        if !fraction.iter().all(char::is_ascii_digit) || second_less(whole) {
            return None;
        }
        let mut six: String = fraction.iter().take(6).collect();
        while six.len() < 6 {
            six.push('0');
        }
        six.parse().ok()?
    };
    let offset = match zone.first() {
        None => 0,
        Some('Z') if zone.len() == 1 => 0,
        Some(sign @ ('+' | '-')) => {
            let [h, m, s] = fields(&zone[1..])?;
            if h > 23 || m > 59 || s > 59 {
                return None;
            }
            let seconds = h * 3600 + m * 60 + s;
            if *sign == '-' { -seconds } else { seconds }
        }
        _ => return None,
    };
    Some((hour * 3600 + minute * 60 + second, micros, offset))
}

/// A time's hours, minutes and seconds, with colons between them or none: `HH`, `HH:MM`, `HH:MM:SS`, `HHMM`,
/// `HHMMSS`.
fn fields(text: &[char]) -> Option<[i64; 3]> {
    let parts: Vec<&[char]> =
        if text.contains(&':') { text.split(|c| *c == ':').collect() } else { text.chunks(2).collect() };
    if parts.is_empty() || parts.len() > 3 || parts.iter().any(|p| p.len() != 2) {
        return None;
    }
    let mut out = [0; 3];
    for (slot, part) in out.iter_mut().zip(&parts) {
        *slot = digits(part)?;
    }
    Some(out)
}

/// Whether a time stops short of its seconds, which a fraction may not follow.
fn second_less(whole: &[char]) -> bool {
    whole.iter().filter(|c| c.is_ascii_digit()).count() < 6
}

fn digits(text: &[char]) -> Option<i64> {
    if text.is_empty() || !text.iter().all(char::is_ascii_digit) {
        return None;
    }
    text.iter().try_fold(0i64, |n, c| Some(n * 10 + i64::from(c.to_digit(10)?)))
}

fn days_in(year: i64, month: i64) -> i64 {
    match month {
        2 if (year % 4 == 0 && year % 100 != 0) || year % 400 == 0 => 29,
        2 => 28,
        4 | 6 | 9 | 11 => 30,
        _ => 31,
    }
}

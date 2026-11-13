//! The person's own zone: where a wall-clock time stands. What a snooze to "tomorrow" means, where a search's
//! `before:2026-11-02` ends and which day a task is due by. The server is the person's machine, so it is the
//! machine's zone, as Python's `time.localtime` reads it; the program hands it in, from `TZ` or the system's zone.
//!
//! A zone is the offset it has at each moment. Placing a wall-clock time, which a gap or a fold makes ambiguous, is
//! Python's `datetime._mktime` over those offsets, so a time is placed here where Python placed it. A zone file
//! (TZif, RFC 8536) is read from its bytes here; opening one is the store's.

use std::fmt;

use crate::when::{civil, days_from_civil};

/// A zone: its offset from UTC at each moment.
pub trait Zone: Send + Sync + fmt::Debug {
    /// Seconds east of UTC at the moment `utc`, in seconds since the epoch.
    fn offset_at(&self, utc: i64) -> i64;
}

/// Coordinated Universal Time, the zone the test suites run in.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Utc;

impl Zone for Utc {
    fn offset_at(&self, _utc: i64) -> i64 {
        0
    }
}

/// The wall-clock time at `utc` in `zone`, counted in seconds as though it were UTC.
pub fn local(zone: &dyn Zone, utc: i64) -> i64 {
    utc + zone.offset_at(utc)
}

/// A wall-clock time in `zone` as the moment it names, `local` counted in seconds as though it were UTC: Python's
/// `_mktime` with `fold` 0. In a fold it is the earlier moment, and in a gap the later of the two the offsets
/// either side of it give.
pub fn place(zone: &dyn Zone, local_secs: i64) -> i64 {
    const MAX_FOLD: i64 = 24 * 3600;
    let at = |u: i64| local(zone, u);
    let t = local_secs;
    let a = at(t) - t;
    let u1 = t - a;
    let t1 = at(u1);
    let b = if t1 == t {
        let u2 = u1 - MAX_FOLD;
        let b = at(u2) - u2;
        if a == b {
            return u1;
        }
        b
    } else {
        t1 - u1
    };
    let u2 = t - b;
    if at(u2) == t {
        return u2;
    }
    if t1 == t {
        return u1;
    }
    u1.max(u2)
}

/// The day (days since 1970-01-01) and the second of that day it is at `utc` in `zone`.
pub fn day_and_second(zone: &dyn Zone, utc: i64) -> (i64, i64) {
    let wall = local(zone, utc);
    (wall.div_euclid(86_400), wall.rem_euclid(86_400))
}

/// The date it is at `utc` in `zone`: (year, month, day).
pub fn date(zone: &dyn Zone, utc: i64) -> (i64, u32, u32) {
    civil(day_and_second(zone, utc).0)
}

/// A zone read from a TZif file: its transitions, and the rule its footer gives for the times after the last.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Tzif {
    /// The moments the offset changes, in order, each with the offset from then on.
    transitions: Vec<(i64, i64)>,
    /// The offset before the first transition: the first time type's.
    before: i64,
    footer: Option<Rule>,
}

impl Tzif {
    /// A zone file's bytes as a zone, or None for what isn't one.
    pub fn parse(bytes: &[u8]) -> Option<Tzif> {
        let header = Header::read(bytes)?;
        let (header, body, wide) = if header.version >= b'2' {
            let second = bytes.get(header.v1_len(4)..)?;
            (Header::read(second)?, second.get(44..)?, true)
        } else {
            (header, bytes.get(44..)?, false)
        };
        let size = if wide { 8 } else { 4 };
        let times = body.get(..header.timecnt * size)?;
        let indices = body.get(header.timecnt * size..header.timecnt * (size + 1))?;
        let types_at = header.timecnt * (size + 1);
        let types = body.get(types_at..types_at + header.typecnt * 6)?;
        let offsets: Vec<i64> =
            types.as_chunks::<6>().0.iter().map(|t| i64::from(i32::from_be_bytes([t[0], t[1], t[2], t[3]]))).collect();
        let before = *offsets.first()?;
        let mut transitions = Vec::with_capacity(header.timecnt);
        for (i, &index) in indices.iter().enumerate() {
            let raw = &times[i * size..(i + 1) * size];
            let at = if wide {
                i64::from_be_bytes(raw.try_into().ok()?)
            } else {
                i64::from(i32::from_be_bytes(raw.try_into().ok()?))
            };
            transitions.push((at, *offsets.get(usize::from(index))?));
        }
        let footer = if wide {
            let rest = body.get(header.v2_body_len()..)?;
            let text = std::str::from_utf8(rest).ok()?;
            text.strip_prefix('\n').and_then(|t| t.split('\n').next()).and_then(Rule::parse)
        } else {
            None
        };
        Some(Tzif { transitions, before, footer })
    }
}

impl Zone for Tzif {
    fn offset_at(&self, utc: i64) -> i64 {
        match self.transitions.last() {
            None => self.footer.as_ref().map_or(self.before, |rule| rule.offset_at(utc)),
            Some(&(last, offset)) if utc >= last => self.footer.as_ref().map_or(offset, |rule| rule.offset_at(utc)),
            Some(_) => {
                let after = self.transitions.partition_point(|&(at, _)| at <= utc);
                if after == 0 { self.before } else { self.transitions[after - 1].1 }
            }
        }
    }
}

struct Header {
    version: u8,
    isutcnt: usize,
    isstdcnt: usize,
    leapcnt: usize,
    timecnt: usize,
    typecnt: usize,
    charcnt: usize,
}

impl Header {
    fn read(bytes: &[u8]) -> Option<Header> {
        if bytes.get(..4)? != b"TZif" {
            return None;
        }
        let count = |at: usize| -> Option<usize> {
            let raw: [u8; 4] = bytes.get(at..at + 4)?.try_into().ok()?;
            usize::try_from(u32::from_be_bytes(raw)).ok()
        };
        Some(Header {
            version: *bytes.get(4)?,
            isutcnt: count(20)?,
            isstdcnt: count(24)?,
            leapcnt: count(28)?,
            timecnt: count(32)?,
            typecnt: count(36)?,
            charcnt: count(40)?,
        })
    }

    /// The length of the version 1 header and data, times of `size` bytes each.
    fn v1_len(&self, size: usize) -> usize {
        44 + self.timecnt * (size + 1)
            + self.typecnt * 6
            + self.charcnt
            + self.leapcnt * (size + 4)
            + self.isstdcnt
            + self.isutcnt
    }

    /// The length of the version 2 data after its header.
    fn v2_body_len(&self) -> usize {
        self.v1_len(8) - 44
    }
}

/// A POSIX TZ rule, such as `CET-1CEST,M3.5.0,M10.5.0/3`: a standard offset, and a daylight one with when each year
/// it starts and ends.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Rule {
    standard: i64,
    daylight: Option<(i64, When, When)>,
}

/// A day of a year, and the second of local time on it, as a rule names when daylight time starts or ends.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum When {
    /// `Jn`: the nth day, 1 to 365, February 29th never counted.
    Julian(i64, i64),
    /// `n`: the nth day counted from 0, February 29th counted in a leap year.
    Day(i64, i64),
    /// `Mm.w.d`: day d (0 Sunday) of week w (5 the last) of month m.
    Month(u32, u32, u32, i64),
}

impl Rule {
    /// A TZ string as a rule, or None for one that doesn't read.
    pub fn parse(text: &str) -> Option<Rule> {
        let mut s = Scan(text.as_bytes(), 0);
        s.name()?;
        let standard = -s.offset()?;
        if s.done() {
            return Some(Rule { standard, daylight: None });
        }
        s.name()?;
        let daylight = if s.peek().is_some_and(|c| c != b',') { -s.offset()? } else { standard + 3600 };
        let (start, end) = if s.done() {
            // No rule given: the United States', as the C library assumes.
            (When::Month(3, 2, 0, 7200), When::Month(11, 1, 0, 7200))
        } else {
            s.expect(b',')?;
            let start = s.when()?;
            s.expect(b',')?;
            (start, s.when()?)
        };
        s.done().then_some(Rule { standard, daylight: Some((daylight, start, end)) })
    }

    pub fn offset_at(&self, utc: i64) -> i64 {
        let Some((daylight, start, end)) = self.daylight else {
            return self.standard;
        };
        let (year, _, _) = civil((utc + self.standard).div_euclid(86_400));
        let starts = start.utc(year, self.standard);
        let ends = end.utc(year, daylight);
        let in_daylight = if starts < ends { starts <= utc && utc < ends } else { !(ends <= utc && utc < starts) };
        if in_daylight { daylight } else { self.standard }
    }
}

impl When {
    /// The moment it names in `year`, its time being local time `offset` seconds east of UTC.
    fn utc(self, year: i64, offset: i64) -> i64 {
        let leap = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);
        let jan1 = days_from_civil(year, 1, 1);
        let (day, second) = match self {
            When::Julian(n, second) => (jan1 + n - 1 + i64::from(leap && n >= 60), second),
            When::Day(n, second) => (jan1 + n, second),
            When::Month(month, week, weekday, second) => {
                let first = days_from_civil(year, month, 1);
                // 1970-01-01 was a Thursday.
                let first_weekday = (first + 4).rem_euclid(7);
                let mut day = first + (i64::from(weekday) - first_weekday).rem_euclid(7) + i64::from(week - 1) * 7;
                let next =
                    if month == 12 { days_from_civil(year + 1, 1, 1) } else { days_from_civil(year, month + 1, 1) };
                while day >= next {
                    day -= 7;
                }
                (day, second)
            }
        };
        day * 86_400 + second - offset
    }
}

struct Scan<'a>(&'a [u8], usize);

impl Scan<'_> {
    fn peek(&self) -> Option<u8> {
        self.0.get(self.1).copied()
    }

    fn done(&self) -> bool {
        self.1 >= self.0.len()
    }

    fn expect(&mut self, c: u8) -> Option<()> {
        (self.peek()? == c).then(|| self.1 += 1)
    }

    /// A zone's abbreviation: three letters or more, or anything between `<` and `>`.
    fn name(&mut self) -> Option<()> {
        if self.peek()? == b'<' {
            let end = self.0[self.1..].iter().position(|&c| c == b'>')?;
            self.1 += end + 1;
            return Some(());
        }
        let start = self.1;
        while self.peek().is_some_and(|c| c.is_ascii_alphabetic()) {
            self.1 += 1;
        }
        (self.1 - start >= 3).then_some(())
    }

    fn number(&mut self) -> Option<i64> {
        let start = self.1;
        while self.peek().is_some_and(|c| c.is_ascii_digit()) {
            self.1 += 1;
        }
        std::str::from_utf8(&self.0[start..self.1]).ok()?.parse().ok()
    }

    /// `[+-]hh[:mm[:ss]]` in seconds, as written.
    fn offset(&mut self) -> Option<i64> {
        let sign = match self.peek()? {
            b'-' => {
                self.1 += 1;
                -1
            }
            b'+' => {
                self.1 += 1;
                1
            }
            _ => 1,
        };
        let mut seconds = self.number()? * 3600;
        for unit in [60, 1] {
            if self.peek() != Some(b':') {
                break;
            }
            self.1 += 1;
            seconds += self.number()? * unit;
        }
        Some(sign * seconds)
    }

    fn when(&mut self) -> Option<When> {
        let day = match self.peek()? {
            b'J' => {
                self.1 += 1;
                When::Julian(self.number()?, 0)
            }
            b'M' => {
                self.1 += 1;
                let month = u32::try_from(self.number()?).ok().filter(|m| (1..=12).contains(m))?;
                self.expect(b'.')?;
                let week = u32::try_from(self.number()?).ok().filter(|w| (1..=5).contains(w))?;
                self.expect(b'.')?;
                let weekday = u32::try_from(self.number()?).ok().filter(|d| *d <= 6)?;
                When::Month(month, week, weekday, 0)
            }
            _ => When::Day(self.number()?, 0),
        };
        let second = if self.peek() == Some(b'/') {
            self.1 += 1;
            self.offset()?
        } else {
            7200
        };
        Some(match day {
            When::Julian(n, _) => When::Julian(n, second),
            When::Day(n, _) => When::Day(n, second),
            When::Month(m, w, d, _) => When::Month(m, w, d, second),
        })
    }
}

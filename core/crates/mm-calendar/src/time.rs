//! A calendar's time as Python's `datetime` holds one: a wall-clock time to the second and what it is in, which is a
//! zone the zone database names, a fixed offset (`datetime.fromisoformat` of a time written with one), or nothing at
//! all, a floating time read as the person's own wall clock.
//!
//! Python's arithmetic is copied, since what an invite means hangs on it. Adding to a time adds to its wall clock.
//! Two times in the same named zone are compared and subtracted by their wall clocks, any other two aware ones by
//! the moments they name. A wall-clock time in a named zone is placed as `zoneinfo` places it: `fold` 0 is the first
//! of two that a change of clocks repeats and, in a gap, the offset before it; `fold` 1 the other. Python's year
//! runs from 1 to 9999, and a time past either end is no time (its `OverflowError`).

use std::cmp::Ordering;
use std::fmt;
use std::sync::Arc;

use mm_kernel::isoformat;
use mm_kernel::when::{civil, days_from_civil};
use mm_kernel::zone::{self, Zone};

pub const DAY: i64 = 86_400;
/// 0001-01-01T00:00:00, as seconds from 1970 counted on the wall clock.
pub const MIN_WALL: i64 = -62_135_596_800;
/// 9999-12-31T23:59:59.
pub const MAX_WALL: i64 = 253_402_300_799;

const WEEKDAYS: [&str; 7] = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const MONTHS: [&str; 12] = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/// A zone the zone database names: `ZoneInfo(name)`. Two of the same name are the same zone, as `ZoneInfo`'s cache
/// makes them the same object.
#[derive(Clone)]
pub struct Named {
    pub name: String,
    pub zone: Arc<dyn Zone>,
}

impl fmt::Debug for Named {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_tuple("Named").field(&self.name).finish()
    }
}

/// What a time is in.
#[derive(Clone, Debug)]
pub enum Tz {
    /// No zone: the person's own wall clock.
    Floating,
    /// Seconds east of UTC.
    Fixed(i64),
    Named(Arc<Named>),
}

impl Tz {
    pub fn is_aware(&self) -> bool {
        !matches!(self, Tz::Floating)
    }

    /// Whether Python would take the two for the same `tzinfo` object, which compares by the wall clock.
    fn same(&self, other: &Tz) -> bool {
        match (self, other) {
            (Tz::Named(a), Tz::Named(b)) => a.name == b.name,
            _ => false,
        }
    }
}

/// A wall-clock time to the second and what it is in.
#[derive(Clone, Debug)]
pub struct Dt {
    /// The wall-clock time, counted in seconds from 1970 as though it were UTC.
    pub wall: i64,
    pub tz: Tz,
    /// The later of two wall-clock times a change of clocks repeats, as `zoneinfo` marks one it converted to.
    pub fold: bool,
}

/// `wall` if it is a time Python's years hold.
fn in_range(wall: i64) -> Option<i64> {
    (MIN_WALL..=MAX_WALL).contains(&wall).then_some(wall)
}

/// The offset `zone` has at the wall-clock time `wall`, as `ZoneInfo.utcoffset` reads it with `fold`.
fn offset_in(zone: &dyn Zone, wall: i64, fold: bool) -> i64 {
    let first = wall - zone::place(zone, wall);
    if !fold {
        return first;
    }
    // Either side of a change of clocks a day off, if there is one near.
    let (before, after) = (zone.offset_at(wall - DAY), zone.offset_at(wall + DAY));
    if before == after {
        return first;
    }
    let holds = |offset: i64| zone.offset_at(wall - offset) == offset;
    match (holds(before), holds(after)) {
        // Repeated: fold 1 is the later moment, the smaller offset.
        (true, true) => before.min(after),
        // Skipped: fold 1 is the offset after the change.
        (false, false) => after,
        (true, false) => before,
        (false, true) => after,
    }
}

impl Dt {
    pub fn new(wall: i64, tz: Tz) -> Dt {
        Dt { wall, tz, fold: false }
    }

    pub fn floating(wall: i64) -> Dt {
        Dt::new(wall, Tz::Floating)
    }

    /// A time from its parts, None for one that isn't a date or a time of day.
    pub fn from_parts(year: i64, month: u32, day: u32, hour: u32, minute: u32, second: u32, tz: Tz) -> Option<Dt> {
        if !(1..=9999).contains(&year)
            || !(1..=12).contains(&month)
            || day < 1
            || day > days_in_month(year, month)
            || hour > 23
            || minute > 59
            || second > 59
        {
            return None;
        }
        let wall = days_from_civil(year, month, day) * DAY + i64::from(hour * 3600 + minute * 60 + second);
        Some(Dt::new(wall, tz))
    }

    pub fn is_aware(&self) -> bool {
        self.tz.is_aware()
    }

    /// Seconds east of UTC, None for a floating time: `utcoffset()`.
    pub fn offset(&self) -> Option<i64> {
        match &self.tz {
            Tz::Floating => None,
            Tz::Fixed(offset) => Some(*offset),
            Tz::Named(named) => Some(offset_in(&*named.zone, self.wall, self.fold)),
        }
    }

    /// The moment an aware time names, in seconds since the epoch.
    pub fn utc(&self) -> Option<i64> {
        self.offset().map(|offset| self.wall - offset)
    }

    /// The moment it names, a floating time placed on the person's wall clock as Python's `astimezone()` placed it.
    pub fn moment(&self, person: &dyn Zone) -> i64 {
        self.utc().unwrap_or_else(|| zone::place(person, self.wall))
    }

    /// `astimezone(tz)`: the same moment on `tz`'s wall clock. None past Python's years.
    pub fn astimezone(&self, tz: &Tz, person: &dyn Zone) -> Option<Dt> {
        let utc = self.moment(person);
        match tz {
            Tz::Floating => Some(Dt::floating(in_range(zone::local(person, utc))?)),
            Tz::Fixed(offset) => Some(Dt::new(in_range(utc + offset)?, tz.clone())),
            Tz::Named(named) => {
                let wall = in_range(zone::local(&*named.zone, utc))?;
                let fold = zone::place(&*named.zone, wall) != utc;
                Some(Dt { wall, tz: tz.clone(), fold })
            }
        }
    }

    /// The person's wall-clock time at this moment, floating; a floating time is already that. Python's
    /// `astimezone().replace(tzinfo=None)`.
    pub fn local_naive(&self, person: &dyn Zone) -> Option<Dt> {
        if self.is_aware() { self.astimezone(&Tz::Floating, person) } else { Some(self.clone()) }
    }

    /// `replace(tzinfo=tz)`: the same wall clock, in `tz`.
    pub fn with_tz(&self, tz: Tz) -> Dt {
        Dt { wall: self.wall, tz, fold: self.fold }
    }

    /// `self + timedelta(seconds=seconds)`: on the wall clock, with fold 0. None past Python's years.
    pub fn plus(&self, seconds: i64) -> Option<Dt> {
        Some(Dt::new(in_range(self.wall.checked_add(seconds)?)?, self.tz.clone()))
    }

    /// `self - other` in seconds, as Python subtracts: by the wall clock in one named zone or where both float, by
    /// the moments otherwise.
    pub fn minus(&self, other: &Dt) -> i64 {
        match (self.utc(), other.utc()) {
            (Some(a), Some(b)) if !self.tz.same(&other.tz) => a - b,
            _ => self.wall - other.wall,
        }
    }

    /// Python's comparison of the two, for times both aware or both floating.
    pub fn cmp_to(&self, other: &Dt) -> Ordering {
        self.minus(other).cmp(&0)
    }

    /// The day it is on its own wall clock, in days since 1970-01-01.
    pub fn day(&self) -> i64 {
        self.wall.div_euclid(DAY)
    }

    /// Its seconds since its day began.
    pub fn second_of_day(&self) -> i64 {
        self.wall.rem_euclid(DAY)
    }

    /// (year, month, day) of its wall clock.
    pub fn date(&self) -> (i64, u32, u32) {
        civil(self.day())
    }

    /// Monday 0 to Sunday 6.
    pub fn weekday(&self) -> usize {
        weekday(self.day())
    }

    /// `datetime.isoformat()`: `YYYY-MM-DDTHH:MM:SS`, with its offset where it has one.
    pub fn isoformat(&self) -> String {
        let mut out = format!("{}T{}", date_iso(self.day()), clock(self.second_of_day()));
        if let Some(offset) = self.offset() {
            out.push_str(&offset_text(offset));
        }
        out
    }

    /// `isoformat(timespec="minutes")` of a floating time: `YYYY-MM-DDTHH:MM`.
    pub fn minutes_iso(&self) -> String {
        let second = self.second_of_day();
        format!("{}T{:02}:{:02}", date_iso(self.day()), second / 3600, second % 3600 / 60)
    }

    /// `strftime("%a, %b ") + str(day)`.
    pub fn day_words(&self) -> String {
        let (_, month, day) = self.date();
        format!("{}, {} {day}", WEEKDAYS[self.weekday()], MONTHS[month as usize - 1])
    }

    /// `strftime("%-I:%M %p")`.
    pub fn clock_words(&self) -> String {
        let second = self.second_of_day();
        let (hour, minute) = (second / 3600, second % 3600 / 60);
        let twelve = if hour % 12 == 0 { 12 } else { hour % 12 };
        format!("{twelve}:{minute:02} {}", if hour < 12 { "AM" } else { "PM" })
    }

    /// `strftime("%b %-d, %Y")`.
    pub fn month_day_year(&self) -> String {
        let (year, month, day) = self.date();
        format!("{} {day}, {year:04}", MONTHS[month as usize - 1])
    }

    /// `strftime("%Y%m%dT%H%M%S")`.
    pub fn basic(&self) -> String {
        let (year, month, day) = self.date();
        let second = self.second_of_day();
        format!("{year:04}{month:02}{day:02}T{:02}{:02}{:02}", second / 3600, second % 3600 / 60, second % 60)
    }
}

/// `datetime.fromisoformat(text)`, a time with no zone floating and one with an offset fixed at it. The fraction of
/// a second is dropped: no time MailMind writes has one.
pub fn fromisoformat(text: &str) -> Option<Dt> {
    let time = isoformat::read(text)?;
    let w = time.wall;
    let tz = match time.offset {
        None => Tz::Floating,
        Some(micros) => Tz::Fixed(micros.div_euclid(1_000_000)),
    };
    Dt::from_parts(i64::from(w.year), w.month, w.day, w.hour, w.minute, w.second, tz)
}

pub fn is_leap(year: i64) -> bool {
    year % 4 == 0 && (year % 100 != 0 || year % 400 == 0)
}

pub fn days_in_month(year: i64, month: u32) -> u32 {
    match month {
        2 if is_leap(year) => 29,
        2 => 28,
        4 | 6 | 9 | 11 => 30,
        _ => 31,
    }
}

/// Monday 0 to Sunday 6 of a day counted from 1970-01-01, a Thursday.
pub fn weekday(day: i64) -> usize {
    (day + 3).rem_euclid(7) as usize
}

/// `date.isoformat()` of a day counted from 1970-01-01.
pub fn date_iso(day: i64) -> String {
    let (year, month, date) = civil(day);
    format!("{year:04}-{month:02}-{date:02}")
}

fn clock(second: i64) -> String {
    format!("{:02}:{:02}:{:02}", second / 3600, second % 3600 / 60, second % 60)
}

/// An offset as `isoformat` writes it: `+HH:MM`, with `:SS` where it has seconds.
fn offset_text(offset: i64) -> String {
    let sign = if offset < 0 { '-' } else { '+' };
    let off = offset.abs();
    let mut out = format!("{sign}{:02}:{:02}", off / 3600, off % 3600 / 60);
    if off % 60 != 0 {
        out.push_str(&format!(":{:02}", off % 60));
    }
    out
}

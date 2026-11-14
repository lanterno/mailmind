//! A recurrence rule's occurrences, as the Python app expanded one (`dateutil.rrule`, through
//! `recurring_ical_events`), for the rules an invite says: a series by the day, the week, the month or the year.
//!
//! dateutil's walk is copied: a period at a time (a year, a month, a week from WKST, a day), each day of it kept or
//! dropped by the BY parts, BYSETPOS choosing among what is left, and every time of day BYHOUR, BYMINUTE and
//! BYSECOND give. Its defaults too: a yearly rule with no day parts falls on its start's month and day, a monthly one
//! on its start's day, a weekly one on its start's weekday; an nth weekday counts in a month or a year only; and the
//! start itself is no occurrence unless the rule gives it (`recurring_ical_events` adds it beside the rule).
//!
//! What it doesn't expand is not a rule here, and the agenda falls back to the event's first date for it, as the
//! Python app did for a rule its reader couldn't expand: anything finer than a day, a part it doesn't know, a value
//! that won't read, and dateutil's own BYEASTER, which no invite MailMind has seen says. A rule that
//! would walk for ever finding nothing, or give more times than any person could attend, stops at `Budget`'s count, as
//! the Python app's stopped at its time.

use std::cell::Cell;
use std::cmp::Ordering;
use std::collections::{BTreeMap, VecDeque};

use mm_kernel::when::{civil, days_from_civil};

use crate::invite::py_int;
use crate::time::{DAY, Dt, days_in_month, is_leap, weekday};

const WEEKDAY_CODES: [&str; 7] = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"];

/// How much a rule may look at before it is taken for one that runs for ever: each day of a period counts one, and
/// so does each time of day a kept day gives.
#[derive(Debug)]
pub struct Budget {
    days: u64,
    left: Cell<u64>,
}

impl Budget {
    pub fn new(days: u64) -> Budget {
        Budget { days, left: Cell::new(days) }
    }

    /// What has been looked at so far.
    pub fn used(&self) -> u64 {
        self.days - self.left.get()
    }

    /// `count` more looked at; false once there is too little left for them.
    fn spend(&self, count: u64) -> bool {
        let left = self.left.get();
        self.left.set(left.saturating_sub(count));
        left >= count
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Freq {
    Yearly,
    Monthly,
    Weekly,
    Daily,
}

/// A rule as dateutil holds it once read.
#[derive(Clone, Debug)]
pub struct Rule {
    freq: Freq,
    interval: i64,
    count: Option<u64>,
    until: Option<Dt>,
    wkst: usize,
    bymonth: Vec<u32>,
    bymonthday: Vec<i64>,
    bynmonthday: Vec<i64>,
    byyearday: Vec<i64>,
    byweekno: Vec<i64>,
    byweekday: Vec<usize>,
    bynweekday: Vec<(usize, i64)>,
    /// Seconds of the day, sorted.
    timeset: Vec<i64>,
    bysetpos: Vec<i64>,
}

/// `values` in order, each once.
fn sorted<T: Ord>(values: &mut Vec<T>) {
    values.sort_unstable();
    values.dedup();
}

fn ints(value: &str) -> Option<Vec<i64>> {
    value.split(',').map(py_int).collect()
}

/// A BYDAY item: its weekday and its nth, if it has one (`+1MO`, `-1SU`, `TU`).
fn by_day(item: &str) -> Option<(usize, Option<i64>)> {
    let code = item.get(item.len().checked_sub(2)?..)?;
    let day = WEEKDAY_CODES.iter().position(|c| *c == code)?;
    let nth = &item[..item.len() - 2];
    if nth.is_empty() {
        return Some((day, None));
    }
    let digits = nth.strip_prefix(['+', '-']).unwrap_or(nth);
    if digits.is_empty() || digits.len() > 2 || !digits.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    let n: i64 = nth.trim_start_matches('+').parse().ok()?;
    (n != 0).then_some((day, Some(n)))
}

impl Rule {
    /// The rule `kv` says (its parts upper case, `invite::rule_parts`), from `start`, bounded by `until`
    /// (`invite::until_bound`). None where it is no rule this expands.
    pub fn read(kv: &BTreeMap<String, String>, start: &Dt, until: Option<Dt>) -> Option<Rule> {
        let freq = match kv.get("FREQ").map(String::as_str) {
            Some("YEARLY") => Freq::Yearly,
            Some("MONTHLY") => Freq::Monthly,
            Some("WEEKLY") => Freq::Weekly,
            Some("DAILY") => Freq::Daily,
            _ => return None,
        };
        // Below 1, dateutil would stand still for ever.
        let interval = kv.get("INTERVAL").map_or(Some(1), |i| py_int(i))?.max(1);
        let count = kv.get("COUNT").filter(|c| !c.is_empty() && c.bytes().all(|b| b.is_ascii_digit()));
        let mut rule = Rule {
            freq,
            interval,
            count: match count {
                Some(count) => Some(count.parse().ok()?),
                None => None,
            },
            until,
            wkst: 0,
            bymonth: Vec::new(),
            bymonthday: Vec::new(),
            bynmonthday: Vec::new(),
            byyearday: Vec::new(),
            byweekno: Vec::new(),
            byweekday: Vec::new(),
            bynweekday: Vec::new(),
            timeset: Vec::new(),
            bysetpos: Vec::new(),
        };
        let (mut hours, mut minutes, mut seconds) = (None, None, None);
        let (mut said_day, mut said_monthday) = (false, false);
        for (key, value) in kv {
            match key.as_str() {
                "FREQ" | "INTERVAL" | "COUNT" | "UNTIL" => {}
                "WKST" => rule.wkst = WEEKDAY_CODES.iter().position(|c| c == value)?,
                "BYMONTH" => {
                    let months = ints(value)?;
                    rule.bymonth = months.into_iter().map(|m| u32::try_from(m).unwrap_or(0)).collect();
                }
                "BYMONTHDAY" => {
                    said_monthday = true;
                    let days = ints(value)?;
                    rule.bymonthday = days.iter().copied().filter(|d| *d > 0).collect();
                    rule.bynmonthday = days.into_iter().filter(|d| *d < 0).collect();
                }
                "BYYEARDAY" => {
                    said_day = true;
                    rule.byyearday = ints(value)?;
                }
                "BYWEEKNO" => {
                    said_day = true;
                    rule.byweekno = ints(value)?;
                }
                "BYDAY" => {
                    said_day = true;
                    for item in value.split(',') {
                        match by_day(item)? {
                            (day, Some(n)) if matches!(freq, Freq::Yearly | Freq::Monthly) => {
                                rule.bynweekday.push((day, n));
                            }
                            (day, _) => rule.byweekday.push(day),
                        }
                    }
                }
                "BYHOUR" => hours = Some(ints(value)?),
                "BYMINUTE" => minutes = Some(ints(value)?),
                "BYSECOND" => seconds = Some(ints(value)?),
                "BYSETPOS" => {
                    rule.bysetpos = ints(value)?;
                    if rule.bysetpos.iter().any(|p| *p == 0 || !(-366..=366).contains(p)) {
                        return None;
                    }
                }
                _ => return None,
            }
        }
        let (_, month, day) = start.date();
        if !said_day && !said_monthday {
            match freq {
                Freq::Yearly => {
                    if rule.bymonth.is_empty() {
                        rule.bymonth.push(month);
                    }
                    rule.bymonthday.push(i64::from(day));
                }
                Freq::Monthly => rule.bymonthday.push(i64::from(day)),
                Freq::Weekly => rule.byweekday.push(start.weekday()),
                Freq::Daily => {}
            }
        }
        // dateutil holds each part as a set: a stranger's list of the same day forty thousand times is one day.
        sorted(&mut rule.bymonth);
        sorted(&mut rule.bymonthday);
        sorted(&mut rule.bynmonthday);
        sorted(&mut rule.byyearday);
        sorted(&mut rule.byweekno);
        sorted(&mut rule.byweekday);
        sorted(&mut rule.bynweekday);
        sorted(&mut rule.bysetpos);
        let second = start.second_of_day();
        let mut hours = hours.unwrap_or_else(|| vec![second / 3600]);
        let mut minutes = minutes.unwrap_or_else(|| vec![second % 3600 / 60]);
        let mut seconds = seconds.unwrap_or_else(|| vec![second % 60]);
        sorted(&mut hours);
        sorted(&mut minutes);
        sorted(&mut seconds);
        if hours.iter().any(|h| !(0..24).contains(h))
            || minutes.iter().any(|m| !(0..60).contains(m))
            || seconds.iter().any(|s| !(0..60).contains(s))
        {
            return None;
        }
        for h in &hours {
            for m in &minutes {
                for s in &seconds {
                    rule.timeset.push(h * 3600 + m * 60 + s);
                }
            }
        }
        rule.timeset.sort_unstable();
        rule.timeset.dedup();
        Some(rule)
    }

    /// The bound UNTIL gives, inclusive.
    pub fn until(&self) -> Option<&Dt> {
        self.until.as_ref()
    }

    /// The occurrences from `start`, in order.
    pub fn expand<'a>(self, start: &Dt, budget: &'a Budget) -> Expansion<'a> {
        let (year, month, _) = start.date();
        let count = self.count;
        Expansion {
            rule: self,
            start: start.clone(),
            year,
            month,
            day: start.day(),
            days: VecDeque::new(),
            ready: VecDeque::new(),
            count,
            ended: false,
            done: false,
            budget,
        }
    }

    /// Whether `day` (days since 1970-01-01) stays, by the parts that look at a day alone, the nth weekdays of
    /// its period (`nth`) and the days its year's BYWEEKNO weeks hold (`weeks`).
    fn keeps(&self, day: i64, nth: &[i64], weeks: &Weeks) -> bool {
        let (year, month, mday) = civil(day);
        let in_month = i64::from(days_in_month(year, month));
        let mday = i64::from(mday);
        let yearday = day - days_from_civil(year, 1, 1) + 1;
        let yearlen = if is_leap(year) { 366 } else { 365 };
        (self.bymonth.is_empty() || self.bymonth.contains(&month))
            && (self.byweekno.is_empty() || weeks.holds(day))
            && (self.byweekday.is_empty() || self.byweekday.contains(&weekday(day)))
            && (self.bynweekday.is_empty() || nth.contains(&day))
            && ((self.bymonthday.is_empty() && self.bynmonthday.is_empty())
                || self.bymonthday.contains(&mday)
                || self.bynmonthday.contains(&(mday - in_month - 1)))
            && (self.byyearday.is_empty()
                || self.byyearday.contains(&yearday)
                || self.byyearday.contains(&(yearday - yearlen - 1)))
    }

    /// The days of `year` its BYWEEKNO weeks hold, marked as dateutil marks them (`_iterinfo.rebuild`): a week runs
    /// from WKST, week 1 is the first with four or more of the year's days, a week 1 that begins late in the year
    /// counts too, and so do the year's first days where they are the year before's last week and the rule names it.
    fn weeks(&self, year: i64) -> Weeks {
        let first = days_from_civil(year, 1, 1);
        let mut marked = vec![false; 380];
        if self.byweekno.is_empty() {
            return Weeks { first, marked };
        }
        let yearlen: i64 = if is_leap(year) { 366 } else { 365 };
        let wkst = self.wkst as i64;
        let yearweekday = weekday(first) as i64;
        let firstwkst = (7 - yearweekday + wkst).rem_euclid(7);
        let (no1wkst, wyearlen) = if firstwkst >= 4 {
            (0, yearlen + (yearweekday - wkst).rem_euclid(7))
        } else {
            (firstwkst, yearlen - firstwkst)
        };
        let numweeks = wyearlen / 7 + wyearlen % 7 / 4;
        let short = if no1wkst != firstwkst { 7 - firstwkst } else { 0 };
        let mut mark_week = |mut i: i64| {
            for _ in 0..7 {
                if let Some(day) = usize::try_from(i).ok().and_then(|i| marked.get_mut(i)) {
                    *day = true;
                }
                i += 1;
                if weekday(first + i) as i64 == wkst {
                    break;
                }
            }
        };
        for &n in &self.byweekno {
            let n = if n < 0 { n + numweeks + 1 } else { n };
            if !(0 < n && n <= numweeks) {
                continue;
            }
            mark_week(if n > 1 { no1wkst + (n - 1) * 7 - short } else { no1wkst });
        }
        if self.byweekno.contains(&1) {
            let i = no1wkst + numweeks * 7 - short;
            if i < yearlen {
                mark_week(i);
            }
        }
        if no1wkst != 0 {
            let lnumweeks = if self.byweekno.contains(&-1) {
                -1
            } else {
                let lyearweekday = weekday(days_from_civil(year - 1, 1, 1)) as i64;
                let lyearlen: i64 = if is_leap(year - 1) { 366 } else { 365 };
                // This year's length where it is the year before's that counts: as dateutil has it.
                if (7 - lyearweekday + wkst).rem_euclid(7) >= 4 {
                    52 + (lyearlen + (lyearweekday - wkst).rem_euclid(7)) % 7 / 4
                } else {
                    52 + (yearlen - no1wkst) % 7 / 4
                }
            };
            if self.byweekno.contains(&lnumweeks) {
                for day in marked.iter_mut().take(no1wkst as usize) {
                    *day = true;
                }
            }
        }
        Weeks { first, marked }
    }

    /// The days of a period that are some nth weekday the rule says: of each month BYMONTH names in a yearly rule's
    /// year, else of the whole year, or of a monthly rule's month.
    fn nth_days(&self, year: i64, month: u32) -> Vec<i64> {
        if self.bynweekday.is_empty() {
            return Vec::new();
        }
        let month_range = |m: u32| {
            let first = days_from_civil(year, m, 1);
            (first, first + i64::from(days_in_month(year, m)) - 1)
        };
        let ranges: Vec<(i64, i64)> = match self.freq {
            Freq::Yearly if self.bymonth.is_empty() => {
                vec![(days_from_civil(year, 1, 1), days_from_civil(year, 12, 31))]
            }
            Freq::Yearly => self.bymonth.iter().filter(|m| (1..=12).contains(*m)).map(|m| month_range(*m)).collect(),
            _ => vec![month_range(month)],
        };
        let mut out = Vec::new();
        for (first, last) in ranges {
            for &(wday, n) in &self.bynweekday {
                let at = if n < 0 {
                    let i = last + (n + 1) * 7;
                    i - (weekday(i) as i64 - wday as i64).rem_euclid(7)
                } else {
                    let i = first + (n - 1) * 7;
                    i + (7 - weekday(i) as i64 + wday as i64).rem_euclid(7)
                };
                if (first..=last).contains(&at) {
                    out.push(at);
                }
            }
        }
        out
    }
}

/// The days of a year BYWEEKNO keeps, from the year's first day: a week period that runs into the next year reads
/// the days past its end from the same marks, as dateutil's does.
#[derive(Debug)]
struct Weeks {
    first: i64,
    marked: Vec<bool>,
}

impl Weeks {
    fn holds(&self, day: i64) -> bool {
        usize::try_from(day - self.first).ok().and_then(|i| self.marked.get(i)).copied().unwrap_or(false)
    }
}

/// A rule's occurrences, a period at a time and a day of it at a time. `Err` once the budget is spent.
#[derive(Debug)]
pub struct Expansion<'a> {
    rule: Rule,
    start: Dt,
    year: i64,
    month: u32,
    /// The day the next week or day period begins on.
    day: i64,
    /// The kept days of the period it is at, still to give their times.
    days: VecDeque<i64>,
    ready: VecDeque<Dt>,
    count: Option<u64>,
    /// Past the last period there is.
    ended: bool,
    done: bool,
    budget: &'a Budget,
}

/// The budget ran out before the rule did.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Spent;

impl Expansion<'_> {
    /// The days of the period it is at.
    fn period(&self) -> Vec<i64> {
        match self.rule.freq {
            Freq::Yearly => {
                let first = days_from_civil(self.year, 1, 1);
                (first..first + if is_leap(self.year) { 366 } else { 365 }).collect()
            }
            Freq::Monthly => {
                let first = days_from_civil(self.year, self.month, 1);
                (first..first + i64::from(days_in_month(self.year, self.month))).collect()
            }
            Freq::Weekly => {
                let mut days = vec![self.day];
                while days.len() < 7 && weekday(self.day + days.len() as i64) != self.rule.wkst {
                    days.push(self.day + days.len() as i64);
                }
                days
            }
            Freq::Daily => vec![self.day],
        }
    }

    /// The next period's start; false past year 9999.
    fn advance(&mut self) -> bool {
        let interval = self.rule.interval;
        match self.rule.freq {
            Freq::Yearly => self.year = self.year.saturating_add(interval),
            Freq::Monthly => {
                let months = i64::from(self.month - 1).saturating_add(interval);
                self.year = self.year.saturating_add(months.div_euclid(12));
                self.month = months.rem_euclid(12) as u32 + 1;
            }
            Freq::Weekly => {
                let back = (weekday(self.day) as i64 - self.rule.wkst as i64).rem_euclid(7);
                self.day = self.day.saturating_sub(back).saturating_add(interval.saturating_mul(7));
                self.year = civil(self.day.min(days_from_civil(10_000, 1, 1))).0;
            }
            Freq::Daily => {
                self.day = self.day.saturating_add(interval);
                self.year = civil(self.day.min(days_from_civil(10_000, 1, 1))).0;
            }
        }
        self.year <= 9999
    }

    /// The days of the period it is at that the rule keeps, each to give its times; where BYSETPOS chooses among
    /// them, the occurrences it chooses.
    fn fill(&mut self) {
        let days = self.period();
        let nth = self.rule.nth_days(self.year, self.month);
        let weeks = self.rule.weeks(self.year);
        let kept: Vec<i64> = days.into_iter().filter(|day| self.rule.keeps(*day, &nth, &weeks)).collect();
        if self.rule.bysetpos.is_empty() {
            self.days = kept.into();
            return;
        }
        let times = &self.rule.timeset;
        let len = times.len() as i64;
        let mut chosen: Vec<(i64, i64)> = Vec::new();
        for &pos in &self.rule.bysetpos {
            let (day_at, time_at) =
                if pos < 0 { (pos.div_euclid(len), pos.rem_euclid(len)) } else { ((pos - 1) / len, (pos - 1) % len) };
            let day = if day_at < 0 { kept.len() as i64 + day_at } else { day_at };
            if let (Some(day), Some(second)) =
                (usize::try_from(day).ok().and_then(|d| kept.get(d)), times.get(time_at as usize))
            {
                chosen.push((*day, *second));
            }
        }
        sorted(&mut chosen);
        for (day, second) in chosen {
            self.offer(day, second);
            if self.done {
                return;
            }
        }
    }

    /// Every time of `day`, as dateutil yields them, until the rule ends.
    fn give(&mut self, day: i64) {
        for i in 0..self.rule.timeset.len() {
            self.offer(day, self.rule.timeset[i]);
            if self.done {
                return;
            }
        }
    }

    /// The occurrence at `second` of `day`, unless it comes before the start or the rule has ended by it.
    fn offer(&mut self, day: i64, second: i64) {
        let occurrence = Dt::new(day * DAY + second, self.start.tz.clone());
        if self.rule.until.as_ref().is_some_and(|until| occurrence.cmp_to(until) == Ordering::Greater) {
            self.done = true;
            return;
        }
        if occurrence.cmp_to(&self.start) != Ordering::Less {
            if let Some(count) = self.count.as_mut() {
                if *count == 0 {
                    self.done = true;
                    return;
                }
                *count -= 1;
            }
            self.ready.push_back(occurrence);
        }
    }
}

impl Iterator for Expansion<'_> {
    type Item = Result<Dt, Spent>;

    fn next(&mut self) -> Option<Self::Item> {
        loop {
            if let Some(next) = self.ready.pop_front() {
                return Some(Ok(next));
            }
            if self.done {
                return None;
            }
            if let Some(day) = self.days.pop_front() {
                if !self.budget.spend(self.rule.timeset.len() as u64) {
                    self.done = true;
                    return Some(Err(Spent));
                }
                self.give(day);
                continue;
            }
            if self.ended {
                self.done = true;
                continue;
            }
            let days = match self.rule.freq {
                Freq::Yearly => 366,
                Freq::Monthly => 31,
                Freq::Weekly => 7,
                Freq::Daily => 1,
            };
            if !self.budget.spend(days) {
                self.done = true;
                return Some(Err(Spent));
            }
            self.fill();
            if !self.advance() {
                self.ended = true;
            }
        }
    }
}

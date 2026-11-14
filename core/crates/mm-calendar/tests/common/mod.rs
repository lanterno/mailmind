//! What the calendar's tests share: the system's zone database, a person in a zone, a calendar of one event, a store
//! of invites held in memory, and a frozen clock. Every address is at example.test.

#![allow(dead_code, clippy::unwrap_used)]

use std::sync::{Arc, Mutex};

use mm_calendar::agenda::{Agenda, RULE_DAYS};
use mm_calendar::invite::{self, Invite};
use mm_calendar::ports::{FolderRoles, Here, InviteRow, StoredInvites, ZoneNames};
use mm_calendar::rrule::Budget;
use mm_calendar::time::{Dt, Tz, date_iso};
use mm_kernel::errors::Fault;
use mm_kernel::seams::{Clock, Moment};
use mm_kernel::zone::{Tzif, Utc, Zone};
use serde_json::Value;

/// The system's zone files, as `ZoneInfo` reads them.
#[derive(Debug)]
pub struct Zones;

impl ZoneNames for Zones {
    fn zone(&self, name: &str) -> Option<Arc<dyn Zone>> {
        if name.is_empty() || name.starts_with('/') || name.split('/').any(|p| matches!(p, "" | "." | "..")) {
            return None;
        }
        let bytes = std::fs::read(format!("/usr/share/zoneinfo/{name}")).ok()?;
        Some(Arc::new(Tzif::parse(&bytes)?))
    }
}

/// A person in the zone `name`, as `TZ` would name it.
pub fn person(name: &str) -> Arc<dyn Zone> {
    if name == "UTC" { Arc::new(Utc) } else { Zones.zone(name).unwrap() }
}

/// The zones the tests that read a time on this machine's clock run in: the suite's own UTC can't tell UTC from
/// local time.
pub const MACHINE_ZONES: [&str; 3] = ["UTC", "Asia/Tokyo", "America/New_York"];

pub fn here(person: &dyn Zone) -> Here<'_> {
    Here { zones: &Zones, person }
}

/// A floating time: the person's wall clock.
pub fn at(year: i64, month: u32, day: u32, hour: u32, minute: u32) -> Dt {
    Dt::from_parts(year, month, day, hour, minute, 0, Tz::Floating).unwrap()
}

/// A time in the zone `name` names.
pub fn zoned(year: i64, month: u32, day: u32, hour: u32, minute: u32, name: &str) -> Dt {
    let tz = invite::zone(name, &Zones).unwrap();
    Dt::from_parts(year, month, day, hour, minute, 0, tz).unwrap()
}

/// A time to the minute, with its offset where it has one: `isoformat(timespec="minutes")`.
pub fn minutes(at: &Dt) -> String {
    let iso = at.isoformat();
    format!("{}{}", &iso[..16], &iso[19..])
}

/// One event, UID and SUMMARY first so a line can say them again.
pub fn cal(lines: &[&str], method: &str) -> String {
    let mut all = vec!["BEGIN:VCALENDAR", "VERSION:2.0"];
    let said = format!("METHOD:{method}");
    if !method.is_empty() {
        all.push(&said);
    }
    all.extend(["BEGIN:VEVENT", "UID:u1@example.test", "SUMMARY:Studio sync"]);
    all.extend(lines);
    all.extend(["END:VEVENT", "END:VCALENDAR", ""]);
    all.join("\r\n")
}

/// The card's event of a text, in UTC.
pub fn parse(text: &str) -> Option<Invite> {
    invite::parse(text, here(&Utc))
}

pub fn parse_in(text: &str, person: &dyn Zone) -> Option<Invite> {
    invite::parse(text, here(person))
}

/// The next occurrence of `inv` that hasn't ended by `after`, with a budget a rule never runs out of here.
pub fn next(inv: &Invite, after: &Dt, person: &dyn Zone) -> Option<(Dt, Dt)> {
    mm_calendar::agenda::next_occurrence(inv, after, here(person), &Budget::new(RULE_DAYS))
}

/// The first `count` occurrences of a series, each found by asking what comes once the last has ended, from
/// 2026-09-01 12:00 on the person's clock.
pub fn walk(inv: &Invite, count: usize, person: &dyn Zone) -> Vec<(Dt, Dt)> {
    let mut found = Vec::new();
    let mut after = at(2026, 9, 1, 12, 0);
    while found.len() < count {
        let Some(occ) = next(inv, &after, person) else { break };
        after = occ.1.clone();
        found.push(occ);
    }
    found
}

/// The dates of the first `count` occurrences of the event these lines say, in UTC.
pub fn days(lines: &[&str], count: usize) -> Vec<String> {
    days_in(lines, count, &Utc)
}

pub fn days_in(lines: &[&str], count: usize, person: &dyn Zone) -> Vec<String> {
    let inv = parse_in(&cal(lines, "REQUEST"), person).unwrap();
    walk(&inv, count, person).iter().map(|(start, _)| date_iso(start.day())).collect()
}

/// A message an invite came in.
pub fn row(pk: i64) -> InviteRow {
    InviteRow {
        pk,
        key: Some(pk),
        uid: Some(pk),
        account: "ada@example.test".into(),
        folder: "INBOX".into(),
        thread_id: Some(format!("t{pk}")),
    }
}

/// Invite parts held in memory, newest first, as the store gives them.
#[derive(Debug, Default)]
pub struct Stored {
    /// (its row, when it came in seconds since the epoch, the folder's role, its text)
    pub parts: Mutex<Vec<(InviteRow, i64, String)>>,
    pub sent: Mutex<Vec<String>>,
}

impl Stored {
    /// One message, which came in 2026-09-21: within any window these tests ask about.
    pub fn one(text: &str) -> Stored {
        let stored = Stored::default();
        stored.add(row(1), 1_790_000_000, text);
        stored
    }

    /// A message, which came in at `ts`.
    pub fn add(&self, row: InviteRow, ts: i64, text: &str) {
        let mut parts = self.parts.lock().unwrap();
        parts.push((row, ts, text.to_owned()));
        parts.sort_by(|a, b| b.1.cmp(&a.1).then(b.0.pk.cmp(&a.0.pk)));
    }
}

impl StoredInvites for Stored {
    fn invite_parts(&self, since: i64, skipped: &dyn Fn(&str) -> bool) -> Result<Vec<(InviteRow, String)>, Fault> {
        Ok(self
            .parts
            .lock()
            .unwrap()
            .iter()
            .filter(|(row, ts, _)| *ts >= since && !skipped(&row.folder))
            .map(|(row, _, text)| (row.clone(), text.clone()))
            .collect())
    }

    fn keys_sent(&self, prefix: &str) -> Result<Vec<String>, Fault> {
        Ok(self.sent.lock().unwrap().iter().rev().filter(|k| k.starts_with(prefix)).cloned().collect())
    }
}

/// A folder's role by its name alone, as far as these tests name folders.
#[derive(Debug)]
pub struct Roles;

impl FolderRoles for Roles {
    fn role_by_name(&self, folder: &str) -> String {
        match folder.to_lowercase().as_str() {
            "inbox" => "inbox",
            "trash" | "deleted items" => "trash",
            "spam" | "junk" => "junk",
            _ => "custom",
        }
        .to_owned()
    }
}

/// A clock stopped at a moment.
#[derive(Debug)]
pub struct Frozen(pub i64);

impl Clock for Frozen {
    fn now(&self) -> Moment {
        Moment::from_micros(self.0 * 1_000_000)
    }
}

/// The moment the person's wall clock says `wall`.
pub fn moment(wall: &Dt, person: &dyn Zone) -> i64 {
    wall.moment(person)
}

/// Today & next as of `now` (the person's wall clock), over `days` of mail.
pub fn upcoming(stored: Arc<Stored>, now: &Dt, days: i64, person: Arc<dyn Zone>) -> Vec<Value> {
    let seconds = moment(now, &*person);
    let agenda = Agenda::new(stored, Arc::new(Roles), Arc::new(Zones), Arc::new(Frozen(seconds)), person);
    agenda.soonest(seconds, 5, days).unwrap()
}

/// What Today & next lists of one text, as "summary start to end", on 2026-11-01 at noon in UTC.
pub fn listed(text: &str) -> Vec<String> {
    listed_at(text, &at(2026, 11, 1, 12, 0), &(Arc::new(Utc) as Arc<dyn Zone>))
}

pub fn listed_at(text: &str, now: &Dt, person: &Arc<dyn Zone>) -> Vec<String> {
    upcoming(Arc::new(Stored::one(text)), now, 4000, person.clone())
        .iter()
        .map(|x| {
            format!(
                "{} {} to {}",
                x["summary"].as_str().unwrap(),
                x["start"].as_str().unwrap(),
                x["end"].as_str().unwrap()
            )
        })
        .collect()
}

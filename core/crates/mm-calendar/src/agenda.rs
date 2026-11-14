//! Today & next: which invites make the list, and when (`mailmind.calendar.domain.agenda`, `application.agenda` and
//! the expansion of `adapters.icalendar_acl`). What a message says about an event, as the reader parsed it: an
//! update replaces the original, a cancellation removes it, and a message about one occurrence of a series
//! (RECURRENCE-ID) moves or cancels just that one, or with RANGE=THISANDFUTURE, that one and every one after it.
//!
//! It reads only stored bodies, never IMAP.

use std::cmp::Ordering;
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::iter::Peekable;
use std::sync::Arc;

use mm_kernel::errors::Fault;
use mm_kernel::seams::Clock;
use mm_kernel::zone::Zone;
use serde_json::{Value, json};

use crate::invite::{self, Invite, Span};
use crate::ports::{FolderRoles, Here, InviteRow, StoredInvites, ZoneNames};
use crate::rrule::{Budget, Expansion, Rule, Spent};
use crate::time::{DAY, Dt, Tz, fromisoformat};

/// The folders whose mail lists no event.
pub const SKIPPED_ROLES: [&str; 2] = ["trash", "junk"];
/// Days a rule may look at to find its next occurrence, as the Python app gave one 0.1 s of its time.
pub const RULE_DAYS: u64 = 400_000;
/// Days the rules of one list may look at, as the Python app gave a list 1 s: past it, a series shows its first date.
const LIST_DAYS: u64 = 4_000_000;

/// What makes two invites say the same event: its UID, or its title and start where it has none, and the
/// occurrence a RECURRENCE-ID names.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub enum EventKey {
    Uid(String, String),
    Untitled(String, String, String),
}

pub fn event_key(inv: &Invite) -> EventKey {
    if inv.uid.is_empty() {
        EventKey::Untitled(inv.summary.clone(), inv.start_iso.clone(), inv.recurrence_id.clone())
    } else {
        EventKey::Uid(inv.uid.clone(), inv.recurrence_id.clone())
    }
}

/// Whether what an older message says (`inv`) still beats what a newer one does: only with the later of the
/// organizer's revisions. A message can come late, and a stale copy be sent again.
pub fn supersedes(inv: &Invite, held: &Invite) -> bool {
    inv.sequence > held.sequence
}

pub fn dead(inv: &Invite) -> bool {
    inv.method == "CANCEL" || inv.status == "CANCELLED"
}

/// The event as though it happened only once, on the date it starts.
pub fn first_date(inv: &Invite) -> Invite {
    Invite { rrule: String::new(), rdates: Vec::new(), ..inv.clone() }
}

/// Per series: its rescheduled or cancelled occurrences, and the series that are gone.
pub fn changed_occurrences<'a>(
    invites: impl IntoIterator<Item = &'a Invite>,
) -> (HashMap<String, Vec<String>>, BTreeSet<String>) {
    let mut moved: HashMap<String, Vec<String>> = HashMap::new();
    let mut gone = BTreeSet::new();
    for inv in invites {
        if !inv.uid.is_empty() && !inv.recurrence_id.is_empty() {
            // One with RANGE=THISANDFUTURE is `cut_up`'s.
            if !inv.this_and_future {
                moved.entry(inv.uid.clone()).or_default().push(inv.recurrence_id.clone());
            }
        } else if !inv.uid.is_empty() && dead(inv) {
            gone.insert(inv.uid.clone());
        }
    }
    (moved, gone)
}

/// An occurrence a RECURRENCE-ID names, on the person's wall clock: None for one that won't read or that their zone
/// pushes past Python's years.
fn occurrence(iso: &str, person: &dyn Zone) -> Option<Dt> {
    fromisoformat(iso)?.local_naive(person)
}

/// `found`, with each series a RANGE=THISANDFUTURE override changed cut where it did: the series stops before the
/// first such occurrence, and each override goes on as the series from there to the next, in its own words, moved as
/// far as it moved. One with no live series beside it stays what it is, a word about one date.
pub fn cut_up<T: Clone>(found: &[(T, Invite)], person: &dyn Zone) -> Vec<(T, Invite)> {
    let mut series: HashMap<&str, &Invite> = HashMap::new();
    for (_, inv) in found {
        if !inv.uid.is_empty() && inv.recurrence_id.is_empty() && inv.repeats() && !dead(inv) {
            series.insert(&inv.uid, inv);
        }
    }
    let mut said_at: BTreeMap<&str, Vec<(i64, &str)>> = BTreeMap::new();
    for (_, inv) in found {
        if inv.this_and_future
            && series.contains_key(inv.uid.as_str())
            && let Some(at) = occurrence(&inv.recurrence_id, person)
        {
            said_at.entry(&inv.uid).or_default().push((at.wall, &inv.recurrence_id));
        }
    }
    let cuts: HashMap<&str, Vec<&str>> = said_at
        .into_iter()
        .map(|(uid, mut each)| {
            each.sort();
            (uid, each.into_iter().map(|(_, said)| said).collect())
        })
        .collect();
    let mut out = Vec::with_capacity(found.len());
    for (t, inv) in found {
        let said = inv.recurrence_id.as_str();
        let Some(cut) = cuts.get(inv.uid.as_str()).filter(|cut| said.is_empty() || cut.contains(&said)) else {
            out.push((t.clone(), inv.clone()));
            continue;
        };
        if said.is_empty() {
            let span = Span { before: cut[0].to_owned(), ..Span::default() };
            out.push((t.clone(), Invite { span: Some(span), ..inv.clone() }));
            continue;
        }
        let whole = series[inv.uid.as_str()];
        let at = cut.iter().position(|c| *c == said).unwrap_or_default();
        let span = Span {
            origin: whole.start_iso.clone(),
            since: said.to_owned(),
            before: cut.get(at + 1).map(|c| (*c).to_owned()).unwrap_or_default(),
        };
        out.push((
            t.clone(),
            Invite {
                rrule: whole.rrule.clone(),
                rdates: whole.rdates.clone(),
                exdates: whole.exdates.clone(),
                recurrence: whole.recurrence.clone(),
                tzid: whole.tzid.clone(),
                recurrence_id: String::new(),
                span: Some(span),
                ..inv.clone()
            },
        ));
    }
    out
}

/// A time an invite gives, back in the zone its TZID names: a change of clocks moves the wall clock.
fn in_its_zone(iso: &str, tzid: &str, here: Here<'_>) -> Option<Dt> {
    let at = fromisoformat(iso)?;
    if !at.is_aware() {
        return Some(at);
    }
    match invite::zone(tzid, here.zones) {
        Some(tz) => at.astimezone(&tz, here.person),
        None => Some(at),
    }
}

/// How far an occurrence moved, in seconds. Where only one of them floats, by the wall clock, as `length` reads a
/// duration.
fn moved(was: &Dt, to: &Dt) -> i64 {
    if was.is_aware() == to.is_aware() { to.minus(was) } else { to.minus(&was.with_tz(to.tz.clone())) }
}

/// (start, end) of the first occurrence of an invite that hasn't ended by `after` (the person's wall clock), in the
/// invite's own terms: aware in its zone, or floating. A one-off event is its only occurrence. None when every
/// occurrence has ended, or for a rule this doesn't expand: one it can't read, finer than a day, or one that runs
/// past `budget`.
pub fn next_occurrence(inv: &Invite, after: &Dt, here: Here<'_>, budget: &Budget) -> Option<(Dt, Dt)> {
    let local = |d: &Dt| d.local_naive(here.person);
    let start = in_its_zone(&inv.start_iso, &inv.tzid, here)?;
    let end = if inv.end_iso.is_empty() { None } else { Some(fromisoformat(&inv.end_iso)?) };
    let dur = invite::length(&start, end.as_ref(), inv.all_day);
    let span = inv.span.clone().unwrap_or_default();
    let origin = if span.origin.is_empty() { start.clone() } else { in_its_zone(&span.origin, &inv.tzid, here)? };
    let shift =
        if !span.origin.is_empty() && !span.since.is_empty() { moved(&fromisoformat(&span.since)?, &start) } else { 0 };
    let first = if span.since.is_empty() { None } else { Some(local(&fromisoformat(&span.since)?)?) };
    let stop = if span.before.is_empty() { None } else { Some(local(&fromisoformat(&span.before)?)?) };
    if let Some(stop) = &stop
        && local(&origin)?.wall >= stop.wall
    {
        return None;
    }
    let now = local(after)?;
    let kv = invite::rule_parts(&inv.rrule);
    if kv.is_empty() && inv.rdates.is_empty() {
        let end = start.plus(dur)?;
        return (local(&end)?.wall > now.wall).then_some((start, end));
    }
    let whole_days: BTreeSet<&str> = inv.exdates.iter().filter(|x| x.len() == 10).map(String::as_str).collect();
    let mut struck = BTreeSet::new();
    for x in &inv.exdates {
        if let Some(at) = fromisoformat(x) {
            struck.insert(local(&at)?.wall);
        }
    }
    let struck_days: BTreeSet<i64> = struck.iter().map(|wall| wall.div_euclid(DAY)).collect();
    let since = if origin.is_aware() { after.astimezone(&origin.tz, here.person)? } else { now.clone() };
    // A day over: at a change of clocks, an hour may be missing from `dur`.
    let since =
        dur.checked_add(shift).and_then(|d| d.checked_add(DAY)).and_then(|d| since.plus(-d)).unwrap_or(origin.clone());
    for s in occurrences(inv, &origin, &kv, &since, here, budget)? {
        let s = s?;
        let said = local(&s)?;
        if stop.as_ref().is_some_and(|stop| said.wall >= stop.wall) {
            return None;
        }
        if first.as_ref().is_some_and(|first| said.wall < first.wall)
            || struck.contains(&said.wall)
            || whole_days.contains(crate::time::date_iso(s.day()).as_str())
            || (inv.all_day && struck_days.contains(&s.day()))
        {
            continue;
        }
        let moved_to = s.plus(shift)?;
        let ends = moved_to.plus(dur)?;
        if local(&ends)?.wall > now.wall {
            return Some((moved_to, ends));
        }
    }
    None
}

/// The series' occurrences from `since` on, in order: its start and its RDATEs beside what its rule gives, as
/// `recurring_ical_events` sets them. None for a rule this doesn't expand; an `Err` once the budget is spent.
fn occurrences<'a>(
    inv: &Invite,
    origin: &Dt,
    kv: &BTreeMap<String, String>,
    since: &Dt,
    here: Here<'_>,
    budget: &'a Budget,
) -> Option<impl Iterator<Item = Option<Dt>> + 'a> {
    let midnight = |d: &Dt| Dt::floating(d.day() * DAY);
    let mut start = if inv.all_day { midnight(origin) } else { origin.clone() };
    let mut dates = Vec::with_capacity(inv.rdates.len() + 1);
    for d in &inv.rdates {
        let rdate = fromisoformat(d)?;
        dates.push(if inv.all_day { midnight(&rdate) } else { rdate });
    }
    // One zone for them all: the start's, or the first RDATE's that has one.
    let tz = if start.is_aware() {
        Some(start.tz.clone())
    } else {
        dates.iter().find(|d| d.is_aware()).map(|d| d.tz.clone())
    };
    let mut since = since.clone();
    if let Some(tz) = &tz {
        for d in std::iter::once(&mut start).chain(dates.iter_mut()).chain(std::iter::once(&mut since)) {
            if !d.is_aware() {
                *d = d.with_tz(tz.clone());
            }
        }
    }
    let rule = if kv.is_empty() {
        None
    } else {
        let until = invite::until_bound(kv, origin, here).ok()?;
        // A floating bound on a series an RDATE made aware: recurring_ical_events reads it as UTC's.
        let until = until.map(|u| if start.is_aware() && !u.is_aware() { u.with_tz(Tz::Fixed(0)) } else { u });
        Some(Rule::read(kv, &start, until)?)
    };
    // The start is an occurrence unless the rule ended before it.
    let until = rule.as_ref().and_then(|r| r.until());
    if until.is_none_or(|until| start.cmp_to(until) != Ordering::Greater) {
        dates.push(start.clone());
    }
    dates.sort_by(Dt::cmp_to);
    dates.dedup_by(|a, b| a.cmp_to(b) == Ordering::Equal);
    let merged = Merged {
        dates: dates.into_iter().peekable(),
        rule: rule.map(|r| r.expand(&start, budget).peekable()),
        last: None,
    };
    Some(merged.filter(move |d| d.as_ref().is_none_or(|d| d.cmp_to(&since) != Ordering::Less)))
}

/// The dates beside a rule and the rule's own, merged in order, each once. `None` once the rule's budget is spent.
struct Merged<'a> {
    dates: Peekable<std::vec::IntoIter<Dt>>,
    rule: Option<Peekable<Expansion<'a>>>,
    last: Option<Dt>,
}

impl Iterator for Merged<'_> {
    type Item = Option<Dt>;

    fn next(&mut self) -> Option<Option<Dt>> {
        loop {
            let from_rule = match self.rule.as_mut().and_then(Peekable::peek) {
                Some(Err(Spent)) => return Some(None),
                Some(Ok(at)) => Some(at),
                None => None,
            };
            let take_rule = match (self.dates.peek(), from_rule) {
                (None, None) => return None,
                (Some(_), None) => false,
                (None, Some(_)) => true,
                (Some(date), Some(at)) => at.cmp_to(date) == Ordering::Less,
            };
            let next = if take_rule { self.rule.as_mut()?.next()?.ok()? } else { self.dates.next()? };
            if self.last.as_ref().is_some_and(|last| last.cmp_to(&next) == Ordering::Equal) {
                continue;
            }
            self.last = Some(next.clone());
            return Some(Some(next));
        }
    }
}

/// The mail an event came in, as Today & next shows it.
fn item(row: &InviteRow, inv: &Invite, start: &Dt, end: &Dt) -> Value {
    let organizer = inv.organizer.clone().unwrap_or_default();
    let organizer = if organizer.name.is_empty() { organizer.email } else { organizer.name };
    json!({
        "key": row.key, "folder": row.folder, "uid": row.uid, "account": row.account, "thread_id": row.thread_id,
        "summary": inv.summary, "location": inv.location,
        "start": start.minutes_iso(), "end": end.minutes_iso(),
        "all_day": inv.all_day, "organizer": organizer,
        "invite": inv.method == "REQUEST", "recurrence": inv.recurrence,
    })
}

/// Today & next.
#[derive(Debug)]
pub struct Agenda {
    stored: Arc<dyn StoredInvites>,
    roles: Arc<dyn FolderRoles>,
    zones: Arc<dyn ZoneNames>,
    clock: Arc<dyn Clock>,
    person: Arc<dyn Zone>,
}

impl Agenda {
    pub fn new(
        stored: Arc<dyn StoredInvites>,
        roles: Arc<dyn FolderRoles>,
        zones: Arc<dyn ZoneNames>,
        clock: Arc<dyn Clock>,
        person: Arc<dyn Zone>,
    ) -> Agenda {
        Agenda { stored, roles, zones, clock, person }
    }

    /// `GET /api/upcoming`: the next five.
    pub fn upcoming(&self) -> Result<Value, Fault> {
        let now = self.clock.now().micros().div_euclid(1_000_000);
        Ok(json!({"ok": true, "items": self.soonest(now, 5, 60)?}))
    }

    /// The events in invites from the last `days` of mail that haven't ended by `now` (seconds since the epoch),
    /// soonest first, at most `limit`; a repeating one at its next occurrence. One row per event, from the message
    /// with its latest SEQUENCE, the newest of those that have it. Times are the person's wall clock, to the minute.
    pub fn soonest(&self, now: i64, limit: usize, days: i64) -> Result<Vec<Value>, Fault> {
        let here = Here { zones: &*self.zones, person: &*self.person };
        let after = Dt::floating(mm_kernel::zone::local(&*self.person, now));
        let skipped = |folder: &str| SKIPPED_ROLES.contains(&self.roles.role_by_name(folder).as_str());
        let mut seen: HashMap<EventKey, usize> = HashMap::new();
        let mut found: Vec<(InviteRow, Invite)> = Vec::new();
        for (row, text) in self.stored.invite_parts(now - days * DAY, &skipped)? {
            for invite in invite::events(&text, here) {
                if invite.start_iso.is_empty() {
                    continue;
                }
                let key = event_key(&invite);
                match seen.get(&key) {
                    None => {
                        seen.insert(key, found.len());
                        found.push((row.clone(), invite));
                    }
                    Some(&held) => {
                        if supersedes(&invite, &found[held].1) {
                            found[held] = (row.clone(), invite);
                        }
                    }
                }
            }
        }
        let (moved, gone) = changed_occurrences(found.iter().map(|(_, inv)| inv));
        let mut items: Vec<(String, Value)> = Vec::new();
        let mut spent = 0;
        for (row, mut inv) in cut_up(&found, &*self.person) {
            if dead(&inv) || (!inv.recurrence_id.is_empty() && gone.contains(&inv.uid)) {
                continue;
            }
            if inv.repeats() && inv.recurrence_id.is_empty() {
                inv.exdates.extend(moved.get(&inv.uid).into_iter().flatten().cloned());
            }
            let over = inv.repeats() && spent > LIST_DAYS;
            let budget = Budget::new(RULE_DAYS);
            let mut occ = if over { None } else { next_occurrence(&inv, &after, here, &budget) };
            spent += budget.used();
            if occ.is_none() && inv.repeats() {
                // A rule this can't expand, or isn't asked to: its first date.
                occ = next_occurrence(&first_date(&inv), &after, here, &budget);
            }
            let Some((start, end)) = occ else { continue };
            let (Some(start), Some(end)) = (start.local_naive(here.person), end.local_naive(here.person)) else {
                continue;
            };
            items.push((start.minutes_iso(), item(&row, &inv, &start, &end)));
        }
        items.sort_by(|a, b| a.0.cmp(&b.0));
        Ok(items.into_iter().take(limit).map(|(_, item)| item).collect())
    }
}

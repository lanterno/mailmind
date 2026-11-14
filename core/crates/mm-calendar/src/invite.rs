//! An invite as a reader of its text gives it, which Today & next and an answer read and a message's card shows
//! (`mailmind.calendar.domain.invite`, and the reading of `adapters.icalendar_acl`).
//!
//! What a TZID means is one rule, `zone`: a name the zone database holds, or Windows' name for one. Any other makes
//! the time floating, whatever the invite's own VTIMEZONE says. A RRULE, a UID, an ORGANIZER and an ATTENDEE are kept
//! as said, their escapes and all: an answer echoes a UID and writes to an organizer, and a stranger's `\n` there
//! would be a line of their own in it.

use std::collections::BTreeMap;
use std::sync::Arc;

use serde_json::{Map, Value, json};

use crate::ics::{self, Component, Prop};
use crate::ports::{Here, ZoneNames};
use crate::time::{DAY, Dt, Named, Tz, date_iso};
use crate::windows_zones;

/// Characters of a text past which it isn't read.
pub const MAX_TEXT: usize = 1_000_000;
/// Events read of one text: Today & next reads all of them on every request.
pub const MAX_EVENTS: usize = 200;
/// A revision count; what is said past one is no count at all.
const MAX_SEQUENCE: i64 = 1 << 31;

/// An organizer or an attendee.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Party {
    pub name: String,
    pub email: String,
}

/// The part of a series an invite stands for, where a RANGE=THISANDFUTURE override cut the series up
/// (`agenda::cut_up`). Never from a text. Each is an ISO time, an occurrence's as the series' rule puts it.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Span {
    /// Where the rule starts counting from, when that isn't the invite's own start.
    pub origin: String,
    /// The first occurrence that is this part's.
    pub since: String,
    /// The first that isn't any more.
    pub before: String,
}

/// An event, as a card shows it and Today & next reads it.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Invite {
    pub summary: String,
    pub location: String,
    pub description: String,
    pub when: String,
    pub all_day: bool,
    pub start_iso: String,
    pub end_iso: String,
    /// None where the invite names none.
    pub organizer: Option<Party>,
    pub attendees: Vec<Party>,
    pub recurrence: String,
    pub method: String,
    pub status: String,
    pub uid: String,
    pub rrule: String,
    /// A day struck from a timed series is a date (`YYYY-MM-DD`), anything else a time.
    pub exdates: Vec<String>,
    pub recurrence_id: String,
    pub tzid: String,
    /// The organizer's count of its revisions.
    pub sequence: i64,
    pub rdates: Vec<String>,
    /// Said with RANGE=THISANDFUTURE: what it says goes for each occurrence from this one on.
    pub this_and_future: bool,
    pub span: Option<Span>,
}

impl Invite {
    /// The card's invite: the Python app's dict, its keys in its order.
    pub fn to_json(&self) -> Value {
        let party = |p: &Party| json!({"name": p.name, "email": p.email});
        let mut out = Map::new();
        out.insert("summary".into(), json!(self.summary));
        out.insert("location".into(), json!(self.location));
        out.insert("description".into(), json!(self.description));
        out.insert("when".into(), json!(self.when));
        out.insert("all_day".into(), json!(self.all_day));
        out.insert("start_iso".into(), json!(self.start_iso));
        out.insert("end_iso".into(), json!(self.end_iso));
        out.insert("organizer".into(), self.organizer.as_ref().map_or_else(|| json!({}), party));
        out.insert("attendees".into(), Value::Array(self.attendees.iter().map(party).collect()));
        out.insert("recurrence".into(), json!(self.recurrence));
        out.insert("method".into(), json!(self.method));
        out.insert("status".into(), json!(self.status));
        out.insert("uid".into(), json!(self.uid));
        out.insert("rrule".into(), json!(self.rrule));
        out.insert("exdates".into(), json!(self.exdates));
        out.insert("recurrence_id".into(), json!(self.recurrence_id));
        out.insert("tzid".into(), json!(self.tzid));
        out.insert("sequence".into(), json!(self.sequence));
        if self.this_and_future {
            out.insert("this_and_future".into(), json!(true));
        }
        if !self.rdates.is_empty() {
            out.insert("rdates".into(), json!(self.rdates));
        }
        Value::Object(out)
    }

    /// Whether it repeats: a rule, or dates of its own.
    pub fn repeats(&self) -> bool {
        !self.rrule.is_empty() || !self.rdates.is_empty()
    }
}

/// Every event the text holds, up to `MAX_EVENTS`: none for a text that isn't a calendar, or is too long to read.
pub fn events(text: &str, here: Here<'_>) -> Vec<Invite> {
    if text.len() > MAX_TEXT && text.chars().count() > MAX_TEXT {
        return Vec::new();
    }
    let Ok(tops) = ics::read(text) else { return Vec::new() };
    let mut found = Vec::new();
    for top in &tops {
        let method = top.last("METHOD").map(|m| ics::py_strip(&m.value).to_uppercase()).unwrap_or_default();
        let mut each = Vec::new();
        top.walk("VEVENT", &mut each);
        for event in each {
            if found.len() == MAX_EVENTS {
                return found;
            }
            found.push(invite_of(&fields(event, here), &method, here));
        }
    }
    found
}

/// The event a card shows: the series itself where the text holds one, else its first.
pub fn parse(text: &str, here: Here<'_>) -> Option<Invite> {
    let mut found = events(text, here);
    let series = found.iter().position(|e| e.recurrence_id.is_empty()).unwrap_or(0);
    (!found.is_empty()).then(|| found.swap_remove(series))
}

/// A time, or a whole day, as an EXDATE or an RDATE says it.
#[derive(Clone, Debug)]
enum Moment {
    At(Dt),
    /// Days since 1970-01-01.
    Day(i64),
}

impl Moment {
    fn isoformat(&self) -> String {
        match self {
            Moment::At(at) => at.isoformat(),
            Moment::Day(day) => date_iso(*day),
        }
    }

    fn midnight(self) -> Dt {
        match self {
            Moment::At(at) => at,
            Moment::Day(day) => Dt::floating(day * DAY),
        }
    }
}

/// What the reader found in one event, each as it was said. A time is aware, or floating where the invite floats.
#[derive(Clone, Debug, Default)]
struct Fields {
    summary: Option<String>,
    location: Option<String>,
    description: Option<String>,
    uid: Option<String>,
    status: Option<String>,
    rrule: Option<String>,
    start: Option<Dt>,
    end: Option<Dt>,
    all_day: bool,
    tzid: String,
    organizer: Option<Party>,
    attendees: Vec<Party>,
    exdates: Vec<Moment>,
    rdates: Vec<Dt>,
    recurrence_id: Option<Dt>,
    this_and_future: bool,
    sequence: i64,
}

fn fields(event: &Component, here: Here<'_>) -> Fields {
    let text = |name: &str| event.last(name).map(|p| ics::py_strip(&p.value).to_owned());
    let said = |name: &str| event.last(name).map(|p| ics::py_strip(&p.raw).to_owned());
    let mut e = Fields {
        attendees: event.each("ATTENDEE").map(|a| party(&a.raw, &a.param("CN"))).collect(),
        summary: text("SUMMARY"),
        location: text("LOCATION"),
        description: text("DESCRIPTION"),
        uid: said("UID"),
        status: text("STATUS").map(|s| s.to_uppercase()),
        rrule: said("RRULE"),
        ..Fields::default()
    };
    if let Some(dtstart) = event.last("DTSTART") {
        let (start, all_day) = prop_time(dtstart, here);
        e.all_day = all_day;
        e.tzid = if start.as_ref().is_some_and(Dt::is_aware) { dtstart.param("TZID") } else { String::new() };
        e.start = start;
    }
    if let Some(dtend) = event.last("DTEND") {
        e.end = prop_time(dtend, here).0;
    } else if let (Some(start), Some(duration)) = (&e.start, event.last("DURATION").and_then(|d| duration(&d.value))) {
        e.end = start.plus(duration);
    }
    if let Some(organizer) = event.last("ORGANIZER") {
        e.organizer = Some(party(&organizer.raw, &organizer.param("CN")));
    }
    if let Some(recurrence_id) = event.last("RECURRENCE-ID") {
        e.recurrence_id = prop_time(recurrence_id, here).0;
        e.this_and_future = recurrence_id.param("RANGE").to_uppercase() == "THISANDFUTURE";
    }
    if let Some(sequence) = event.last("SEQUENCE") {
        e.sequence = py_int(ics::py_strip(&sequence.value)).filter(|s| (0..MAX_SEQUENCE).contains(s)).unwrap_or(0);
    }
    // A whole day struck from a timed series stays a date, which says so; from an all-day one it's that midnight.
    e.exdates = times(event, "EXDATE", here)
        .into_iter()
        .map(|m| if e.all_day { Moment::At(m.midnight()) } else { m })
        .collect();
    e.rdates = times(event, "RDATE", here).into_iter().map(Moment::midnight).collect();
    e
}

/// What an invite's fields come to, the card's words among them.
fn invite_of(e: &Fields, method: &str, here: Here<'_>) -> Invite {
    let iso = |d: &Option<Dt>| d.as_ref().map(Dt::isoformat).unwrap_or_default();
    Invite {
        summary: e.summary.clone().unwrap_or_else(|| "(no title)".into()),
        location: e.location.clone().unwrap_or_default(),
        description: e.description.as_deref().unwrap_or_default().chars().take(1500).collect(),
        when: when(e.start.as_ref(), e.end.as_ref(), e.all_day, here),
        all_day: e.all_day,
        start_iso: iso(&e.start),
        end_iso: iso(&e.end),
        organizer: e.organizer.clone(),
        attendees: e.attendees.iter().filter(|a| !a.email.is_empty()).take(25).cloned().collect(),
        recurrence: rule_in_words(e.rrule.as_deref().unwrap_or_default(), here),
        method: method.to_owned(),
        status: e.status.clone().unwrap_or_default(),
        uid: e.uid.clone().unwrap_or_default(),
        rrule: e.rrule.clone().unwrap_or_default(),
        exdates: e.exdates.iter().map(Moment::isoformat).collect(),
        recurrence_id: iso(&e.recurrence_id),
        tzid: e.tzid.clone(),
        sequence: e.sequence,
        rdates: e.rdates.iter().map(Dt::isoformat).collect(),
        this_and_future: e.recurrence_id.is_some() && e.this_and_future,
        span: None,
    }
}

/// The names a TZID might call its zone: itself, or Windows' name for it. One opening with a slash, RFC 5545's mark
/// of a name from a registry (`/vendor.example/Olson_2011/Europe/Berlin`), also by what ends it, at most three parts.
fn zone_names(tzid: &str) -> Vec<String> {
    let mut names = vec![windows_zones::named(tzid).unwrap_or(tzid).to_owned()];
    if tzid.starts_with('/') {
        let parts: Vec<&str> = tzid.split('/').collect();
        let path = &parts[parts.len().saturating_sub(3)..];
        names.extend((0..path.len().saturating_sub(1)).map(|at| path[at..].join("/")));
    }
    names
}

/// The zone a TZID names, in the zone database or as Windows calls it. Any other is no zone at all: the time it
/// qualifies floats.
pub fn zone(tzid: &str, zones: &dyn ZoneNames) -> Option<Tz> {
    for name in zone_names(tzid) {
        if name.is_empty() {
            return None;
        }
        if let Some(zone) = zones.zone(&name) {
            return Some(Tz::Named(Arc::new(Named { name, zone })));
        }
    }
    None
}

pub fn party(value: &str, name: &str) -> Party {
    let mut email = ics::py_strip(value);
    if email.get(..7).is_some_and(|head| head.eq_ignore_ascii_case("mailto:")) {
        email = &email[7..];
    }
    Party { name: ics::py_strip(name).to_owned(), email: ics::py_strip(email).to_owned() }
}

/// Python's `int(text)` of a short field: its digits, with a sign and the spaces around it Python allows, and an
/// underscore between two digits.
pub(crate) fn py_int(text: &str) -> Option<i64> {
    let text = ics::py_strip(text);
    let (negative, digits) = match text.as_bytes().first() {
        Some(b'-') => (true, &text[1..]),
        Some(b'+') => (false, &text[1..]),
        _ => (false, text),
    };
    if digits.is_empty()
        || digits.starts_with('_')
        || digits.ends_with('_')
        || digits.contains("__")
        || !digits.bytes().all(|b| b.is_ascii_digit() || b == b'_')
    {
        return None;
    }
    let value: i64 = digits.replace('_', "").parse().ok()?;
    Some(if negative { -value } else { value })
}

/// What an iCalendar date, date-time, duration, period or time value is (`vDDDTypes.from_ical`).
enum Ddd {
    At(Dt),
    Day(i64),
    /// A period: its start.
    Period(Box<Ddd>),
    /// A duration or a time of day: no time an event starts at.
    Other,
}

/// `vDDDTypes.from_ical`: None where it raises.
fn ddd(value: &str) -> Option<Ddd> {
    let upper = value.to_uppercase();
    if upper.starts_with('P') || upper.starts_with("-P") || upper.starts_with("+P") {
        return duration(value).map(|_| Ddd::Other);
    }
    if upper.contains('/') {
        let [start, end] = value.split('/').collect::<Vec<_>>()[..] else { return None };
        // A period's day is its midnight.
        let start = match ddd(start)? {
            Ddd::Day(day) => Ddd::At(Dt::floating(day * DAY)),
            other => other,
        };
        ddd(end)?;
        return Some(Ddd::Period(Box::new(start)));
    }
    match value.chars().count() {
        15 | 16 => date_time(value).map(Ddd::At),
        8 if value.bytes().all(|b| b.is_ascii_digit()) => {
            let field = |from: usize, to: usize| value.get(from..to).and_then(py_int);
            let small = |from: usize, to: usize| field(from, to).and_then(|n| u32::try_from(n).ok());
            let day = Dt::from_parts(field(0, 4)?, small(4, 6)?, small(6, 8)?, 0, 0, 0, Tz::Floating)?;
            Some(Ddd::Day(day.day()))
        }
        6 | 7 => time_of_day(value).then_some(Ddd::Other),
        _ => None,
    }
}

/// `vDatetime.from_ical`: `YYYYMMDDTHHMMSS`, with `Z` for UTC.
fn date_time(value: &str) -> Option<Dt> {
    let v = value.rsplit(':').next()?;
    if !v.is_ascii() || v.len() < 15 || v.as_bytes()[8] != b'T' {
        return None;
    }
    let tz = match &v[15..] {
        "" => Tz::Floating,
        "Z" => Tz::Fixed(0),
        _ => return None,
    };
    let field = |from: usize, to: usize| py_int(&v[from..to]);
    let small = |from: usize, to: usize| field(from, to).and_then(|n| u32::try_from(n).ok());
    Dt::from_parts(field(0, 4)?, small(4, 6)?, small(6, 8)?, small(9, 11)?, small(11, 13)?, small(13, 15)?, tz)
}

/// Whether `vTime.from_ical` reads it: `HHMMSS`, with `Z` for UTC.
fn time_of_day(value: &str) -> bool {
    let v = value.rsplit(':').next().unwrap_or_default();
    let v = v.strip_suffix('Z').unwrap_or(v);
    if v.len() != 6 || !v.bytes().all(|b| b.is_ascii_digit()) {
        return false;
    }
    let field = |from: usize| v[from..from + 2].parse::<u32>().unwrap_or(99);
    field(0) < 24 && field(2) < 60 && field(4) < 60
}

/// A DURATION in seconds (`vDuration.from_ical`), None where it won't read.
fn duration(value: &str) -> Option<i64> {
    let (negative, rest) = match value.as_bytes().first() {
        Some(b'-') => (true, &value[1..]),
        Some(b'+') => (false, &value[1..]),
        _ => (false, value),
    };
    let mut rest = rest.strip_prefix('P')?;
    let mut total: i64 = 0;
    let mut take = |rest: &mut &str, unit: char, seconds: i64| -> Option<()> {
        let digits = rest.bytes().take_while(u8::is_ascii_digit).count();
        if digits > 0 && rest[digits..].starts_with(unit) {
            let n: i64 = rest[..digits].parse().ok()?;
            total = total.checked_add(n.checked_mul(seconds)?)?;
            *rest = &rest[digits + 1..];
        }
        Some(())
    };
    take(&mut rest, 'W', 7 * DAY)?;
    take(&mut rest, 'D', DAY)?;
    if let Some(time) = rest.strip_prefix('T') {
        rest = time;
        take(&mut rest, 'H', 3600)?;
        take(&mut rest, 'M', 60)?;
        take(&mut rest, 'S', 1)?;
    }
    // A timedelta holds 999,999,999 days at most.
    (rest.is_empty() && total < 1_000_000_000 * DAY).then_some(if negative { -total } else { total })
}

/// A time read with its TZID: a floating one put in the zone the TZID names, if it names one.
fn zoned(at: Dt, tzid: &str, zones: &dyn ZoneNames) -> Dt {
    if at.is_aware() {
        return at;
    }
    match zone(tzid, zones) {
        Some(tz) => at.with_tz(tz),
        None => at,
    }
}

/// DTSTART's, DTEND's or RECURRENCE-ID's time, and whether it is a whole day (`_time`). What the iCalendar reader
/// wouldn't read goes to `read_time`.
fn prop_time(prop: &Prop, here: Here<'_>) -> (Option<Dt>, bool) {
    match prop.param("VALUE").to_uppercase().as_str() {
        "TEXT" | "INTEGER" | "URI" | "CAL-ADDRESS" | "UNKNOWN" => {
            return read_time(&prop.value, &prop.param("TZID"), here);
        }
        "PERIOD" | "TIME" | "BINARY" | "BOOLEAN" | "FLOAT" | "RECUR" | "UTC-OFFSET" | "GEO" => return (None, false),
        _ => {}
    }
    match ddd(&prop.value) {
        Some(Ddd::At(at)) => (Some(zoned(at, &prop.param("TZID"), here.zones)), false),
        Some(Ddd::Day(day)) => (Some(Dt::floating(day * DAY)), true),
        Some(Ddd::Period(_) | Ddd::Other) => (None, false),
        None => read_time(&prop.value, &prop.param("TZID"), here),
    }
}

/// Each value of each EXDATE or RDATE line: a day for a whole day, the start of a period for a period. One bad value
/// sends its line to `read_time`, a value at a time.
fn times(event: &Component, name: &str, here: Here<'_>) -> Vec<Moment> {
    let mut out = Vec::new();
    for line in event.each(name) {
        if name == "RDATE" && line.value.is_empty() {
            continue;
        }
        let tzid = line.param("TZID");
        let read: Option<Vec<Ddd>> = line.value.split(',').map(ddd).collect();
        let Some(values) = read else {
            for piece in line.value.split(',') {
                if let (Some(at), whole_day) = read_time(piece, &tzid, here) {
                    out.push(if whole_day { Moment::Day(at.day()) } else { Moment::At(at) });
                }
            }
            continue;
        };
        for value in values {
            let value = match value {
                Ddd::Period(start) => *start,
                other => other,
            };
            match value {
                Ddd::At(at) => out.push(Moment::At(zoned(at, &tzid, here.zones))),
                Ddd::Day(day) => out.push(Moment::Day(day)),
                Ddd::Period(_) | Ddd::Other => {}
            }
        }
    }
    out
}

/// One field of a `strptime` format, as Python's `_strptime` matches it.
#[derive(Clone, Copy)]
enum Field {
    Year,
    Month,
    Day,
    Hour,
    Minute,
    Second,
    T,
}

impl Field {
    /// The lengths this field can take at the start of `rest`, in the order Python's regular expression tries them.
    fn takes(self, rest: &[u8]) -> Vec<usize> {
        let at = |i: usize| rest.get(i).copied().unwrap_or(0);
        let d = |i: usize| at(i).is_ascii_digit();
        let within = |i: usize, lo: u8, hi: u8| (lo..=hi).contains(&at(i));
        let mut out = Vec::new();
        let mut try_ = |ok: bool, len: usize| {
            if ok {
                out.push(len);
            }
        };
        match self {
            Field::Year => try_(d(0) && d(1) && d(2) && d(3), 4),
            Field::Month => {
                try_(at(0) == b'1' && within(1, b'0', b'2'), 2);
                try_(at(0) == b'0' && within(1, b'1', b'9'), 2);
                try_(within(0, b'1', b'9'), 1);
            }
            Field::Day => {
                try_(at(0) == b'3' && within(1, b'0', b'1'), 2);
                try_(within(0, b'1', b'2') && d(1), 2);
                try_(at(0) == b'0' && within(1, b'1', b'9'), 2);
                try_(within(0, b'1', b'9'), 1);
                try_(at(0) == b' ' && within(1, b'1', b'9'), 2);
            }
            Field::Hour => {
                try_(at(0) == b'2' && within(1, b'0', b'3'), 2);
                try_(within(0, b'0', b'1') && d(1), 2);
                try_(d(0), 1);
                try_(at(0) == b' ' && d(1), 2);
            }
            Field::Minute => {
                try_(within(0, b'0', b'5') && d(1), 2);
                try_(d(0), 1);
            }
            Field::Second => {
                try_(at(0) == b'6' && within(1, b'0', b'1'), 2);
                try_(within(0, b'0', b'5') && d(1), 2);
                try_(d(0), 1);
            }
            Field::T => try_(matches!(at(0), b'T' | b't'), 1),
        }
        out
    }
}

/// The first way `fields` match the start of `text`, as a backtracking regular expression finds it: each field's
/// length.
fn first_match(fields: &[Field], text: &[u8]) -> Option<Vec<usize>> {
    let Some((field, rest)) = fields.split_first() else { return Some(Vec::new()) };
    for len in field.takes(text) {
        if let Some(mut more) = first_match(rest, &text[len..]) {
            more.insert(0, len);
            return Some(more);
        }
    }
    None
}

/// `datetime.strptime(text, format)` for the formats an invite's times are read with: None where it raises.
fn strptime(text: &str, fields: &[Field]) -> Option<Dt> {
    let bytes = text.as_bytes();
    let lens = first_match(fields, bytes)?;
    if lens.iter().sum::<usize>() != bytes.len() {
        return None;
    }
    let mut at = 0;
    let mut parts = [1_i64, 1, 1, 0, 0, 0];
    for (field, len) in fields.iter().zip(lens) {
        let value = || text[at..at + len].trim().parse::<i64>().unwrap_or(0);
        match field {
            Field::Year => parts[0] = value(),
            Field::Month => parts[1] = value(),
            Field::Day => parts[2] = value(),
            Field::Hour => parts[3] = value(),
            Field::Minute => parts[4] = value(),
            Field::Second => parts[5] = value(),
            Field::T => {}
        }
        at += len;
    }
    let [year, month, day, hour, minute, second] = parts;
    let small = |n: i64| u32::try_from(n).ok();
    Dt::from_parts(year, small(month)?, small(day)?, small(hour)?, small(minute)?, small(second)?, Tz::Floating)
}

const DATE: [Field; 3] = [Field::Year, Field::Month, Field::Day];
const TO_MINUTE: [Field; 6] = [Field::Year, Field::Month, Field::Day, Field::T, Field::Hour, Field::Minute];
const TO_SECOND: [Field; 7] =
    [Field::Year, Field::Month, Field::Day, Field::T, Field::Hour, Field::Minute, Field::Second];

/// A time as a line said it, and whether it is a whole day (`read_time`): one ending in Z is UTC's, any other in the
/// zone its TZID names, or floating.
pub fn read_time(value: &str, tzid: &str, here: Here<'_>) -> (Option<Dt>, bool) {
    let v = ics::py_strip(value);
    if v.chars().count() == 8 && v.bytes().all(|b| b.is_ascii_digit()) {
        return (strptime(v, &DATE), true);
    }
    let (utc, v) = match v.strip_suffix('Z') {
        Some(rest) => (true, rest),
        None => (false, v),
    };
    // This order: strptime would read 1030 as 10:03:00, with seconds to fill.
    let Some(at) = strptime(v, &TO_MINUTE).or_else(|| strptime(v, &TO_SECOND)) else { return (None, false) };
    if utc {
        return (Some(at.with_tz(Tz::Fixed(0))), false);
    }
    match zone(tzid, here.zones) {
        Some(tz) => (Some(at.with_tz(tz)), false),
        None => (Some(at), false),
    }
}

/// An aware time on the person's wall clock; a floating one, or one past Python's years there, as it is.
fn local(at: &Dt, here: Here<'_>) -> Dt {
    at.local_naive(here.person).unwrap_or_else(|| at.clone())
}

/// The card's words for when: `Tue, Nov 3 · 1:00 PM–2:00 PM`.
pub fn when(start: Option<&Dt>, end: Option<&Dt>, all_day: bool, here: Here<'_>) -> String {
    let Some(start) = start else { return String::new() };
    let s = local(start, here);
    if all_day {
        return s.day_words();
    }
    let head = format!("{} · {}", s.day_words(), s.clock_words());
    let Some(end) = end else { return head };
    let e = local(end, here);
    if e.day() == s.day() {
        format!("{head}–{}", e.clock_words())
    } else {
        format!("{head} → {} {}", e.day_words(), e.clock_words())
    }
}

const FREQ_WORDS: [(&str, &str); 4] = [("DAILY", "day"), ("WEEKLY", "week"), ("MONTHLY", "month"), ("YEARLY", "year")];
const DAY_WORDS: [(&str, &str); 7] =
    [("MO", "Mon"), ("TU", "Tue"), ("WE", "Wed"), ("TH", "Thu"), ("FR", "Fri"), ("SA", "Sat"), ("SU", "Sun")];

/// A rule in words: `Every 2 weeks on Mon, Wed ×10`.
pub fn rule_in_words(rule: &str, here: Here<'_>) -> String {
    if rule.is_empty() {
        return String::new();
    }
    let mut kv: BTreeMap<String, &str> = BTreeMap::new();
    for part in rule.split(';') {
        if let Some((k, v)) = part.split_once('=') {
            kv.insert(k.to_uppercase(), v);
        }
    }
    let get = |k: &str| kv.get(k).copied().unwrap_or_default();
    let freq = get("FREQ").to_uppercase();
    let Some(&(_, freq)) = FREQ_WORDS.iter().find(|(f, _)| *f == freq) else { return String::new() };
    let n = py_int(kv.get("INTERVAL").copied().unwrap_or("1")).unwrap_or(1);
    let mut base = if n > 1 { format!("Every {n} {freq}s") } else { format!("Every {freq}") };
    if !get("BYDAY").is_empty() {
        let days: Vec<String> = get("BYDAY")
            .split(',')
            .map(|x| {
                let stripped = ics::py_strip(x);
                let tail: String = stripped.chars().rev().take(2).collect::<Vec<_>>().into_iter().rev().collect();
                let tail = tail.to_uppercase();
                DAY_WORDS.iter().find(|(d, _)| *d == tail).map_or_else(|| x.to_owned(), |(_, w)| (*w).to_owned())
            })
            .collect();
        base.push_str(" on ");
        base.push_str(&days.join(", "));
    }
    if !get("COUNT").is_empty() {
        base.push_str(" ×");
        base.push_str(get("COUNT"));
    } else if !get("UNTIL").is_empty()
        && let (Some(until), _) = read_time(get("UNTIL"), "", here)
    {
        base.push_str(" until ");
        base.push_str(&local(&until, here).month_day_year());
    }
    base
}

/// How long an event runs, in seconds, an all-day one with no end a day. An end that floats where its start doesn't,
/// or the other way about, is read by its wall clock, as though in the start's terms.
pub fn length(start: &Dt, end: Option<&Dt>, all_day: bool) -> i64 {
    let Some(end) = end else { return if all_day { DAY } else { 0 } };
    let end = if end.is_aware() == start.is_aware() { end.clone() } else { end.with_tz(start.tz.clone()) };
    end.minus(start).max(0)
}

/// A rule's parts, each name and value stripped and upper case.
pub fn rule_parts(rule: &str) -> BTreeMap<String, String> {
    let mut kv = BTreeMap::new();
    for part in rule.split(';') {
        if let Some((k, v)) = part.split_once('=') {
            kv.insert(ics::py_strip(k).to_uppercase(), ics::py_strip(v).to_uppercase());
        }
    }
    kv
}

/// A bound past Python's years, where reading it raised.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct PastYears;

/// UNTIL as a bound comparable with the event's own starts (inclusive), or None.
pub fn until_bound(kv: &BTreeMap<String, String>, start: &Dt, here: Here<'_>) -> Result<Option<Dt>, PastYears> {
    let (until, date_only) = read_time(kv.get("UNTIL").map_or("", String::as_str), "", here);
    let Some(mut until) = until else { return Ok(None) };
    if date_only {
        until = until.plus(DAY - 1).ok_or(PastYears)?;
    }
    if until.is_aware() && !start.is_aware() {
        return until.local_naive(here.person).map(Some).ok_or(PastYears);
    }
    if start.is_aware() && !until.is_aware() {
        return Ok(Some(until.with_tz(start.tz.clone())));
    }
    Ok(Some(until))
}

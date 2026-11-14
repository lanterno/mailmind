//! An event a person has confirmed, as a METHOD:PUBLISH calendar: a file for their calendar, beside an answer to
//! someone else's (`mailmind.calendar.domain.publish`, `application.publishing`, and the reading of what is
//! proposed for a calendar, `mailmind.kernel.proposed`). It sends nothing and makes nothing: the page saves what this
//! answers, and the person opens it. It is sent to no one, so it holds no ATTENDEE, and its ORGANIZER, which a
//! PUBLISH must name (RFC 5546), is the person whose calendar it goes in.
//!
//! What it says came from a stranger's mail by way of a model, or as a person typed it, so nothing in it may break a
//! line, and a date that won't do is refused and said so.

use std::sync::Arc;

use mm_kernel::errors::{Failure, Fault, Problem};
use mm_kernel::isoformat;
use mm_kernel::seams::Clock;
use mm_kernel::when::{civil, days_from_civil};
use mm_kernel::zone::Zone;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

use crate::ports::Message;
use crate::rsvp::{fold, ics_dt, ics_esc};
use crate::time::{DAY, Dt, Tz, date_iso};

/// How far before today a date may be: a message read late can still name last month's meeting.
const BEHIND_DAYS: i64 = 366;
const AHEAD_DAYS: i64 = 366 * 5;
/// What an event may run to: past this it's a misread end.
const LONGEST: i64 = 31 * DAY;
pub const TITLE_MAX: usize = 200;
pub const PLACE_MAX: usize = 300;
/// A time with no end runs this long: a calendar's zero-minute event otherwise.
const DEFAULT_LENGTH: i64 = 3600;
pub const FILENAME: &str = "event.ics";

/// What ends a line somewhere, and what no line should hold.
fn breaks(c: char) -> bool {
    matches!(c, '\u{0}'..='\u{1f}' | '\u{7f}'..='\u{9f}' | '\u{2028}' | '\u{2029}')
}

/// Text as one line, within `cap` characters.
pub fn one_line(value: Option<&str>, cap: usize) -> String {
    let Some(value) = value else { return String::new() };
    let spaced: String = value.chars().map(|c| if breaks(c) { ' ' } else { c }).collect();
    let joined = spaced.split(mm_kernel::text::is_space).filter(|w| !w.is_empty()).collect::<Vec<_>>().join(" ");
    joined.chars().take(cap).collect()
}

/// `\d{4}-\d{2}-\d{2}`, whole.
fn is_day_text(b: &[u8]) -> bool {
    b.len() == 10 && b.iter().enumerate().all(|(i, c)| if i == 4 || i == 7 { *c == b'-' } else { c.is_ascii_digit() })
}

/// `\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}(?::?\d{2})?)?`, whole.
fn is_time_text(s: &str) -> bool {
    let b = s.as_bytes();
    let digits = |from: usize, n: usize| b.len() >= from + n && b[from..from + n].iter().all(u8::is_ascii_digit);
    if b.len() < 16 || !is_day_text(&b[..10]) || !matches!(b[10], b'T' | b' ') || !digits(11, 2) || b[13] != b':' {
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

/// A day or a time as a person or a model proposed it.
#[derive(Clone, Debug)]
enum Reading {
    /// Days since 1970-01-01.
    Day(i64),
    At(Dt),
}

impl Reading {
    fn day(&self) -> i64 {
        match self {
            Reading::Day(day) => *day,
            Reading::At(at) => at.day(),
        }
    }
}

fn reading(text: Option<&str>) -> Option<Reading> {
    let s = mm_kernel::text::strip(text?);
    if is_day_text(s.as_bytes()) {
        let n = |r: std::ops::Range<usize>| s[r].parse::<u32>().ok();
        let year = i64::from(n(0..4)?);
        let (month, day) = (n(5..7)?, n(8..10)?);
        let days = days_from_civil(year, month, day);
        return (year >= 1 && civil(days) == (year, month, day)).then_some(Reading::Day(days));
    }
    if !is_time_text(s) {
        return None;
    }
    let time = isoformat::read(s)?;
    let w = time.wall;
    let tz = time.offset.map_or(Tz::Floating, |micros| Tz::Fixed(micros.div_euclid(1_000_000)));
    Some(Reading::At(Dt::from_parts(i64::from(w.year), w.month, w.day, w.hour, w.minute, w.second, tz)?))
}

/// A day, or a time with its offset where it has one, as ISO text: None where it won't read or falls outside the
/// window around `today` (days since 1970-01-01). A date a model made up or mistook is dropped, not clamped to the
/// window's edge, which would put a wrong date in front of someone.
pub fn read_moment(text: Option<&str>, today: i64) -> Option<String> {
    let value = reading(text)?;
    if !(today - BEHIND_DAYS..=today + AHEAD_DAYS).contains(&value.day()) {
        return None;
    }
    Some(match value {
        Reading::Day(day) => date_iso(day),
        Reading::At(at) => {
            let iso = at.isoformat();
            // To the minute: `isoformat(timespec="minutes")`.
            format!("{}{}", &iso[..16], &iso[19..])
        }
    })
}

/// Whether what `read_moment` gave is a whole day, rather than a time.
pub fn is_day(moment: &str) -> bool {
    is_day_text(moment.as_bytes())
}

/// Whether `end` can end an event that begins at `start`, both as `read_moment` gave them: of one kind, not before it
/// (a day may end on itself, a time may not), and no more than `LONGEST` past it.
pub fn ends_after(start: &str, end: &str) -> bool {
    match (reading(Some(start)), reading(Some(end))) {
        (Some(Reading::At(a)), Some(Reading::At(b))) => {
            a.is_aware() == b.is_aware() && (1..=LONGEST).contains(&b.minus(&a))
        }
        (Some(Reading::Day(a)), Some(Reading::Day(b))) => (0..=LONGEST / DAY).contains(&(b - a)),
        _ => false,
    }
}

/// An event a person confirmed.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct NewEvent {
    pub title: String,
    /// A day or a time, as `read_moment` gave it.
    pub start: String,
    pub end: Option<String>,
    pub place: String,
}

/// The same event confirmed again is the same event to a calendar, which replaces it rather than listing it twice.
pub fn uid_for(account: &str, key: Option<i64>, event: &NewEvent) -> String {
    let key = key.map_or_else(|| "None".to_owned(), |k| k.to_string());
    let seed = [account, key.as_str(), event.title.as_str(), event.start.as_str()].join("\0");
    let digest = Sha256::digest(seed.as_bytes());
    let hex: String = digest.iter().map(|b| format!("{b:02x}")).collect();
    format!("{}@mailmind.local", &hex[..32])
}

fn esc(text: &str, cap: usize) -> String {
    ics_esc(&one_line(Some(text), cap))
}

fn when_lines(event: &NewEvent, person: &dyn Zone) -> Vec<String> {
    if is_day(&event.start) {
        // DTEND of a day is the day after the last, exclusive.
        let last = match reading(Some(event.end.as_deref().unwrap_or(&event.start))) {
            Some(Reading::Day(day)) => day,
            _ => 0,
        };
        let (y, m, d) = civil(last + 1);
        return vec![
            format!("DTSTART;VALUE=DATE:{}", event.start.replace('-', "")),
            format!("DTEND;VALUE=DATE:{y:04}{m:02}{d:02}"),
        ];
    }
    let end = match &event.end {
        Some(end) => end.clone(),
        None => match reading(Some(&event.start)) {
            Some(Reading::At(at)) => at.plus(DEFAULT_LENGTH).map_or_else(String::new, |end| {
                let iso = end.isoformat();
                format!("{}{}", &iso[..16], &iso[19..])
            }),
            _ => String::new(),
        },
    };
    vec![format!("DTSTART:{}", ics_dt(&event.start, person)), format!("DTEND:{}", ics_dt(&end, person))]
}

/// A METHOD:PUBLISH calendar holding `event`, for `organizer`'s own calendar.
pub fn build(event: &NewEvent, organizer: &str, uid: &str, stamp: &Dt, person: &dyn Zone) -> String {
    let mut lines: Vec<String> = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//MailMind//Suggestion//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
    ]
    .into_iter()
    .map(str::to_owned)
    .collect();
    lines.push(fold(&format!("UID:{uid}")));
    lines.push(format!("DTSTAMP:{}", ics_dt(&stamp.isoformat(), person)));
    lines.push("SEQUENCE:0".into());
    lines.push(fold(&format!("SUMMARY:{}", esc(&event.title, TITLE_MAX))));
    lines.extend(when_lines(event, person));
    if !event.place.is_empty() {
        lines.push(fold(&format!("LOCATION:{}", esc(&event.place, PLACE_MAX))));
    }
    lines.push(fold(&format!("ORGANIZER:mailto:{}", esc(organizer, 320))));
    lines.extend(["END:VEVENT".to_owned(), "END:VCALENDAR".to_owned()]);
    lines.join("\r\n") + "\r\n"
}

/// What an event file asks: the message's pk, None where the request names none, and each field as the person left
/// it.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Asked {
    pub pk: Option<i64>,
    pub title: Option<String>,
    pub start: Option<String>,
    pub end: Option<String>,
    pub place: Option<String>,
}

/// The message an event file comes from.
pub trait EventMessages: Send + Sync + std::fmt::Debug {
    fn message(&self, pk: i64) -> Result<Option<Message>, Fault>;
}

/// `POST /api/calendar/event`.
#[derive(Debug)]
pub struct Publishing {
    messages: Arc<dyn EventMessages>,
    clock: Arc<dyn Clock>,
    person: Arc<dyn Zone>,
}

impl Publishing {
    pub fn new(messages: Arc<dyn EventMessages>, clock: Arc<dyn Clock>, person: Arc<dyn Zone>) -> Publishing {
        Publishing { messages, clock, person }
    }

    /// The event as a file for the person's calendar.
    pub fn event(&self, asked: &Asked) -> Result<Value, Failure> {
        let refused = |words: &str| Failure::Problem(Problem::refused(words));
        let pk = asked.pk.ok_or_else(|| refused("missing message"))?;
        let title = one_line(asked.title.as_deref(), TITLE_MAX);
        if title.is_empty() {
            return Err(refused("an event needs a title"));
        }
        let now = self.clock.now().micros().div_euclid(1_000_000);
        let today = mm_kernel::zone::local(&*self.person, now).div_euclid(DAY);
        let start = read_moment(asked.start.as_deref(), today)
            .ok_or_else(|| refused("that start isn\u{2019}t a date this can use"))?;
        let mut end = None;
        if asked.end.as_deref().is_some_and(|e| !e.is_empty()) {
            end = read_moment(asked.end.as_deref(), today).filter(|end| ends_after(&start, end));
            if end.is_none() {
                return Err(refused("that end doesn\u{2019}t come after the start"));
            }
        }
        let row = self.messages.message(pk)?.ok_or_else(|| Failure::Problem(Problem::not_found("not found")))?;
        let event = NewEvent { title, start, end, place: one_line(asked.place.as_deref(), PLACE_MAX) };
        let uid = uid_for(&row.account, row.key, &event);
        let stamp = Dt::new(now, Tz::Fixed(0));
        Ok(json!({
            "ok": true, "filename": FILENAME, "ics": build(&event, &row.account, &uid, &stamp, &*self.person),
        }))
    }
}

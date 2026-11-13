//! A Date header as the reader shows it: read as Python's `email.utils` read one, which is lenient in ways mail
//! leans on (a missing weekday, two-digit years, US zone names, dots in the time), and written back the RFC 5322 way.
//! Replies and forwards quote it, and a date that reads differently would quote a different day.

const MONTHS: [&str; 24] = [
    "jan",
    "feb",
    "mar",
    "apr",
    "may",
    "jun",
    "jul",
    "aug",
    "sep",
    "oct",
    "nov",
    "dec",
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
];
const DAYS: [&str; 7] = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const ZONES: [(&str, i64); 14] = [
    ("UT", 0),
    ("UTC", 0),
    ("GMT", 0),
    ("Z", 0),
    ("AST", -400),
    ("ADT", -300),
    ("EST", -500),
    ("EDT", -400),
    ("CST", -600),
    ("CDT", -500),
    ("MST", -700),
    ("MDT", -600),
    ("PST", -800),
    ("PDT", -700),
];
const WEEKDAY_NAMES: [&str; 7] = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const MONTH_NAMES: [&str; 12] = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/// A date and time as a header gave it, and its zone in seconds east of UTC (None for -0000, which knows none).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct Stamp {
    year: i64,
    month: i64,
    day: i64,
    hour: i64,
    minute: i64,
    second: i64,
    zone: Option<i64>,
}

/// A date written the RFC 5322 way (`Mon, 02 Nov 2026 12:00:00 +0000`), or None when it doesn't read as one.
pub(super) fn normalized(value: &str) -> Option<String> {
    let stamp = read(value)?;
    valid(&stamp).then(|| written(&stamp))
}

/// Python's `int()` on a token: an optional sign, then digits, single underscores allowed between them.
fn int(token: &str) -> Option<i64> {
    let digits = token.strip_prefix(['+', '-']).unwrap_or(token);
    if digits.is_empty()
        || !digits.bytes().all(|byte| byte.is_ascii_digit() || byte == b'_')
        || digits.starts_with('_')
        || digits.ends_with('_')
        || digits.contains("__")
    {
        return None;
    }
    let number: i64 = digits.replace('_', "").parse().ok()?;
    Some(if token.starts_with('-') { -number } else { number })
}

/// Python's `email._parseaddr._parsedate_tz`, step for step.
fn read(value: &str) -> Option<Stamp> {
    let mut data: Vec<String> = value.split_whitespace().map(str::to_owned).collect();
    let first = data.first()?;
    if first.ends_with(',') || DAYS.contains(&first.to_lowercase().as_str()) {
        data.remove(0);
    } else if let Some(comma) = first.rfind(',') {
        let after = first[comma + 1..].to_owned();
        if let Some(slot) = data.first_mut() {
            *slot = after;
        }
    }
    if data.len() == 3 {
        let stuff: Vec<String> = data.first()?.split('-').map(str::to_owned).collect();
        if stuff.len() == 3 {
            data = stuff.into_iter().chain(data.into_iter().skip(1)).collect();
        }
    }
    if data.len() == 4 {
        let s = data.get(3)?.clone();
        let sign = s.find('+').or_else(|| s.find('-'));
        match sign {
            Some(at) if at > 0 => {
                data.truncate(3);
                data.push(s[..at].to_owned());
                data.push(s[at..].to_owned());
            }
            _ => data.push(String::new()),
        }
    }
    if data.len() < 5 {
        return None;
    }
    data.truncate(5);
    let [dd, mm, yy, tm, tz]: [String; 5] = data.try_into().ok()?;
    let (mut dd, mut mm, mut yy, mut tm, mut tz) = (dd, mm, yy, tm, tz);
    if dd.is_empty() || mm.is_empty() || yy.is_empty() {
        return None;
    }
    mm = mm.to_lowercase();
    if !MONTHS.contains(&mm.as_str()) {
        (dd, mm) = (mm, dd.to_lowercase());
        if !MONTHS.contains(&mm.as_str()) {
            return None;
        }
    }
    let mut month = MONTHS.iter().position(|name| *name == mm)? as i64 + 1;
    if month > 12 {
        month -= 12;
    }
    if dd.ends_with(',') {
        dd.pop();
    }
    if yy.find(':').is_some_and(|at| at > 0) {
        (yy, tm) = (tm, yy);
    }
    if yy.ends_with(',') {
        yy.pop();
        if yy.is_empty() {
            return None;
        }
    }
    if !yy.chars().next().is_some_and(|c| c.is_ascii_digit()) {
        (yy, tz) = (tz, yy);
    }
    if tm.ends_with(',') {
        tm.pop();
    }
    let pieces: Vec<&str> = tm.split(':').collect();
    let (hh, mi, ss) = match pieces.as_slice() {
        [hh, mi] => (*hh, *mi, "0"),
        [hh, mi, ss] => (*hh, *mi, *ss),
        [one] if one.contains('.') => match one.split('.').collect::<Vec<_>>().as_slice() {
            [hh, mi] => (*hh, *mi, "0"),
            [hh, mi, ss] => (*hh, *mi, *ss),
            _ => return None,
        },
        _ => return None,
    };
    let mut year = int(&yy)?;
    let day = int(&dd)?;
    let (hour, minute, second) = (int(hh)?, int(mi)?, int(ss)?);
    if year < 100 {
        year += if year > 68 { 1900 } else { 2000 };
    }
    let tz = tz.to_uppercase();
    let mut offset = match ZONES.iter().find(|(name, _)| *name == tz) {
        Some((_, offset)) => Some(*offset),
        None => {
            let offset = int(&tz);
            if offset == Some(0) && tz.starts_with('-') { None } else { offset }
        }
    };
    if let Some(found) = offset.filter(|found| *found != 0) {
        let magnitude = found.abs();
        offset = Some(found.signum() * ((magnitude / 100) * 3600 + (magnitude % 100) * 60));
    } else if tz.is_empty() || offset.is_none() {
        offset = None;
    }
    Some(Stamp { year, month, day, hour, minute, second, zone: offset })
}

/// Whether Python's `datetime` takes the stamp: a real day and time, and a zone within a day of UTC.
fn valid(stamp: &Stamp) -> bool {
    (1..=9999).contains(&stamp.year)
        && (1..=12).contains(&stamp.month)
        && (1..=days_in(stamp.year, stamp.month)).contains(&stamp.day)
        && (0..24).contains(&stamp.hour)
        && (0..60).contains(&stamp.minute)
        && (0..60).contains(&stamp.second)
        && stamp.zone.is_none_or(|zone| zone.abs() < 86_400)
}

fn leap(year: i64) -> bool {
    year % 4 == 0 && (year % 100 != 0 || year % 400 == 0)
}

fn days_in(year: i64, month: i64) -> i64 {
    match month {
        2 if leap(year) => 29,
        2 => 28,
        4 | 6 | 9 | 11 => 30,
        _ => 31,
    }
}

/// Days since 1970-01-01 of a date in the proleptic Gregorian calendar.
fn days_from_civil(year: i64, month: i64, day: i64) -> i64 {
    let year = if month <= 2 { year - 1 } else { year };
    let era = year.div_euclid(400);
    let of_era = year - era * 400;
    let of_year = (153 * (month + if month > 2 { -3 } else { 9 }) + 2) / 5 + day - 1;
    let of_cycle = of_era * 365 + of_era / 4 - of_era / 100 + of_year;
    era * 146_097 + of_cycle - 719_468
}

/// Python's `email.utils.format_datetime`: the weekday of the date as written, and -0000 for no zone.
fn written(stamp: &Stamp) -> String {
    // 1970-01-01 was a Thursday, Monday counting as 0.
    let weekday = (days_from_civil(stamp.year, stamp.month, stamp.day) + 3).rem_euclid(7) as usize;
    let zone = match stamp.zone {
        None => "-0000".to_owned(),
        Some(seconds) => {
            let sign = if seconds < 0 { '-' } else { '+' };
            let seconds = seconds.abs();
            format!("{sign}{:02}{:02}", seconds / 3600, (seconds % 3600) / 60)
        }
    };
    format!(
        "{}, {:02} {} {:04} {:02}:{:02}:{:02} {}",
        WEEKDAY_NAMES.get(weekday).copied().unwrap_or_default(),
        stamp.day,
        MONTH_NAMES.get(stamp.month as usize - 1).copied().unwrap_or_default(),
        stamp.year,
        stamp.hour,
        stamp.minute,
        stamp.second,
        zone
    )
}

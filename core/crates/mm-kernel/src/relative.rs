//! How long ago something was, as a list says it (`mailmind.kernel.when`): `12m ago`, `5h ago`, `yesterday`,
//! `Oct 05`, and a card's shorter `3d`. Each takes the moment it is now, and the person's zone where the day matters,
//! so nothing here reads a clock.

use crate::isoformat;
use crate::seams::Moment;
use crate::zone::{self, Zone};

const MONTHS: [&str; 12] = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/// `rel_time`: how long before `now` the time `iso` names, as a list row says it (`since`). A time with no zone is
/// UTC's. One a month or more back, or on a day still to come, is its date, as it was written; text that is no time
/// is its first ten characters.
pub fn rel_time(iso: Option<&str>, now: Moment, zone: &dyn Zone) -> String {
    let Some(iso) = iso.filter(|i| !i.is_empty()) else {
        return String::new();
    };
    let Some(time) = isoformat::read(iso) else {
        return iso.chars().take(10).collect();
    };
    let at = Moment::from_micros(time.micros_since_epoch() - time.offset.unwrap_or(0));
    since(at, now, zone, || format!("{} {:02}", MONTHS[(time.wall.month - 1) as usize], time.wall.day))
}

/// How long before `now` the moment `at` was, in words that agree with the day heads a list puts over its rows
/// (Today, Yesterday, Past week, Earlier): counted in the person's calendar days in `zone`, not in spans of 24 hours.
/// The same day is `just now` within a minute and a half, as the page says it, then minutes or hours; the day before
/// is `yesterday`, then `4d ago` within the week and `2w ago` within a month. Further back, or on a later day than
/// today, it is `date`.
pub fn since(at: Moment, now: Moment, zone: &dyn Zone, date: impl FnOnce() -> String) -> String {
    let day = |m: Moment| zone::day_and_second(zone, m.micros().div_euclid(1_000_000)).0;
    let days = day(now) - day(at);
    let seconds = (now.micros() - at.micros()).div_euclid(1_000_000);
    match days {
        0 if seconds < 90 => "just now".to_owned(),
        0 if seconds < 3600 => format!("{}m ago", seconds / 60),
        0 => format!("{}h ago", seconds / 3600),
        1 => "yesterday".to_owned(),
        2..=6 => format!("{days}d ago"),
        7..=29 => format!("{}w ago", days / 7),
        _ => date(),
    }
}

/// `ago`: how long before `now` a moment in seconds since the epoch was, shortest: `12m`, `5h`, `3d`. With no moment,
/// or 0, it is `a while`; one in the future is `0m`.
pub fn ago(ts: Option<i64>, now: Moment) -> String {
    let Some(ts) = ts.filter(|t| *t != 0) else {
        return "a while".to_owned();
    };
    let s = (now.whole_seconds() - ts).max(0);
    if s < 3600 {
        format!("{}m", s / 60)
    } else if s < 86_400 {
        format!("{}h", s / 3600)
    } else {
        format!("{}d", s / 86_400)
    }
}

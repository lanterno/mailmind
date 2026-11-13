//! How long ago something was, as a list says it (`mailmind.kernel.when`): `5h ago`, `yesterday`, `Oct 05`, and a
//! card's shorter `3d`. Each takes the moment it is now, so nothing here reads a clock.

use crate::isoformat;
use crate::seams::Moment;

const MONTHS: [&str; 12] = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const DAY: i64 = 86_400_000_000;

/// `rel_time`: how long before `now` the time `iso` names, as a list row says it. A time with no zone is UTC's. One
/// a month or more back is its date, as it was written; text that is no time is its first ten characters.
pub fn rel_time(iso: Option<&str>, now: Moment) -> String {
    let Some(iso) = iso.filter(|i| !i.is_empty()) else {
        return String::new();
    };
    let Some(time) = isoformat::read(iso) else {
        return iso.chars().take(10).collect();
    };
    let delta = now.micros() - (time.micros_since_epoch() - time.offset.unwrap_or(0));
    // A timedelta's days round down, so one in the future is days of -1 and less.
    let (days, rest) = (delta.div_euclid(DAY), delta.rem_euclid(DAY) / 1_000_000);
    match days {
        0 => match rest / 3600 {
            0 => "just now".to_owned(),
            h => format!("{h}h ago"),
        },
        1 => "yesterday".to_owned(),
        d if d < 7 => format!("{d}d ago"),
        d if d < 30 => format!("{}w ago", d.div_euclid(7)),
        _ => format!("{} {:02}", MONTHS[(time.wall.month - 1) as usize], time.wall.day),
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

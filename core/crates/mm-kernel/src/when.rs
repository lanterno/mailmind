//! A moment written as the Python app writes one: `datetime.isoformat()` of an aware UTC time.

use crate::seams::Moment;

/// `2026-11-02T12:00:00+00:00`, with `.ffffff` after the seconds when the microseconds aren't 0, as
/// `datetime.now(timezone.utc).isoformat()` writes it (`kernel.when.now_iso`).
pub fn iso(at: Moment) -> String {
    let micros = at.micros();
    let seconds = micros.div_euclid(1_000_000);
    let fraction = micros.rem_euclid(1_000_000);
    let days = seconds.div_euclid(86_400);
    let of_day = seconds.rem_euclid(86_400);
    let (year, month, day) = civil(days);
    let mut out =
        format!("{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}", of_day / 3600, of_day % 3600 / 60, of_day % 60);
    if fraction != 0 {
        out.push_str(&format!(".{fraction:06}"));
    }
    out.push_str("+00:00");
    out
}

/// The day `days` after 1970-01-01, as (year, month, day) of the proleptic Gregorian calendar.
pub fn civil(days: i64) -> (i64, u32, u32) {
    // Howard Hinnant's days_from_civil, inverted.
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let month = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    let year = yoe + era * 400 + i64::from(month <= 2);
    (year, month, day)
}

/// The days from 1970-01-01 to (year, month, day), the inverse of `civil`.
pub fn days_from_civil(year: i64, month: u32, day: u32) -> i64 {
    let year = if month <= 2 { year - 1 } else { year };
    let era = year.div_euclid(400);
    let yoe = year.rem_euclid(400);
    let month = i64::from(month);
    let doy = (153 * (if month > 2 { month - 3 } else { month + 9 }) + 2) / 5 + i64::from(day) - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

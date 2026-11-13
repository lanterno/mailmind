//! The frozen clock: it stands where the suite puts it (`MAILMIND_TEST_NOW`, then each `clock` command), and only
//! there.

use std::sync::atomic::{AtomicI64, Ordering};

use mm_kernel::seams::{Clock, Moment};
use mm_kernel::when::days_from_civil;

#[derive(Debug)]
pub struct FrozenClock {
    micros: AtomicI64,
}

impl FrozenClock {
    pub fn at(moment: Moment) -> FrozenClock {
        FrozenClock { micros: AtomicI64::new(moment.micros()) }
    }

    pub fn move_to(&self, moment: Moment) {
        self.micros.store(moment.micros(), Ordering::SeqCst);
    }
}

impl Clock for FrozenClock {
    fn now(&self) -> Moment {
        Moment::from_micros(self.micros.load(Ordering::SeqCst))
    }
}

/// A time as Python's `datetime.isoformat()` writes one, and `fromisoformat` reads: `YYYY-MM-DD`, `T` or a space,
/// `HH:MM[:SS[.ffffff]]`, and an offset (`+HH:MM[:SS]` or `Z`). One without an offset is UTC, as the suite's clock
/// is (`TZ=UTC`).
pub fn parse_iso(text: &str) -> Option<Moment> {
    let (date, time) = text.split_at_checked(10)?;
    let mut date_parts = date.split('-');
    let year: i64 = number(date_parts.next()?, 4)?;
    let month: u32 = u32::try_from(number(date_parts.next()?, 2)?).ok()?;
    let day: u32 = u32::try_from(number(date_parts.next()?, 2)?).ok()?;
    if !(1..=12).contains(&month) || !(1..=31).contains(&day) {
        return None;
    }
    let days = days_from_civil(year, month, day);
    let mut seconds_of_day = 0i64;
    let mut micros = 0i64;
    let mut offset = 0i64;
    if !time.is_empty() {
        let time = time.strip_prefix(['T', ' '])?;
        let (clock, zone) = match time.find(['+', '-', 'Z']) {
            Some(i) => time.split_at(i),
            None => (time, ""),
        };
        let (whole, fraction) = clock.split_once('.').unwrap_or((clock, ""));
        let parts: Vec<&str> = whole.split(':').collect();
        let hour = number(parts.first()?, 2)?;
        let minute = number(parts.get(1)?, 2)?;
        let second = parts.get(2).map_or(Some(0), |s| number(s, 2))?;
        if parts.len() > 3 || hour > 23 || minute > 59 || second > 59 {
            return None;
        }
        seconds_of_day = hour * 3600 + minute * 60 + second;
        if !fraction.is_empty() {
            if fraction.len() > 6 || !fraction.bytes().all(|b| b.is_ascii_digit()) {
                return None;
            }
            micros = format!("{fraction:0<6}").parse().ok()?;
        }
        offset = match zone {
            "" | "Z" => 0,
            _ => {
                let (sign, rest) = zone.split_at(1);
                let parts: Vec<&str> = rest.split(':').collect();
                let seconds = number(parts.first()?, 2)? * 3600
                    + number(parts.get(1)?, 2)? * 60
                    + parts.get(2).map_or(Some(0), |s| number(s, 2))?;
                if sign == "-" { -seconds } else { seconds }
            }
        };
    }
    let seconds = days * 86_400 + seconds_of_day - offset;
    Some(Moment::from_micros(seconds * 1_000_000 + micros))
}

fn number(text: &str, digits: usize) -> Option<i64> {
    (text.len() == digits && text.bytes().all(|b| b.is_ascii_digit())).then(|| text.parse().ok()).flatten()
}

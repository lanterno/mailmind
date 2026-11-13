//! What a person has set for how MailMind looks and behaves: the "preferences" block of mailmind.config.json, beside
//! the accounts (`mailmind.accounts.domain.preferences`).
//!
//! A name the block doesn't hold, or holds something that can't be that preference (a hand edit), is that
//! preference's default, so a file with no block goes on exactly as before and reading one writes nothing. A change
//! names only what changes and leaves the rest of the file as it stands. The AI's daily limit is one more setting, but
//! the file keeps it at the top level, as it always did ("ai_daily_limit_usd"), where the Assistant reads it.
//!
//! A value is JSON as it parsed, read as Python reads it: an integer is a number written without a fraction or an
//! exponent, and true is never a number.

use mm_kernel::errors::Problem;
use serde_json::{Map, Number, Value};

/// The Undo Send window (`kernel.send_window`), in seconds: its default, and the shortest and longest it may be.
pub const SEND_WINDOW: (i64, i64, i64) = (10, 5, 60);
/// At the file's top level, not in the block.
pub const AI_DAILY_LIMIT: &str = "ai_daily_limit_usd";
/// Dollars: past this is a slip of the keyboard, not a budget.
pub const AI_DAILY_LIMIT_MAX: f64 = 1000.0;
/// What the Assistant spends a day when the file names no limit it can read (decision D7).
pub const DEFAULT_DAILY_LIMIT_USD: f64 = 15.0;
const BLOCK: &str = "preferences";

/// What a preference is, and its default.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    /// A number of seconds in the Undo Send window.
    Seconds(i64),
    /// One of a closed set of words.
    Choice(&'static str, &'static [&'static str]),
    Flag(bool),
    /// A time of day, `HH:MM`.
    Time(&'static str),
    /// Dollars, as the AI's daily limit is: no default here, since the Assistant has its own.
    Dollars,
}

/// Every preference the block may hold, in the order the page is told them, and what it is with nothing said.
const DEFAULTS: [(&str, Kind); 21] = [
    ("undo_send_seconds", Kind::Seconds(SEND_WINDOW.0)),
    ("density", Kind::Choice("comfortable", &["comfortable", "cozy", "compact"])),
    ("high_contrast", Kind::Flag(false)),
    ("theme", Kind::Choice("day", &["day", "dusk", "night"])),
    ("read_width", Kind::Choice("medium", &["narrow", "medium", "wide"])),
    ("read_size", Kind::Choice("m", &["s", "m", "l", "xl"])),
    ("read_font", Kind::Choice("sans", &["sans", "serif", "mono"])),
    ("mail_colors", Kind::Choice("auto", &["auto", "dark", "original"])),
    ("single_keys", Kind::Flag(true)),
    ("conversations", Kind::Flag(true)),
    ("search_scope", Kind::Choice("all", &["all", "account"])),
    ("draft_tone", Kind::Choice("balanced", &["brief", "balanced", "warm", "formal"])),
    ("notify_new_mail", Kind::Flag(true)),
    ("notify_meetings", Kind::Flag(true)),
    ("quiet_hours", Kind::Flag(false)),
    ("quiet_from", Kind::Time("22:00")),
    ("quiet_until", Kind::Time("07:00")),
    // the first-run guide has been offered, here and in any browser from now on
    ("guide_offered", Kind::Flag(false)),
    ("morning_brief", Kind::Flag(false)),
    // the week's review written ahead on Monday morning, and read all week
    ("weekly_review", Kind::Flag(false)),
    ("auto_tidy", Kind::Flag(false)),
];

/// The names a change may name and what each is, in the order a request's schema declares them: every preference,
/// then the AI's daily limit. A change is checked, and written, in this order.
pub fn changeable() -> impl Iterator<Item = (&'static str, Kind)> {
    DEFAULTS.iter().copied().chain([(AI_DAILY_LIMIT, Kind::Dollars)])
}

fn default_of(name: &str) -> Option<Kind> {
    DEFAULTS.iter().find(|(n, _)| *n == name).map(|(_, d)| *d)
}

/// What a value of `name` that can't be one is told.
fn refusal(name: &str) -> String {
    match name {
        "undo_send_seconds" => {
            format!("the Undo Send window must be {} to {} seconds", SEND_WINDOW.1, SEND_WINDOW.2)
        }
        AI_DAILY_LIMIT => format!("the AI daily limit must be more than $0 and at most ${AI_DAILY_LIMIT_MAX:.0}"),
        "quiet_from" => "quiet hours need a time such as 22:00".to_owned(),
        "quiet_until" => "quiet hours need a time such as 07:00".to_owned(),
        _ => format!("{name} can't be that"),
    }
}

/// An integer as Python's `json` reads one: written with neither a fraction nor an exponent.
fn integer(value: &Value) -> Option<i64> {
    match value {
        Value::Number(n) if !n.is_f64() => n.as_i64().or_else(|| n.as_u64().map(|_| i64::MAX)),
        _ => None,
    }
}

/// `HH:MM`, from 00:00 to 23:59.
fn is_clock(text: &str) -> bool {
    let b = text.as_bytes();
    b.len() == 5
        && b[2] == b':'
        && matches!((b[0], b[1]), (b'0' | b'1', b'0'..=b'9') | (b'2', b'0'..=b'3'))
        && matches!(b[3], b'0'..=b'5')
        && b[4].is_ascii_digit()
}

/// Whether `value` can be the preference `name`, as a request or a file edited by hand may say it.
fn takes(name: &str, value: &Value) -> bool {
    if name == AI_DAILY_LIMIT {
        return match value {
            Value::Number(n) => n.as_f64().is_some_and(|v| v.is_finite() && v > 0.0 && v <= AI_DAILY_LIMIT_MAX),
            _ => false,
        };
    }
    match default_of(name) {
        Some(Kind::Seconds(_)) => integer(value).is_some_and(|v| (SEND_WINDOW.1..=SEND_WINDOW.2).contains(&v)),
        Some(Kind::Choice(_, choices)) => value.as_str().is_some_and(|v| choices.contains(&v)),
        Some(Kind::Time(_)) => value.as_str().is_some_and(is_clock),
        Some(Kind::Flag(_)) => value.is_boolean(),
        Some(Kind::Dollars) | None => false,
    }
}

fn block_of(raw: &Map<String, Value>) -> Option<&Map<String, Value>> {
    raw.get(BLOCK).and_then(Value::as_object)
}

/// What the file says of one preference, or None where it says nothing that preference can be.
pub fn said<'a>(raw: &'a Map<String, Value>, name: &str) -> Option<&'a Value> {
    block_of(raw).and_then(|block| block.get(name)).filter(|value| takes(name, value))
}

/// Every preference as it stands: what the file says where that can be it, and else the default. It never fails.
pub fn read(raw: &Map<String, Value>) -> Map<String, Value> {
    DEFAULTS
        .iter()
        .map(|(name, default)| {
            let value = said(raw, name).cloned().unwrap_or_else(|| match default {
                Kind::Seconds(seconds) => Value::from(*seconds),
                Kind::Choice(word, _) | Kind::Time(word) => Value::from(*word),
                Kind::Flag(flag) => Value::Bool(*flag),
                Kind::Dollars => Value::Null,
            });
            ((*name).to_owned(), value)
        })
        .collect()
}

/// Of what a person asked, what is a preference, or why not: one value no preference can be refuses the whole
/// request, so a change is all or nothing. A name that is no preference is ignored, as a request's other unknown
/// names are. `asked` is in the order `changeable` names them.
pub fn changes(asked: &Map<String, Value>) -> Result<Map<String, Value>, Problem> {
    let mut wanted = Map::new();
    for (name, value) in asked {
        if name != AI_DAILY_LIMIT && default_of(name).is_none() {
            continue;
        }
        if !takes(name, value) {
            return Err(Problem::refused(refusal(name)));
        }
        wanted.insert(name.clone(), value.clone());
    }
    Ok(wanted)
}

/// Of what a browser held under its old keys, what can be a preference and isn't one yet. What the file already
/// says stands: the first browser to bring a value in sets it, and the next can't move it.
pub fn importable(held: &Map<String, Value>, raw: &Map<String, Value>) -> Map<String, Value> {
    held.iter()
        .filter(|(name, value)| default_of(name).is_some() && takes(name, value) && said(raw, name).is_none())
        .map(|(name, value)| (name.clone(), value.clone()))
        .collect()
}

/// Write `wanted` into the file's contents, and nothing else. The block comes to be only when a preference goes in
/// it.
pub fn apply(raw: &mut Map<String, Value>, wanted: &Map<String, Value>) {
    for (name, value) in wanted {
        if name == AI_DAILY_LIMIT {
            raw.insert(name.clone(), value.clone());
            continue;
        }
        if !raw.get(BLOCK).is_some_and(Value::is_object) {
            raw.insert(BLOCK.to_owned(), Value::Object(Map::new()));
        }
        if let Some(Value::Object(block)) = raw.get_mut(BLOCK) {
            block.insert(name.clone(), value.clone());
        }
    }
}

/// The AI's daily limit in dollars as the Assistant reads the file's "ai_daily_limit_usd" (`assistant.domain.limits`
/// and `bootstrap.assistant.limit_micros`): what Python's `float()` makes of it, when that is positive and finite,
/// else the default; to the micro-dollar, rounded half to even.
pub fn daily_limit_usd(raw: &Map<String, Value>) -> f64 {
    let read = match raw.get(AI_DAILY_LIMIT) {
        Some(Value::Number(n)) => n.as_f64(),
        Some(Value::Bool(b)) => Some(if *b { 1.0 } else { 0.0 }),
        Some(Value::String(s)) => python_float(s),
        _ => None,
    };
    let dollars = read.filter(|v| v.is_finite() && *v > 0.0).unwrap_or(DEFAULT_DAILY_LIMIT_USD);
    (dollars * 1_000_000.0).round_ties_even() / 1_000_000.0
}

/// `float(text)`: surrounding whitespace dropped, an underscore allowed only between two digits.
fn python_float(text: &str) -> Option<f64> {
    let text = mm_kernel::text::strip(text);
    let b = text.as_bytes();
    let underscores_fit = b.iter().enumerate().all(|(i, c)| {
        *c != b'_' || (i > 0 && b[i - 1].is_ascii_digit() && b.get(i + 1).is_some_and(u8::is_ascii_digit))
    });
    if !underscores_fit {
        return None;
    }
    text.replace('_', "").parse::<f64>().ok()
}

/// A number a request gave in dollars, as a float, as the schema's `Dollars` makes it.
pub fn dollars(value: f64) -> Value {
    Number::from_f64(value).map_or(Value::Null, Value::Number)
}

//! Where the page is, kept out of the browser (`mailmind.platform.db.places`): a place is one row of `places`
//! (migration 49), a route the page would otherwise write into its URL, under a random id the URL names instead,
//! `/p/<id>`.
//!
//! The page makes each id itself, so going somewhere waits for nothing, and sends the places it made here in a batch
//! (`POST /api/places`). Opening `/p/<id>` reads the route back into the page. What a route holds is the page's to
//! read: here it is a JSON object of at most `MOST_BYTES`, kept as it came. Only the `KEPT` used last stay, and a place
//! pruned opens as the app's home.

use std::sync::Arc;

use mm_kernel::errors::{Failure, Fault, Problem};
use mm_kernel::seams::Clock;
use mm_kernel::when;
use serde_json::Value;

use crate::db::Database;

/// A route's JSON, as kept.
pub const MOST_BYTES: usize = 4096;
/// The places one request names: the page sends more as more requests.
pub const MOST_AT_ONCE: usize = 64;
/// The places used last, kept.
pub const KEPT: i64 = 10_000;

/// What the page makes: 16 random bytes, base64url, so 16 to 32 letters, digits, `-` or `_`.
pub fn is_id(place: &str) -> bool {
    (16..=32).contains(&place.len()) && place.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
}

/// A route's JSON as kept: compact, and a person's own words as they are (`json.dumps(route, ensure_ascii=False,
/// separators=(",", ":"))`).
pub fn text_of(route: &Value) -> String {
    let mut out = String::new();
    write(&mut out, route);
    out
}

fn write(out: &mut String, value: &Value) {
    match value {
        Value::Null => out.push_str("null"),
        Value::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
        Value::Number(n) => match n.as_f64().filter(|_| n.is_f64()) {
            Some(f) => out.push_str(&float_repr(f)),
            None => out.push_str(&n.to_string()),
        },
        Value::String(s) => write_text(out, s),
        Value::Array(items) => {
            out.push('[');
            for (i, item) in items.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                write(out, item);
            }
            out.push(']');
        }
        Value::Object(members) => {
            out.push('{');
            for (i, (key, item)) in members.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                write_text(out, key);
                out.push(':');
                write(out, item);
            }
            out.push('}');
        }
    }
}

/// Text as `json.dumps` writes it without `ensure_ascii`: only the quote, the backslash and the controls escaped.
fn write_text(out: &mut String, text: &str) {
    out.push('"');
    for c in text.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{08}' => out.push_str("\\b"),
            '\u{0c}' => out.push_str("\\f"),
            '\u{00}'..='\u{1f}' => out.push_str(&format!("\\u{:04x}", u32::from(c))),
            _ => out.push(c),
        }
    }
    out.push('"');
}

/// The shortest text that reads back as `f`, an exponent past 1e16 or under 1e-4 written with its sign and two digits
/// at least, as Python's `repr` writes it.
fn float_repr(f: f64) -> String {
    let debug = format!("{f:?}");
    let Some((mantissa, exponent)) = debug.split_once('e') else {
        return debug;
    };
    let (sign, digits) = match exponent.strip_prefix('-') {
        Some(digits) => ('-', digits),
        None => ('+', exponent),
    };
    format!("{mantissa}e{sign}{digits:0>2}")
}

/// The places, in the database, each marked with when it was last used.
#[derive(Debug)]
pub struct Places {
    database: Arc<Database>,
    clock: Arc<dyn Clock>,
}

impl Places {
    pub fn new(database: Arc<Database>, clock: Arc<dyn Clock>) -> Places {
        Places { database, clock }
    }

    /// Keep each (id, route) given, a route sent again under its id replacing what it held, then prune to the `KEPT`
    /// used last. One that can't be kept refuses the whole batch, before anything is written.
    pub fn keep(&self, given: &[(String, Value)]) -> Result<(), Failure> {
        if given.len() > MOST_AT_ONCE {
            return Err(Problem::refused(format!("at most {MOST_AT_ONCE} places at once")).into());
        }
        let mut rows = Vec::with_capacity(given.len());
        for (place, route) in given {
            if !is_id(place) {
                return Err(Problem::refused("a place's id is 16 to 32 letters, digits, - or _").into());
            }
            if !route.is_object() {
                return Err(Problem::refused("a place's route is a JSON object").into());
            }
            let text = text_of(route);
            if text.len() > MOST_BYTES {
                return Err(Problem::refused(format!("a place's route is at most {MOST_BYTES} bytes")).into());
            }
            rows.push((place, text));
        }
        let now = when::iso(self.clock.now());
        self.database.write(|c| {
            let mut keep = c.prepare_cached(
                "INSERT INTO places(id, route, used_at) VALUES(?, ?, ?) ON CONFLICT(id) DO UPDATE SET \
                 route=excluded.route, used_at=excluded.used_at",
            )?;
            for (place, text) in &rows {
                keep.execute((place, text, &now))?;
            }
            if !rows.is_empty() {
                c.execute(
                    "DELETE FROM places WHERE id IN (SELECT id FROM places ORDER BY used_at DESC, rowid DESC \
                     LIMIT -1 OFFSET ?)",
                    [KEPT],
                )?;
            }
            Ok(())
        })?;
        Ok(())
    }

    /// The route's JSON text a place holds, marked used now, or None for an id that names none. Nothing is asked of
    /// the database for one that couldn't.
    pub fn opened(&self, place: &str) -> Result<Option<String>, Fault> {
        if !is_id(place) {
            return Ok(None);
        }
        let now = when::iso(self.clock.now());
        self.database.write(|c| {
            let route: Option<String> = match c.query_row("SELECT route FROM places WHERE id=?", [place], |r| r.get(0))
            {
                Ok(route) => Some(route),
                Err(rusqlite::Error::QueryReturnedNoRows) => None,
                Err(e) => return Err(e),
            };
            if route.is_some() {
                c.execute("UPDATE places SET used_at=? WHERE id=?", (&now, place))?;
            }
            Ok(route)
        })
    }
}

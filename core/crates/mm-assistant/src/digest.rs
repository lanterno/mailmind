//! A thread summary, with no I/O (`domain.digest` and `domain.fingerprint`): what is taken from a model's answer, each
//! part held to its cap, and what an answer was made from.
//!
//! A fingerprint names messages by their Message-IDs, never by their pks: reconcile can renumber a folder's rows, and
//! that changes nothing an answer says. A message with no Message-ID counts as "". The forms are the ones drafting
//! ahead has stored since it began ("t2:" and "m2:"), so the drafts already stored stay valid.

use serde_json::Value;

use crate::schema::braced;
use crate::text::{cut, python_str, truthy};

/// What a summary says.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Digest {
    pub tldr: String,
    pub points: Vec<String>,
    pub action: String,
    pub participants: Vec<String>,
}

/// `str(d.get(name) or "")`.
fn said(d: &serde_json::Map<String, Value>, name: &str) -> String {
    d.get(name).filter(|v| truthy(v)).map(python_str).unwrap_or_default()
}

fn listed(d: &serde_json::Map<String, Value>, name: &str, each: usize, most: usize) -> Vec<String> {
    match d.get(name) {
        Some(Value::Array(items)) => items.iter().take(most).map(|x| cut(&python_str(x), each).to_owned()).collect(),
        _ => Vec::new(),
    }
}

/// The summary a reply holds, or None when it holds no JSON object.
pub fn parse(out: &str) -> Option<Digest> {
    let Ok(Value::Object(d)) = serde_json::from_str::<Value>(braced(out)?) else { return None };
    Some(Digest {
        tldr: cut(&said(&d, "tldr"), 600).to_owned(),
        points: listed(&d, "points", 200, 6),
        action: cut(&said(&d, "action"), 200).to_owned(),
        participants: listed(&d, "participants", 80, 12),
    })
}

/// zlib's CRC-32.
fn crc32(bytes: &[u8]) -> u32 {
    let mut crc = !0u32;
    for &b in bytes {
        crc ^= u32::from(b);
        for _ in 0..8 {
            crc = if crc & 1 == 1 { (crc >> 1) ^ 0xEDB8_8320 } else { crc >> 1 };
        }
    }
    !crc
}

/// A set of things named by stable keys: Message-IDs, or the (account, thread) of a thread waiting on a reply. It
/// changes when one is added, removed or swapped for another, or when `newest_ts` moves; never when rows are
/// renumbered or come back in another order.
pub fn fingerprint<'a>(keys: impl IntoIterator<Item = Option<&'a str>>, newest_ts: i64) -> String {
    let mut names: Vec<&str> = keys.into_iter().map(Option::unwrap_or_default).collect();
    names.sort_unstable();
    format!("t2:{}:{newest_ts}:{:08x}", names.len(), crc32(names.join("\n").as_bytes()))
}

/// One message, by itself: its Message-ID, and whether its body has been fetched, since a fetched body makes for a
/// better answer.
/// A `body_fetched` that is NULL is written as Python writes None.
pub fn message_fingerprint(message_id: Option<&str>, body_fetched: Option<i64>) -> String {
    let fetched = body_fetched.map_or_else(|| "None".to_owned(), |n| n.to_string());
    format!("m2:{}:{fetched}", message_id.unwrap_or_default())
}

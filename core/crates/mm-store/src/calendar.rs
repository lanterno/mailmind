//! Calendar's view of the database (`mailmind.calendar.adapters.sqlite`). It writes nothing: invites live in stored
//! bodies, the Mailbox's, and what was sent in the outbox, Compose's. Beside it, the zone database a TZID is looked
//! up in: the system's zone files, each opened through `Outside`.

use std::collections::HashMap;
use std::fmt;
use std::path::Path;
use std::sync::{Arc, Mutex};

use mm_calendar::ports::{InviteRow, Message, StoredInvites, ZoneNames};
use mm_calendar::publish::EventMessages;
use mm_kernel::errors::Fault;
use mm_kernel::seams::Outside;
use mm_kernel::zone::Zone;
use rusqlite::{Connection, OptionalExtension, params};

use crate::Database;

/// Every calendar part of mail whose body is stored, newest first.
const INVITE_PARTS: &str = "SELECT m.pk, m.message_key, m.uid, m.account, m.folder, m.thread_id, a.part_id \
     FROM attachments a JOIN messages m ON m.pk=a.message_pk \
     WHERE m.body_fetched=1 AND m.ts>=? AND m.pending_removal IS NULL \
       AND (lower(a.content_type) LIKE '%calendar%' OR lower(a.filename) LIKE '%.ics') \
     ORDER BY m.ts DESC, m.pk DESC";

/// The text of part `part_id` of the body stored for `pk`, None where there is none to read.
fn part_text(c: &Connection, pk: i64, part_id: Option<&str>) -> rusqlite::Result<Option<String>> {
    let Some(part_id) = part_id.and_then(|p| p.parse::<u32>().ok()) else { return Ok(None) };
    let raw: Option<Vec<u8>> = c
        .prepare_cached("SELECT raw FROM bodies WHERE message_pk=?")?
        .query_row([pk], |r| r.get::<_, Option<Vec<u8>>>(0))
        .optional()?
        .flatten();
    let raw = raw.unwrap_or_default();
    Ok(mm_content::mime::extract_part(&raw, part_id).map(|(payload, _)| String::from_utf8_lossy(&payload).into_owned()))
}

impl StoredInvites for Database {
    /// One body at a time: they can be large, and only the calendar part of each is kept.
    fn invite_parts(&self, since: i64, skipped: &dyn Fn(&str) -> bool) -> Result<Vec<(InviteRow, String)>, Fault> {
        self.read(|c| {
            let mut statement = c.prepare_cached(INVITE_PARTS)?;
            let rows = statement
                .query_map([since], |r| {
                    let row = InviteRow {
                        pk: r.get(0)?,
                        key: r.get(1)?,
                        uid: r.get(2)?,
                        account: r.get::<_, Option<String>>(3)?.unwrap_or_default(),
                        folder: r.get::<_, Option<String>>(4)?.unwrap_or_default(),
                        thread_id: r.get(5)?,
                    };
                    Ok((row, r.get::<_, Option<String>>(6)?))
                })?
                .collect::<rusqlite::Result<Vec<_>>>()?;
            let mut found = Vec::new();
            for (row, part_id) in rows {
                if skipped(&row.folder) {
                    continue;
                }
                if let Some(text) = part_text(c, row.pk, part_id.as_deref())? {
                    found.push((row, text));
                }
            }
            Ok(found)
        })
    }

    fn keys_sent(&self, prefix: &str) -> Result<Vec<String>, Fault> {
        let length = i64::try_from(prefix.chars().count()).unwrap_or(i64::MAX);
        self.read(|c| {
            c.prepare_cached(
                "SELECT idempotency_key FROM outbox WHERE substr(idempotency_key, 1, ?) = ? ORDER BY id DESC",
            )?
            .query_map(params![length, prefix], |r| r.get::<_, String>(0))?
            .collect()
        })
    }
}

/// The message `pk` names, as an answer to an invite or an event file reads it.
pub fn message(c: &Connection, pk: i64) -> rusqlite::Result<Option<Message>> {
    c.prepare_cached("SELECT pk, message_key, account FROM messages WHERE pk=?")?
        .query_row([pk], |r| {
            Ok(Message { pk: r.get(0)?, key: r.get(1)?, account: r.get::<_, Option<String>>(2)?.unwrap_or_default() })
        })
        .optional()
}

/// What threads an answer under message `pk`: its Message-ID, References and In-Reply-To, as stored; None where no
/// message is `pk`.
pub fn threading(c: &Connection, pk: i64) -> rusqlite::Result<Option<[Option<String>; 3]>> {
    c.prepare_cached("SELECT message_id, refs, in_reply_to FROM messages WHERE pk=?")?
        .query_row([pk], |r| Ok([r.get(0)?, r.get(1)?, r.get(2)?]))
        .optional()
}

impl EventMessages for Database {
    fn message(&self, pk: i64) -> Result<Option<Message>, Fault> {
        self.read(|c| message(c, pk))
    }
}

/// Whether `name` is a key `ZoneInfo` would look up: a path under the zone files, as `os.path.normpath` leaves it.
fn is_key(name: &str) -> bool {
    !name.is_empty()
        && !name.starts_with('/')
        && !name.contains('\0')
        && name.split('/').all(|part| !matches!(part, "" | "." | ".."))
}

/// The zone database: what `ZoneInfo(name)` finds among the system's zone files. A zone once read is kept, as
/// `ZoneInfo`'s cache keeps it; a name that names none is asked again, so a stranger's names fill nothing.
pub struct SystemZones {
    outside: Arc<dyn Outside>,
    read: Mutex<HashMap<String, Arc<dyn Zone>>>,
}

impl fmt::Debug for SystemZones {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("SystemZones(..)")
    }
}

impl SystemZones {
    pub fn new(outside: Arc<dyn Outside>) -> SystemZones {
        SystemZones { outside, read: Mutex::new(HashMap::new()) }
    }
}

impl ZoneNames for SystemZones {
    fn zone(&self, name: &str) -> Option<Arc<dyn Zone>> {
        if !is_key(name) {
            return None;
        }
        if let Some(zone) = self.read.lock().ok().and_then(|read| read.get(name).cloned()) {
            return Some(zone);
        }
        let zone: Arc<dyn Zone> =
            Arc::new(crate::zone_file::read(&Path::new(crate::zone_file::ZONEINFO).join(name), &*self.outside)?);
        if let Ok(mut read) = self.read.lock() {
            read.insert(name.to_owned(), zone.clone());
        }
        Some(zone)
    }
}

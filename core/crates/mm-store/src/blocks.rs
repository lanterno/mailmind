//! What Discovery's, Curation's and Attention's adapters share: a write block (`UnitOfWork.write`), the documents a
//! context keeps whole (`platform.db.documents`) and the undo stack (`platform.undo`).

use mm_kernel::errors::Fault;
use rusqlite::{Connection, OptionalExtension, Transaction, TransactionBehavior};
use serde_json::Value;

use crate::db::Database;

/// Run `work` in one write block: `BEGIN IMMEDIATE`, committed if it ends and rolled back if it fails, so a change
/// reads, changes and writes with no other write between. Nothing in it may wait on a network.
pub fn write<T>(db: &Database, work: impl FnOnce(&Connection) -> rusqlite::Result<T>) -> Result<T, Fault> {
    db.read(|c| {
        let block = Transaction::new_unchecked(c, TransactionBehavior::Immediate)?;
        let done = work(&block)?;
        block.commit()?;
        Ok(done)
    })
}

/// A document's text, None when there is none yet.
pub fn document_text(c: &Connection, name: &str) -> rusqlite::Result<Option<String>> {
    c.query_row("SELECT body FROM documents WHERE name=?", [name], |r| r.get(0)).optional()
}

/// What a document holds as JSON parses it: null when there is none yet, or its text won't parse.
pub fn document(c: &Connection, name: &str) -> rusqlite::Result<Value> {
    Ok(document_text(c, name)?.and_then(|text| serde_json::from_str(&text).ok()).unwrap_or(Value::Null))
}

/// `value` as the whole document, a version on from the last, written as `json.dumps(indent=2,
/// ensure_ascii=False)` wrote it.
pub fn write_document(c: &Connection, name: &str, value: &Value, now_iso: &str) -> rusqlite::Result<()> {
    let body = serde_json::to_string_pretty(value).unwrap_or_else(|_| "null".to_owned());
    c.execute(
        "INSERT INTO documents(name, body, changed_at) VALUES(?, ?, ?) ON CONFLICT(name) DO UPDATE SET \
         body=excluded.body, version=documents.version+1, changed_at=excluded.changed_at",
        rusqlite::params![name, body, now_iso],
    )?;
    Ok(())
}

/// How many entries the undo stack keeps; a group counts as one.
pub const UNDO_MAX: i64 = 50;
/// How long an entry may still be undone, in seconds.
const KEEP_FOR: i64 = 24 * 3600;

/// Put `entry` on top of the undo stack, or, when the top gathers `group`, add it to that group's children; then
/// keep only the newest `UNDO_MAX`, none older than a day (`platform.undo.push`). `now` is in seconds since the
/// epoch, and `iso` writes a moment as the stack's times are written.
pub fn push_undo(
    c: &Connection,
    entry: &Value,
    group: Option<&str>,
    now: i64,
    iso: impl Fn(i64) -> String,
) -> rusqlite::Result<()> {
    let now_iso = iso(now);
    c.execute("DELETE FROM undo_entries WHERE created_at < ?", [iso(now - KEEP_FOR)])?;
    let top: Option<(i64, Option<String>, String)> = c
        .query_row("SELECT id, grp, entry FROM undo_entries ORDER BY id DESC LIMIT 1", [], |r| {
            Ok((r.get(0)?, r.get(1)?, r.get(2)?))
        })
        .optional()?;
    match (group.filter(|g| !g.is_empty()), top) {
        (Some(group), Some((id, Some(grp), held))) if grp == group => {
            let mut held: Value = serde_json::from_str(&held).unwrap_or(Value::Null);
            if let Some(children) = held.get_mut("children").and_then(Value::as_array_mut) {
                children.push(entry.clone());
            }
            c.execute("UPDATE undo_entries SET entry=? WHERE id=?", rusqlite::params![held.to_string(), id])?;
        }
        (Some(group), _) => {
            let label = entry.get("label").cloned().unwrap_or_else(|| Value::from("Updated"));
            let bulk = serde_json::json!({"kind": "bulk", "group": group, "label": label, "children": [entry]});
            c.execute(
                "INSERT INTO undo_entries(grp, entry, created_at) VALUES(?,?,?)",
                rusqlite::params![group, bulk.to_string(), now_iso],
            )?;
        }
        (None, _) => {
            c.execute(
                "INSERT INTO undo_entries(grp, entry, created_at) VALUES(NULL,?,?)",
                rusqlite::params![entry.to_string(), now_iso],
            )?;
        }
    }
    c.execute(
        "DELETE FROM undo_entries WHERE id NOT IN (SELECT id FROM undo_entries ORDER BY id DESC LIMIT ?)",
        [UNDO_MAX],
    )?;
    Ok(())
}

/// The undo stack, oldest first, none older than a day (`platform.undo.entries`).
pub fn undo_entries(c: &Connection, oldest_kept_iso: &str) -> rusqlite::Result<Vec<Value>> {
    c.prepare_cached("SELECT entry FROM undo_entries WHERE created_at >= ? ORDER BY id")?
        .query_map([oldest_kept_iso], |r| r.get::<_, String>(0))?
        .map(|text| text.map(|t| serde_json::from_str(&t).unwrap_or(Value::Null)))
        .collect()
}

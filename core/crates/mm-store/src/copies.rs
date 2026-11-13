//! What a request names a message by, as the row it is now (`mailmind.mailbox.adapters.sqlite.messages`'s `copy_pk`
//! and `message_pk`, which `mailmind.http.named` asks). A pk is not stable, so the page never holds one: it names a
//! copy by the message's key, its folder and its uid there, looked up as each request comes.

use rusqlite::{Connection, OptionalExtension};

/// The pk the copy of message `key` in `folder` has now, or None. A uid says which: the row of that key in that
/// folder at that uid, and none if it isn't there, since two rows in one folder can share a key. Without one, the only
/// copy there, and none where there are two, rather than a guess.
pub fn copy_pk(c: &Connection, key: i64, folder: &str, uid: Option<i64>) -> rusqlite::Result<Option<i64>> {
    if let Some(uid) = uid {
        return c
            .prepare_cached("SELECT pk FROM messages WHERE message_key=? AND folder=? AND uid=?")?
            .query_row(rusqlite::params![key, folder, uid], |r| r.get(0))
            .optional();
    }
    let pks: Vec<i64> = c
        .prepare_cached("SELECT pk FROM messages WHERE message_key=? AND folder=? LIMIT 2")?
        .query_map(rusqlite::params![key, folder], |r| r.get(0))?
        .collect::<rusqlite::Result<_>>()?;
    Ok(if pks.len() == 1 { pks.first().copied() } else { None })
}

/// The pk of a copy of message `key` now, or None if it has none: the copy at `uid` in `folder` where that is one of
/// its copies, else its copy in `folder` if there is one, else another, one listed first, then the newer.
pub fn message_pk(c: &Connection, key: i64, folder: Option<&str>, uid: Option<i64>) -> rusqlite::Result<Option<i64>> {
    if let (Some(uid), Some(folder)) = (uid, folder)
        && let Some(pk) = copy_pk(c, key, folder, Some(uid))?
    {
        return Ok(Some(pk));
    }
    c.prepare_cached(
        "SELECT pk FROM messages WHERE message_key=? \
         ORDER BY folder IS ? DESC, pending_removal IS NULL DESC, pk DESC LIMIT 1",
    )?
    .query_row(rusqlite::params![key, folder], |r| r.get(0))
    .optional()
}

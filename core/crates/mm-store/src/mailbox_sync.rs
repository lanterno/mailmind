//! What a sync writes (`mm_mailbox::sync_ports::SyncStore`): the Python app's `mailbox.adapters.sqlite` messages,
//! flags, contents, sync_state and threads, the search index's row for each message (`discovery.adapters.fts`), and
//! what a sync's end asks of Attention (`on_ingest`, `resolve_followups`) and of whoever keeps state under a thread id
//! when two conversations merge (`rekey_thread`).
//!
//! Each call is one write transaction, taken with BEGIN IMMEDIATE so what it reads decides what it writes.

use std::collections::{BTreeMap, BTreeSet, HashSet};

use mm_kernel::errors::Fault;
use mm_kernel::identity;
use mm_mailbox::sync_plan::COLUMNS;
use mm_mailbox::sync_ports::{FlagRow, FolderState, Incoming, SyncAccount, SyncStore};
use mm_mailbox::threading::{StoredCopy, Threader};
use rusqlite::{Connection, OptionalExtension, params, params_from_iter};

use crate::db::Database;
use crate::placeholders;

/// How many rows of who a message went to are kept: a forged To can be as long as it likes.
const RECIPIENT_ROWS_MAX: usize = 5000;
/// How many of Gmail's labels are kept of one copy.
const LABEL_ROWS_MAX: usize = 1000;
/// How many values one IN names.
const SLICE: usize = 500;
const THREADING: &str = "pk, message_id, gmail_thread, in_reply_to, refs, thread_id, folder, ts";

impl Database {
    /// `work` in one write transaction: committed when it answers, rolled back when it fails.
    pub(crate) fn write<T>(&self, work: impl FnOnce(&Connection) -> rusqlite::Result<T>) -> Result<T, Fault> {
        self.read(|c| {
            c.execute_batch("BEGIN IMMEDIATE")?;
            match work(c) {
                Ok(done) => {
                    c.execute_batch("COMMIT")?;
                    Ok(done)
                }
                Err(e) => {
                    let _ = c.execute_batch("ROLLBACK");
                    Err(e)
                }
            }
        })
    }
}

/// (Re)index one message for search from its columns (`fts.index_message`).
pub(crate) fn index_message(c: &Connection, pk: i64) -> rusqlite::Result<()> {
    let row: Option<[Option<String>; 6]> = c
        .prepare_cached(
            "SELECT subject, from_name, from_addr, recipients, snippet, body_text FROM messages WHERE pk=?",
        )?
        .query_row([pk], |r| Ok([r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?, r.get(5)?]))
        .optional()?;
    let Some(row) = row else { return Ok(()) };
    c.prepare_cached("DELETE FROM msg_fts WHERE rowid=?")?.execute([pk])?;
    let [a, b, d, e, f, g] = row.map(Option::unwrap_or_default);
    c.prepare_cached(
        "INSERT INTO msg_fts(rowid, subject, from_name, from_addr, recipients, snippet, body_text) \
         VALUES(?,?,?,?,?,?,?)",
    )?
    .execute(params![pk, a, b, d, e, f, g])?;
    Ok(())
}

/// A message's row goes, its search row first, and its identity is gone once no copy of it is left
/// (`delete_message`).
pub(crate) fn delete_message(c: &Connection, pk: i64, now: i64) -> rusqlite::Result<()> {
    let key: Option<Option<i64>> =
        c.query_row("SELECT message_key FROM messages WHERE pk=?", [pk], |r| r.get(0)).optional()?;
    c.prepare_cached("DELETE FROM msg_fts WHERE rowid=?")?.execute([pk])?;
    c.prepare_cached("DELETE FROM messages WHERE pk=?")?.execute([pk])?;
    if let Some(Some(key)) = key {
        c.execute(
            "UPDATE message_identity SET gone_at=? WHERE key=? AND NOT EXISTS (SELECT 1 FROM messages WHERE message_key=?)",
            params![now, key, key],
        )?;
    }
    Ok(())
}

/// The message's key, minted the first time any copy of it is seen (`mint_key`).
pub(crate) fn mint_key(
    c: &Connection,
    account: &str,
    message_id: &str,
    from_addr: &str,
    date_iso: Option<&str>,
    subject: &str,
) -> rusqlite::Result<i64> {
    let who = identity::of(Some(message_id), Some(from_addr), date_iso, Some(subject));
    c.prepare_cached("INSERT OR IGNORE INTO message_identity(account, identity) VALUES(?,?)")?
        .execute(params![account, who])?;
    c.prepare_cached("SELECT key FROM message_identity WHERE account=? AND identity=?")?
        .query_row(params![account, who], |r| r.get(0))
}

fn upsert(c: &Connection, account: &SyncAccount, folder: &str, m: &Incoming) -> rusqlite::Result<i64> {
    let f = &m.fields;
    let key = mint_key(c, &account.email, &f.message_id, &f.from_addr, f.date_iso.as_deref(), &f.subject)?;
    c.prepare_cached("UPDATE message_identity SET gone_at=NULL WHERE key=? AND gone_at IS NOT NULL")?.execute([key])?;
    c.prepare_cached(
        "INSERT INTO messages (account,kind,folder,uid,message_id,gmail_thread,from_addr,from_name,subject,date_iso,ts,\
         snippet,bucket,confidence,reason,is_junk,message_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) \
         ON CONFLICT(account,folder,uid) DO UPDATE SET subject=excluded.subject, \
         snippet=COALESCE(NULLIF(excluded.snippet, ''), snippet), bucket=excluded.bucket, \
         confidence=excluded.confidence, reason=excluded.reason, is_junk=excluded.is_junk, \
         message_key=COALESCE(message_key, excluded.message_key) RETURNING pk",
    )?
    .query_row(
        params![
            account.email,
            account.kind,
            folder,
            m.uid,
            f.message_id,
            m.gmail_thread,
            f.from_addr,
            f.from_name,
            f.subject,
            f.date_iso,
            f.ts,
            m.snippet,
            m.verdict.bucket,
            m.verdict.confidence,
            m.verdict.reason,
            i64::from(m.verdict.is_junk),
            key
        ],
        |r| r.get(0),
    )
}

fn set_flags(c: &Connection, pk: i64, flags: &[i64; 5]) -> rusqlite::Result<()> {
    c.prepare_cached("UPDATE messages SET seen=?, flagged=?, answered=?, draft=?, deleted=? WHERE pk=?")?
        .execute(params![flags[0], flags[1], flags[2], flags[3], flags[4], pk])?;
    Ok(())
}

/// Whether a copy about to hold these references has anything for threading to do (`needs_threading`).
fn needs_threading(c: &Connection, pk: i64, in_reply_to: &str, refs: &str) -> rusqlite::Result<bool> {
    let row: Option<(Option<String>, Option<String>, Option<String>)> = c
        .prepare_cached("SELECT thread_id, in_reply_to, refs FROM messages WHERE pk=?")?
        .query_row([pk], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))
        .optional()?;
    Ok(match row {
        None => true,
        Some((thread_id, held_reply, held_refs)) => {
            thread_id.is_none_or(|t| t.is_empty())
                || (held_reply.unwrap_or_default(), held_refs.unwrap_or_default())
                    != (in_reply_to.to_owned(), refs.to_owned())
        }
    })
}

fn copy_of(r: &rusqlite::Row<'_>) -> rusqlite::Result<StoredCopy> {
    Ok(StoredCopy {
        pk: r.get(0)?,
        message_id: r.get(1)?,
        gmail_thread: r.get(2)?,
        in_reply_to: r.get(3)?,
        refs: r.get(4)?,
        thread_id: r.get(5)?,
        folder: r.get(6)?,
        ts: r.get(7)?,
    })
}

fn slices<T: Clone + Ord>(values: impl IntoIterator<Item = T>) -> Vec<Vec<T>> {
    let mut sorted: Vec<T> = values.into_iter().collect();
    sorted.sort();
    sorted.chunks(SLICE).map(<[T]>::to_vec).collect()
}

/// Give what a sync stored its thread id (`thread_stored`): the copies named, and any of the account's with no id
/// yet. A merge moves what other contexts keep under the absorbed id.
fn thread_stored(c: &Connection, account: &str, pks: &[i64]) -> rusqlite::Result<()> {
    let unthreaded = c
        .prepare_cached(
            "SELECT 1 FROM messages INDEXED BY idx_msg_thread WHERE thread_id IS NULL AND account=? LIMIT 1",
        )?
        .exists([account])?;
    if pks.is_empty() && !unthreaded {
        return Ok(());
    }
    let mut arrivals: BTreeMap<i64, StoredCopy> = BTreeMap::new();
    let sql =
        format!("SELECT {THREADING} FROM messages INDEXED BY idx_msg_thread WHERE thread_id IS NULL AND account=?");
    for copy in c.prepare(&sql)?.query_map([account], copy_of)? {
        let copy = copy?;
        arrivals.insert(copy.pk, copy);
    }
    for part in slices(pks.iter().copied()) {
        let sql = format!("SELECT {THREADING} FROM messages WHERE pk IN ({}) AND account=?", placeholders(part.len()));
        let mut values: Vec<rusqlite::types::Value> = part.iter().map(|pk| (*pk).into()).collect();
        values.push(account.to_owned().into());
        for copy in c.prepare(&sql)?.query_map(params_from_iter(values), copy_of)? {
            let copy = copy?;
            arrivals.insert(copy.pk, copy);
        }
    }
    let arrivals: Vec<StoredCopy> = arrivals.into_values().collect();
    let names: BTreeSet<String> = arrivals.iter().flat_map(|m| m.names()).map(|n| n.into_owned()).collect();
    let mut touching: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    for part in slices(names) {
        let sql = format!(
            "SELECT DISTINCT l.name, m.thread_id FROM thread_links l CROSS JOIN messages m ON m.pk=l.message_pk \
             WHERE l.name IN ({}) AND m.account=? AND m.thread_id IS NOT NULL",
            placeholders(part.len())
        );
        let mut values: Vec<String> = part;
        values.push(account.to_owned());
        let mut statement = c.prepare(&sql)?;
        let rows = statement.query_map(params_from_iter(values), |r| Ok((r.get::<_, String>(0)?, r.get(1)?)))?;
        for row in rows {
            let (name, thread_id) = row?;
            touching.entry(name).or_default().insert(thread_id);
        }
    }
    let touching: Vec<(String, Vec<String>)> =
        touching.into_iter().map(|(n, t)| (n, t.into_iter().collect())).collect();
    let threader = Threader::new(account);
    let conversations = threader.conversations(&arrivals, &touching);
    let mut members = Vec::new();
    for part in slices(threader.contested(&conversations)) {
        let sql = format!(
            "SELECT {THREADING} FROM messages INDEXED BY idx_msg_thread WHERE thread_id IN ({}) AND account=?",
            placeholders(part.len())
        );
        let mut values = part;
        values.push(account.to_owned());
        for copy in c.prepare(&sql)?.query_map(params_from_iter(values), copy_of)? {
            members.push(copy?);
        }
    }
    let placed = threader.place(&conversations, &members);
    let given: BTreeMap<i64, String> = placed.given.into_iter().collect();
    for m in &arrivals {
        if let Some(tid) = given.get(&m.pk)
            && m.thread_id.as_deref() != Some(tid.as_str())
        {
            c.prepare_cached("UPDATE messages SET thread_id=? WHERE pk=?")?.execute(params![tid, m.pk])?;
        }
    }
    for m in &arrivals {
        c.prepare_cached("DELETE FROM thread_links WHERE message_pk=?")?.execute([m.pk])?;
    }
    for m in &arrivals {
        for name in m.names() {
            c.prepare_cached("INSERT OR IGNORE INTO thread_links(name, message_pk) VALUES (?,?)")?
                .execute(params![name.as_ref(), m.pk])?;
        }
    }
    for (absorbed, survivor) in &placed.merged {
        c.execute(
            "UPDATE messages INDEXED BY idx_msg_thread SET thread_id=? WHERE thread_id=? AND account=?",
            params![survivor, absorbed, account],
        )?;
        rekey_thread(c, account, survivor, absorbed)?;
    }
    Ok(())
}

/// Thread `absorbed` merged into `survivor`: what Attention, the assistant and the outbox keep under it moves, and
/// where `survivor` has its own, that one stays (`_conversations_merged`).
fn rekey_thread(c: &Connection, account: &str, survivor: &str, absorbed: &str) -> rusqlite::Result<()> {
    let both = params![survivor, account, absorbed];
    c.execute("UPDATE OR IGNORE muted SET thread_id=? WHERE account=? AND thread_id=?", both)?;
    c.execute("DELETE FROM muted WHERE account=? AND thread_id=?", params![account, absorbed])?;
    c.execute("UPDATE OR IGNORE followups SET thread_id=? WHERE account=? AND thread_id=?", both)?;
    c.execute("DELETE FROM followups WHERE account=? AND thread_id=?", params![account, absorbed])?;
    c.execute("UPDATE OR IGNORE thread_summaries SET thread_id=? WHERE account=? AND thread_id=?", both)?;
    c.execute("DELETE FROM thread_summaries WHERE account=? AND thread_id=?", params![account, absorbed])?;
    let (to, from) = (format!("t:{survivor}"), format!("t:{absorbed}"));
    c.execute("UPDATE OR IGNORE draft_ahead SET ref_key=? WHERE account=? AND ref_key=?", params![to, account, from])?;
    c.execute("DELETE FROM draft_ahead WHERE account=? AND ref_key=?", params![account, from])?;
    c.execute("UPDATE outbox SET orig_thread_id=? WHERE orig_account=? AND orig_thread_id=?", both)?;
    Ok(())
}

/// The row a flag change is queued for: its account, Message-ID, folder, UID and flags.
type QueuedRow = (String, Option<String>, String, i64, [i64; 5]);

/// A flag change for the server, shown on the row at once (`command_log.queue_flags`): the command keeps the flags
/// it replaced, for a refusal to put back. False when that request was seen already.
pub(crate) fn queue_flags(
    c: &Connection,
    pk: i64,
    flags: &[(&str, bool)],
    mutation_id: &str,
    when: &str,
) -> rusqlite::Result<bool> {
    let row: Option<QueuedRow> = c
        .query_row(
            "SELECT account, message_id, folder, uid, seen, flagged, answered, draft, deleted FROM messages WHERE pk=?",
            [pk],
            |r| {
                Ok((
                    r.get(0)?,
                    r.get(1)?,
                    r.get(2)?,
                    r.get(3)?,
                    [r.get(4)?, r.get(5)?, r.get(6)?, r.get(7)?, r.get(8)?],
                ))
            },
        )
        .optional()?;
    let Some((account, message_id, folder, uid, held)) = row else { return Ok(false) };
    let mid = message_id.unwrap_or_default().trim().to_owned();
    let epoch: Option<i64> = c
        .query_row("SELECT uidvalidity FROM sync_state WHERE account=? AND folder=?", params![account, folder], |r| {
            r.get(0)
        })
        .optional()?
        .flatten();
    let held_now = |name: &str| COLUMNS.iter().position(|c| *c == name).is_some_and(|i| held[i] != 0);
    let mut wanted = serde_json::Map::new();
    let mut was = serde_json::Map::new();
    for (name, on) in flags {
        wanted.insert((*name).to_owned(), (*on).into());
        was.insert((*name).to_owned(), held_now(name).into());
    }
    let params_json = serde_json::json!({"pk": pk, "uid": uid, "epoch": epoch, "flags": wanted, "was": was});
    let key = format!("flag:{account}:{}:{mutation_id}", if mid.is_empty() { pk.to_string() } else { mid.clone() });
    if c.prepare_cached("SELECT 1 FROM mail_commands WHERE idempotency_key=?")?.exists([&key])? {
        return Ok(false);
    }
    c.execute(
        "INSERT INTO mail_commands(account, idempotency_key, kind, message_id, src_folder, dest, params, state, \
         next_attempt_at, created_at) VALUES(?,?,'flag',?,?,NULL,?,'queued',0,?)",
        params![account, key, mid, folder, py_json(&params_json), when],
    )?;
    for (name, on) in flags {
        if COLUMNS.contains(name) {
            c.execute(&format!("UPDATE messages SET {name}=? WHERE pk=?"), params![i64::from(*on), pk])?;
        }
    }
    Ok(true)
}

/// JSON as Python's `json.dumps` writes it: `, ` and `: ` between items, non-ASCII escaped.
pub(crate) fn py_json(value: &serde_json::Value) -> String {
    let mut out = String::new();
    write_py(value, &mut out);
    out
}

fn write_py(value: &serde_json::Value, out: &mut String) {
    use serde_json::Value;
    match value {
        Value::Array(items) => {
            out.push('[');
            for (i, item) in items.iter().enumerate() {
                if i > 0 {
                    out.push_str(", ");
                }
                write_py(item, out);
            }
            out.push(']');
        }
        Value::Object(map) => {
            out.push('{');
            for (i, (k, v)) in map.iter().enumerate() {
                if i > 0 {
                    out.push_str(", ");
                }
                write_py(&Value::String(k.clone()), out);
                out.push_str(": ");
                write_py(v, out);
            }
            out.push('}');
        }
        Value::String(s) => {
            out.push('"');
            for ch in s.chars() {
                match ch {
                    '"' => out.push_str("\\\""),
                    '\\' => out.push_str("\\\\"),
                    '\n' => out.push_str("\\n"),
                    '\r' => out.push_str("\\r"),
                    '\t' => out.push_str("\\t"),
                    '\u{8}' => out.push_str("\\b"),
                    '\u{c}' => out.push_str("\\f"),
                    c if (c as u32) < 0x20 || (c as u32) > 0x7f => {
                        let mut units = [0u16; 2];
                        for unit in c.encode_utf16(&mut units) {
                            out.push_str(&format!("\\u{unit:04x}"));
                        }
                    }
                    c => out.push(c),
                }
            }
            out.push('"');
        }
        other => out.push_str(&other.to_string()),
    }
}

/// Unseen members of a muted thread are marked read, here and on the server, so a new reply in one stays silent
/// (`Muting.on_ingest`). One whose mark is still open isn't asked for again.
fn on_ingest(c: &Connection, account: &str, when: &str) -> rusqlite::Result<()> {
    let pks: Vec<i64> = c
        .prepare_cached(
            "SELECT pk FROM messages m WHERE m.account=? AND m.seen=0 AND m.thread_id IN \
             (SELECT thread_id FROM muted WHERE account=?) AND NOT EXISTS (SELECT 1 FROM mail_commands q \
             WHERE q.account=m.account AND q.kind='flag' AND q.applied=0 AND q.message_id=m.message_id \
             AND q.message_id!='')",
        )?
        .query_map(params![account, account], |r| r.get(0))?
        .collect::<Result<_, _>>()?;
    for pk in pks {
        queue_flags(c, pk, &[("seen", true)], &format!("mute:{pk}:{when}"), when)?;
    }
    Ok(())
}

/// The account's live follow-ups whose commitment is met go (`resolve_followups`): the thread is muted, they
/// answered what was sent, or it was replied to.
fn resolve_followups(c: &Connection, account: &str) -> rusqlite::Result<()> {
    c.prepare_cached(
        "DELETE FROM followups WHERE account=? AND state IN ('pending','due') AND (\
         EXISTS (SELECT 1 FROM muted mu WHERE mu.account=followups.account AND mu.thread_id=followups.thread_id) \
         OR (followups.kind='sent' AND EXISTS (SELECT 1 FROM messages m WHERE m.account=followups.account \
         AND m.thread_id=followups.thread_id AND m.ts > IFNULL(followups.first_sent_ts,0) \
         AND lower(m.from_addr) <> lower(followups.account))) \
         OR (followups.kind IN ('received','replylater') AND EXISTS (SELECT 1 FROM messages m \
         WHERE m.account=followups.account AND m.thread_id=followups.thread_id AND (m.answered=1 OR \
         (lower(m.from_addr)=lower(followups.account) AND m.draft=0)))))",
    )?
    .execute([account])?;
    Ok(())
}

impl SyncStore for Database {
    fn folder_state(&self, account: &str, folder: &str) -> Result<FolderState, Fault> {
        self.read(|c| {
            Ok(c.prepare_cached(
                "SELECT uidvalidity, last_uid, highest_modseq FROM sync_state WHERE account=? AND folder=?",
            )?
            .query_row(params![account, folder], |r| {
                Ok(FolderState {
                    uidvalidity: r.get(0)?,
                    watermark: r.get::<_, Option<i64>>(1)?.unwrap_or(0),
                    modseq: r.get(2)?,
                })
            })
            .optional()?
            .unwrap_or_default())
        })
    }

    fn reset_folder(&self, account: &str, folder: &str, now: i64) -> Result<(), Fault> {
        self.write(|c| {
            let pks: Vec<i64> = c
                .prepare("SELECT pk FROM messages WHERE account=? AND folder=?")?
                .query_map(params![account, folder], |r| r.get(0))?
                .collect::<Result<_, _>>()?;
            for pk in pks {
                delete_message(c, pk, now)?;
            }
            c.execute(
                "UPDATE sync_state SET last_uid=0, scan_state='never', highest_modseq=NULL WHERE account=? AND folder=?",
                params![account, folder],
            )?;
            Ok(())
        })
    }

    fn set_uidvalidity(&self, account: &str, folder: &str, uidvalidity: u32) -> Result<(), Fault> {
        self.write(|c| {
            c.execute(
                "INSERT INTO sync_state(account,folder,uidvalidity) VALUES(?,?,?) \
                 ON CONFLICT(account,folder) DO UPDATE SET uidvalidity=excluded.uidvalidity",
                params![account, folder, uidvalidity],
            )?;
            Ok(())
        })
    }

    fn store_copy(&self, account: &SyncAccount, folder: &str, m: &Incoming) -> Result<(i64, bool), Fault> {
        self.write(|c| {
            let pk = upsert(c, account, folder, m)?;
            set_flags(c, pk, &m.flags)?;
            c.prepare_cached("UPDATE messages SET list_unsub_raw=?, list_unsub_method=? WHERE pk=?")?
                .execute(params![m.fields.unsub_raw, m.fields.unsub_method, pk])?;
            c.prepare_cached("DELETE FROM message_recipients WHERE message_pk=?")?.execute([pk])?;
            for addr in m.fields.recipient_addrs.iter().take(RECIPIENT_ROWS_MAX) {
                c.prepare_cached("INSERT OR IGNORE INTO message_recipients(message_pk, addr) VALUES (?,?)")?
                    .execute(params![pk, addr])?;
            }
            if let Some(labels) = &m.gmail_labels {
                c.prepare_cached("DELETE FROM message_labels WHERE message_pk=?")?.execute([pk])?;
                let mut sorted: Vec<&String> = labels.iter().collect();
                sorted.sort();
                for label in sorted.into_iter().take(LABEL_ROWS_MAX) {
                    c.prepare_cached("INSERT OR IGNORE INTO message_labels(message_pk, label) VALUES (?,?)")?
                        .execute(params![pk, label])?;
                }
            }
            let threads = needs_threading(c, pk, &m.fields.in_reply_to, &m.fields.refs)?;
            c.prepare_cached("UPDATE messages SET in_reply_to=?, refs=?, recipients=? WHERE pk=?")?
                .execute(params![m.fields.in_reply_to, m.fields.refs, m.fields.recipients, pk])?;
            index_message(c, pk)?;
            Ok((pk, threads))
        })
    }

    fn advance(&self, account: &str, folder: &str, max_uid: u32, when: &str) -> Result<(), Fault> {
        self.write(|c| {
            c.execute(
                "INSERT INTO sync_state(account,folder,last_sync,last_uid) VALUES(?,?,?,?) \
                 ON CONFLICT(account,folder) DO UPDATE SET last_sync=excluded.last_sync, \
                 last_uid=MAX(sync_state.last_uid, excluded.last_uid)",
                params![account, folder, when, max_uid],
            )?;
            Ok(())
        })
    }

    fn set_scan_state(&self, account: &str, folder: &str, state: &str) -> Result<(), Fault> {
        self.write(|c| {
            c.execute(
                "INSERT INTO sync_state(account,folder,scan_state) VALUES(?,?,?) \
                 ON CONFLICT(account,folder) DO UPDATE SET scan_state=excluded.scan_state",
                params![account, folder, state],
            )?;
            Ok(())
        })
    }

    fn flag_rows(
        &self,
        account: &str,
        folder: &str,
        uids: Option<&[u32]>,
        newest: usize,
    ) -> Result<(Vec<FlagRow>, HashSet<String>), Fault> {
        self.read(|c| {
            let waiting: HashSet<String> = c
                .prepare_cached(
                    "SELECT message_id FROM mail_commands WHERE account=? AND kind='flag' AND applied=0 \
                     AND state IN ('queued', 'running') AND message_id != ''",
                )?
                .query_map([account], |r| r.get(0))?
                .collect::<Result<_, _>>()?;
            let head = "SELECT pk, uid, message_id, pending_removal, seen, flagged, answered, draft, deleted \
                        FROM messages WHERE account=? AND folder=? ";
            let row = |r: &rusqlite::Row<'_>| {
                Ok(FlagRow {
                    pk: r.get(0)?,
                    uid: r.get(1)?,
                    message_id: r.get::<_, Option<String>>(2)?.unwrap_or_default(),
                    pending_removal: r.get::<_, Option<i64>>(3)?.is_some(),
                    flags: [
                        r.get::<_, Option<i64>>(4)?.unwrap_or(0),
                        r.get::<_, Option<i64>>(5)?.unwrap_or(0),
                        r.get::<_, Option<i64>>(6)?.unwrap_or(0),
                        r.get::<_, Option<i64>>(7)?.unwrap_or(0),
                        r.get::<_, Option<i64>>(8)?.unwrap_or(0),
                    ],
                })
            };
            let mut rows = Vec::new();
            match uids {
                None => {
                    let sql = format!("{head}AND pending_removal IS NULL ORDER BY uid DESC LIMIT ?");
                    for r in c.prepare(&sql)?.query_map(params![account, folder, newest as i64], row)? {
                        rows.push(r?);
                    }
                }
                Some(uids) => {
                    for part in uids.chunks(SLICE) {
                        let sql = format!("{head}AND uid IN ({})", placeholders(part.len()));
                        let mut values: Vec<rusqlite::types::Value> =
                            vec![account.to_owned().into(), folder.to_owned().into()];
                        values.extend(part.iter().map(|u| i64::from(*u).into()));
                        for r in c.prepare(&sql)?.query_map(params_from_iter(values), row)? {
                            rows.push(r?);
                        }
                    }
                }
            }
            Ok((rows, waiting))
        })
    }

    fn write_flags(
        &self,
        account: &str,
        folder: &str,
        changed: &[(FlagRow, [i64; 5])],
        mark: Option<u64>,
    ) -> Result<usize, Fault> {
        self.write(|c| {
            let mut count = 0;
            for (was, new) in changed {
                count += c
                    .prepare_cached(
                        "UPDATE messages SET seen=?, flagged=?, answered=?, draft=?, deleted=? WHERE pk=? AND \
                         seen IS ? AND flagged IS ? AND answered IS ? AND draft IS ? AND deleted IS ?",
                    )?
                    .execute(params![
                        new[0],
                        new[1],
                        new[2],
                        new[3],
                        new[4],
                        was.pk,
                        was.flags[0],
                        was.flags[1],
                        was.flags[2],
                        was.flags[3],
                        was.flags[4]
                    ])?;
            }
            if let Some(mark) = mark {
                c.execute(
                    "UPDATE sync_state SET highest_modseq=? WHERE account=? AND folder=?",
                    params![i64::try_from(mark).unwrap_or(i64::MAX), account, folder],
                )?;
            }
            Ok(count)
        })
    }

    fn settle(
        &self,
        account: &str,
        folder: &str,
        modseq: Option<u64>,
        stored: &[i64],
        when: &str,
    ) -> Result<(), Fault> {
        self.write(|c| {
            if let Some(modseq) = modseq {
                c.execute(
                    "UPDATE sync_state SET highest_modseq=? WHERE account=? AND folder=?",
                    params![i64::try_from(modseq).unwrap_or(i64::MAX), account, folder],
                )?;
            }
            thread_stored(c, account, stored)?;
            on_ingest(c, account, when)?;
            resolve_followups(c, account)?;
            Ok(())
        })
    }
}

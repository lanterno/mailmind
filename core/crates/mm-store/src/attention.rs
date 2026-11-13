//! Attention's tables (`mailmind.attention.adapters.sqlite`): `snoozes`, `muted`, `followups` and `tasks`, and the
//! undo stack its use cases push on (`platform.undo`). Each call is one block.
//!
//! The minute's round of follow-ups runs as SQL over every thread at once. Each statement is built, condition by
//! condition, in the order `mailmind.attention.domain.followups` names them.

use std::collections::BTreeSet;
use std::fmt;
use std::sync::Arc;

use mm_attention::followups::FollowUp;
use mm_attention::ports::{AttentionBook, Copy, FollowUpChange, Message, MuteFound, TaskRow, Undo, WaitingRow};
use mm_kernel::errors::Fault;
use mm_kernel::seams::{Clock, Moment};
use rusqlite::{Connection, OptionalExtension, Row, named_params, params};
use serde_json::Value;

use crate::blocks;
use crate::db::Database;
use crate::discovery::not_snoozed_sql;

/// Attention's view of the database.
pub struct AttentionStore {
    db: Arc<Database>,
}

impl fmt::Debug for AttentionStore {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("AttentionStore(..)")
    }
}

impl AttentionStore {
    pub fn new(db: Arc<Database>) -> AttentionStore {
        AttentionStore { db }
    }
}

fn message(c: &Connection, pk: i64) -> rusqlite::Result<Option<Message>> {
    c.prepare_cached(
        "SELECT m.pk, m.account, m.thread_id, m.message_key, m.folder, m.seen, z.until AS snooze_until FROM messages m \
         LEFT JOIN snoozes z ON z.message_key=m.message_key AND z.folder=m.folder WHERE m.pk=?",
    )?
    .query_row([pk], message_of)
    .optional()
}

fn message_of(r: &Row) -> rusqlite::Result<Message> {
    Ok(Message {
        pk: r.get("pk")?,
        account: r.get("account")?,
        thread_id: r.get("thread_id")?,
        key: r.get("message_key")?,
        folder: r.get("folder")?,
        seen: r.get::<_, Option<i64>>("seen")?.unwrap_or(0) != 0,
        snooze_until: r.get("snooze_until")?,
    })
}

fn thread_pks(c: &Connection, account: &str, thread_id: &str) -> rusqlite::Result<Vec<i64>> {
    c.prepare_cached("SELECT pk FROM messages WHERE account=? AND thread_id=? AND pending_removal IS NULL")?
        .query_map([account, thread_id], |r| r.get(0))?
        .collect()
}

/// The pks a snooze names: the thread's, where it names one.
fn named(c: &Connection, thread: Option<(&str, &str)>, pks: &[i64]) -> rusqlite::Result<Vec<i64>> {
    match thread {
        Some((account, thread_id)) => thread_pks(c, account, thread_id),
        None => Ok(pks.to_vec()),
    }
}

/// Hide message `pk` until `until`. It's kept under the message's key and folder, so it outlives the row: a copy that
/// arrives in that folder is hidden too.
fn set_snooze(c: &Connection, pk: i64, until: i64) -> rusqlite::Result<()> {
    c.prepare_cached(
        "INSERT OR REPLACE INTO snoozes(message_key, folder, until) \
         SELECT message_key, folder, ? FROM messages WHERE pk=? AND message_key IS NOT NULL",
    )?
    .execute(params![until, pk])?;
    Ok(())
}

fn clear_snooze(c: &Connection, pk: i64) -> rusqlite::Result<()> {
    c.prepare_cached(
        "DELETE FROM snoozes WHERE (message_key, folder) IN (SELECT message_key, folder FROM messages WHERE pk=?)",
    )?
    .execute([pk])?;
    Ok(())
}

fn copy_of(r: &Row) -> rusqlite::Result<Copy> {
    Ok(Copy { key: r.get("message_key")?, folder: r.get("folder")? })
}

fn follow_up(c: &Connection, account: &str, thread_id: &str) -> rusqlite::Result<Option<FollowUp>> {
    c.prepare_cached(
        "SELECT kind, state, first_sent_ts, last_msg_ts, due_ts, created_at, resolved_at FROM followups \
         WHERE account=? AND thread_id=?",
    )?
    .query_row([account, thread_id], |r| {
        Ok(FollowUp {
            kind: r.get("kind")?,
            state: r.get("state")?,
            first_sent_ts: r.get("first_sent_ts")?,
            last_msg_ts: r.get("last_msg_ts")?,
            due_ts: r.get("due_ts")?,
            created_at: r.get("created_at")?,
            resolved_at: r.get("resolved_at")?,
        })
    })
    .optional()
}

const MUTED: &str =
    "EXISTS (SELECT 1 FROM muted mu WHERE mu.account=followups.account AND mu.thread_id=followups.thread_id)";

/// A `sent` follow-up comes due: `COMES_DUE`'s sent_and_pending, past_the_grace, no_answer_since and not_muted.
fn promote_sql() -> String {
    let comes_due = [
        "kind='sent' AND state='pending'".to_owned(),
        "first_sent_ts <= :cutoff".to_owned(),
        "NOT EXISTS (SELECT 1 FROM messages m WHERE m.account=followups.account \
         AND m.thread_id=followups.thread_id AND m.ts > followups.first_sent_ts \
         AND lower(m.from_addr) <> lower(followups.account))"
            .to_owned(),
        format!("NOT {MUTED}"),
    ];
    format!("UPDATE followups SET state='due', due_ts=:now WHERE {}", comes_due.join(" AND "))
}

/// A `received` nudge: `RECEIVED_NUDGE`'s conditions, in its order.
fn detect_sql() -> String {
    let reply_worthy = format!("(s.importance>=4 OR s.category='{}')", mm_kernel::verdict::ACTION_NEEDED);
    let nudge = [
        "m.thread_id IS NOT NULL AND m.thread_id != ''".to_owned(),
        "m.seen=1".to_owned(),
        "m.answered=0".to_owned(),
        "m.is_junk=0".to_owned(),
        "m.ts <= :cutoff".to_owned(),
        "m.ts >= :floor".to_owned(),
        "m.pk=(SELECT pk FROM messages m2 WHERE m2.account=m.account AND m2.thread_id=m.thread_id \
         AND m2.draft=0 ORDER BY m2.ts DESC, m2.pk DESC LIMIT 1)"
            .to_owned(),
        not_snoozed_sql("m"),
        format!(
            "EXISTS (SELECT 1 FROM messages mi JOIN assessments s ON s.message_key=mi.message_key \
             WHERE mi.account=m.account AND mi.thread_id=m.thread_id AND {reply_worthy})"
        ),
        "NOT EXISTS (SELECT 1 FROM messages mo WHERE mo.account=m.account AND mo.draft=0 \
         AND mo.thread_id=m.thread_id AND lower(mo.from_addr)=lower(m.account))"
            .to_owned(),
        "NOT EXISTS (SELECT 1 FROM muted mu WHERE mu.account=m.account AND mu.thread_id=m.thread_id)".to_owned(),
        "NOT EXISTS (SELECT 1 FROM followups f WHERE f.account=m.account AND f.thread_id=m.thread_id)".to_owned(),
    ];
    format!(
        "INSERT OR IGNORE INTO followups(account,thread_id,kind,state,last_msg_ts,due_ts,created_at) \
         SELECT m.account, m.thread_id, 'received', 'due', m.ts, :now, :when FROM messages m WHERE {}",
        nudge.join(" AND ")
    )
}

/// What meets a live follow-up's commitment: `MET`'s muted, they_answered and i_replied.
fn resolve_sql() -> String {
    let met = [
        MUTED.to_owned(),
        "(followups.kind='sent' AND EXISTS (SELECT 1 FROM messages m WHERE m.account=followups.account \
         AND m.thread_id=followups.thread_id AND m.ts > IFNULL(followups.first_sent_ts,0) \
         AND lower(m.from_addr) <> lower(followups.account)))"
            .to_owned(),
        "(followups.kind IN ('received','replylater') AND EXISTS (SELECT 1 FROM messages m \
         WHERE m.account=followups.account AND m.thread_id=followups.thread_id AND (m.answered=1 OR \
         (lower(m.from_addr)=lower(followups.account) AND m.draft=0))))"
            .to_owned(),
    ];
    format!("DELETE FROM followups WHERE account=? AND state IN ('pending','due') AND ({})", met.join(" OR "))
}

/// A received nudge that has nagged long enough: `EXPIRES`'s received, live and past_its_lifetime.
const EXPIRE: &str = "DELETE FROM followups WHERE kind='received' AND state IN ('pending','due') AND \
                      last_msg_ts IS NOT NULL AND last_msg_ts < :floor";

fn due_keys(c: &Connection) -> rusqlite::Result<BTreeSet<(String, String)>> {
    c.prepare_cached("SELECT account, thread_id FROM followups WHERE state='due'")?
        .query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?
        .collect()
}

/// The messages of the conversation row `m` is in, as opening it shows them (`kernel.conversation.count_sql`).
const THREAD_COUNT: &str = "(SELECT COUNT(DISTINCT COALESCE(NULLIF(TRIM(tm.message_id), ''), 'pk:' || tm.pk)) \
                            FROM messages tm WHERE tm.account=m.account AND tm.thread_id=m.thread_id \
                            AND tm.pending_removal IS NULL)";

fn task_rows(c: &Connection, id: Option<i64>) -> rusqlite::Result<Vec<TaskRow>> {
    let only = if id.is_some() { "WHERE t.id=? " } else { "" };
    let sql = format!(
        "SELECT t.id, t.account, t.message_key, t.title, t.due, t.done_at, m.folder, m.uid, m.thread_id, \
         m.subject, m.from_name, m.from_addr \
         FROM tasks t LEFT JOIN messages m ON m.pk=COALESCE( \
             (SELECT pk FROM messages WHERE message_key=t.message_key AND folder=t.folder \
              AND pending_removal IS NULL ORDER BY pk DESC LIMIT 1), \
             (SELECT pk FROM messages WHERE message_key=t.message_key AND pending_removal IS NULL \
              ORDER BY pk DESC LIMIT 1)) \
         {only}ORDER BY t.done_at IS NOT NULL, t.due IS NULL, t.due, t.id"
    );
    let mut statement = c.prepare_cached(&sql)?;
    let row = |r: &Row| {
        Ok(TaskRow {
            id: r.get("id")?,
            account: r.get("account")?,
            key: r.get("message_key")?,
            title: r.get("title")?,
            due: r.get("due")?,
            done_at: r.get("done_at")?,
            folder: r.get("folder")?,
            uid: r.get("uid")?,
            thread_id: r.get("thread_id")?,
            subject: r.get("subject")?,
            from_name: r.get("from_name")?,
            from_addr: r.get("from_addr")?,
        })
    };
    match id {
        Some(id) => statement.query_map([id], row)?.collect(),
        None => statement.query_map([], row)?.collect(),
    }
}

fn flag(r: &Row, name: &str) -> rusqlite::Result<bool> {
    Ok(r.get::<_, Option<i64>>(name)?.unwrap_or(0) != 0)
}

impl AttentionBook for AttentionStore {
    fn message(&self, pk: i64) -> Result<Option<Message>, Fault> {
        self.db.read(|c| message(c, pk))
    }

    fn snooze(
        &self,
        thread: Option<(&str, &str)>,
        pks: &[i64],
        until: i64,
    ) -> Result<Vec<(i64, Option<Message>)>, Fault> {
        self.db.write(|c| {
            let mut snoozed = Vec::new();
            for pk in named(c, thread, pks)? {
                let before = message(c, pk)?;
                set_snooze(c, pk, until)?;
                snoozed.push((pk, before));
            }
            Ok(snoozed)
        })
    }

    fn unsnooze(&self, thread: Option<(&str, &str)>, pks: &[i64]) -> Result<usize, Fault> {
        self.db.write(|c| {
            let pks = named(c, thread, pks)?;
            for pk in &pks {
                clear_snooze(c, *pk)?;
            }
            Ok(pks.len())
        })
    }

    fn wake_due(&self, now: i64) -> Result<Vec<Message>, Fault> {
        self.db.write(|c| {
            // A snooze come due with no copy left in its folder ends here: the message was filed, and bringing it
            // back later isn't waking it.
            c.prepare_cached(
                "DELETE FROM snoozes WHERE until<=? AND NOT EXISTS \
                 (SELECT 1 FROM messages m WHERE m.message_key=snoozes.message_key AND m.folder=snoozes.folder)",
            )?
            .execute([now])?;
            let due: Vec<Message> = c
                .prepare_cached(
                    "SELECT m.pk, m.account, m.thread_id, m.message_key, m.folder, m.seen, z.until AS snooze_until \
                     FROM snoozes z CROSS JOIN messages m ON m.message_key=z.message_key AND m.folder=z.folder \
                     WHERE z.until<=?",
                )?
                .query_map([now], message_of)?
                .collect::<rusqlite::Result<_>>()?;
            for r in &due {
                clear_snooze(c, r.pk)?;
            }
            Ok(due)
        })
    }

    fn mute(&self, account: &str, thread_id: &str, when: &str) -> Result<Option<MuteFound>, Fault> {
        self.db.write(|c| {
            let muted = c
                .prepare_cached("SELECT 1 FROM muted WHERE account=? AND thread_id=?")?
                .exists([account, thread_id])?;
            if muted {
                return Ok(None);
            }
            let unread = c
                .prepare_cached("SELECT message_key, folder FROM messages WHERE account=? AND thread_id=? AND seen=0")?
                .query_map([account, thread_id], copy_of)?
                .collect::<rusqlite::Result<_>>()?;
            let member = c
                .prepare_cached(
                    "SELECT message_key, folder FROM messages WHERE account=? AND thread_id=? \
                     AND message_key IS NOT NULL LIMIT 1",
                )?
                .query_row([account, thread_id], copy_of)
                .optional()?;
            c.prepare_cached("INSERT OR IGNORE INTO muted(account, thread_id, muted_at) VALUES(?,?,?)")?
                .execute([account, thread_id, when])?;
            Ok(Some(MuteFound { unread, member }))
        })
    }

    fn unmute(&self, account: &str, thread_id: &str) -> Result<(), Fault> {
        self.db.write(|c| {
            c.prepare_cached("DELETE FROM muted WHERE account=? AND thread_id=?")?.execute([account, thread_id])?;
            Ok(())
        })
    }

    fn follow_up(
        &self,
        account: &str,
        thread_id: &str,
        change: &mut dyn FnMut(Option<FollowUp>, Option<i64>) -> FollowUpChange,
    ) -> Result<(), Fault> {
        self.db.write(|c| {
            let current = follow_up(c, account, thread_id)?;
            let last_ts: Option<i64> = c
                .prepare_cached("SELECT MAX(ts) FROM messages WHERE account=? AND thread_id=? AND draft=0")?
                .query_row([account, thread_id], |r| r.get(0))?;
            match change(current, last_ts) {
                FollowUpChange::Nothing => {}
                FollowUpChange::Put(f) => {
                    c.prepare_cached(
                        "INSERT OR REPLACE INTO followups(account,thread_id,kind,state,first_sent_ts,last_msg_ts,\
                         due_ts,created_at,resolved_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    )?
                    .execute(params![
                        account,
                        thread_id,
                        f.kind,
                        f.state,
                        f.first_sent_ts,
                        f.last_msg_ts,
                        f.due_ts,
                        f.created_at,
                        f.resolved_at
                    ])?;
                }
                FollowUpChange::Settle(f) => {
                    c.prepare_cached("UPDATE followups SET state=?, resolved_at=? WHERE account=? AND thread_id=?")?
                        .execute(params![f.state, f.resolved_at, account, thread_id])?;
                }
                FollowUpChange::Delete => {
                    c.prepare_cached("DELETE FROM followups WHERE account=? AND thread_id=?")?
                        .execute([account, thread_id])?;
                }
            }
            Ok(())
        })
    }

    fn follow_up_round(&self, accounts: &[String], now: i64, when: &str) -> Result<bool, Fault> {
        use mm_attention::followups::{EXPIRE_DAYS, GRACE_DAYS, days_before};
        self.db.write(|c| {
            let before = due_keys(c)?;
            // Resolve first, so what an answer met clears before anything is detected again.
            let resolve = resolve_sql();
            for account in accounts {
                c.prepare_cached(&resolve)?.execute([account])?;
            }
            c.prepare_cached(EXPIRE)?.execute(named_params! {":floor": days_before(now, EXPIRE_DAYS)})?;
            c.prepare_cached(&promote_sql())?
                .execute(named_params! {":now": now, ":cutoff": days_before(now, GRACE_DAYS)})?;
            c.prepare_cached(&detect_sql())?.execute(named_params! {
                ":now": now,
                ":when": when,
                ":cutoff": days_before(now, GRACE_DAYS),
                ":floor": days_before(now, EXPIRE_DAYS),
            })?;
            Ok(due_keys(c)? != before)
        })
    }

    fn waiting_on_others(&self, accounts: &[String], limit: i64) -> Result<Vec<WaitingRow>, Fault> {
        let marks = vec!["?"; accounts.len()].join(",");
        let sql = format!(
            "SELECT m.*, f.kind AS f_kind, f.first_sent_ts AS f_first_sent, f.due_ts AS f_due, \
             {THREAD_COUNT} AS thread_count \
             FROM followups f JOIN messages m ON m.account=f.account AND m.thread_id=f.thread_id \
             WHERE f.account IN ({marks}) AND f.state='due' AND f.kind='sent' \
             AND m.pk=(SELECT pk FROM messages m2 WHERE m2.account=m.account AND m2.thread_id=m.thread_id \
             ORDER BY m2.ts DESC, m2.pk DESC LIMIT 1) \
             ORDER BY m.ts, m.pk LIMIT ?"
        );
        let mut given: Vec<&dyn rusqlite::ToSql> = accounts.iter().map(|a| a as &dyn rusqlite::ToSql).collect();
        given.push(&limit);
        self.db.read(|c| {
            c.prepare(&sql)?
                .query_map(given.as_slice(), |r| {
                    Ok(WaitingRow {
                        key: r.get("message_key")?,
                        folder: r.get("folder")?,
                        uid: r.get("uid")?,
                        from_name: r.get("from_name")?,
                        from_addr: r.get("from_addr")?,
                        subject: r.get("subject")?,
                        date_iso: r.get("date_iso")?,
                        snippet: r.get("snippet")?,
                        seen: flag(r, "seen")?,
                        flagged: flag(r, "flagged")?,
                        has_attach: flag(r, "has_attach")?,
                        is_junk: flag(r, "is_junk")?,
                        bucket: r.get("bucket")?,
                        thread_id: r.get("thread_id")?,
                        ts: r.get("ts")?,
                        thread_count: r.get("thread_count")?,
                        account: r.get("account")?,
                        kind: r.get("f_kind")?,
                        first_sent: r.get("f_first_sent")?,
                        recipients: r.get("recipients")?,
                    })
                })?
                .collect()
        })
    }

    fn add_task(&self, pk: i64, title: &str, due: Option<&str>, when: &str) -> Result<Option<TaskRow>, Fault> {
        self.db.write(|c| {
            let Some(row) = message(c, pk)? else { return Ok(None) };
            let Some(key) = row.key else { return Ok(None) };
            // The same one confirmed again is the same row, with the date it was last given.
            c.prepare_cached(
                "INSERT INTO tasks(account, message_key, folder, title, due, created_at) VALUES(?,?,?,?,?,?) \
                 ON CONFLICT(message_key, title) DO UPDATE SET due=excluded.due",
            )?
            .execute(params![row.account, key, row.folder, title, due, when])?;
            let id: i64 = c
                .prepare_cached("SELECT id FROM tasks WHERE message_key=? AND title=?")?
                .query_row(params![key, title], |r| r.get(0))?;
            Ok(task_rows(c, Some(id))?.into_iter().next())
        })
    }

    fn tasks(&self) -> Result<Vec<TaskRow>, Fault> {
        self.db.read(|c| task_rows(c, None))
    }

    fn set_task_done(&self, id: i64, done_at: Option<&str>) -> Result<bool, Fault> {
        self.db.write(|c| {
            Ok(c.prepare_cached("UPDATE tasks SET done_at=? WHERE id=?")?.execute(params![done_at, id])? > 0)
        })
    }

    fn remove_task(&self, id: i64) -> Result<bool, Fault> {
        self.db.write(|c| Ok(c.prepare_cached("DELETE FROM tasks WHERE id=?")?.execute([id])? > 0))
    }
}

/// The undo stack, as a use case pushes on it (`platform.undo.Undo.push`).
pub struct UndoStack {
    pub(crate) db: Arc<Database>,
    pub(crate) clock: Arc<dyn Clock>,
}

impl fmt::Debug for UndoStack {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("UndoStack(..)")
    }
}

impl UndoStack {
    pub fn new(db: Arc<Database>, clock: Arc<dyn Clock>) -> UndoStack {
        UndoStack { db, clock }
    }
}

impl Undo for UndoStack {
    fn push(&self, entry: &Value, group: Option<&str>) -> Result<(), Fault> {
        let now = self.clock.now();
        let seconds = now.whole_seconds();
        let iso = |s: i64| mm_kernel::when::iso(Moment::from_micros(now.micros() + (s - seconds) * 1_000_000));
        self.db.write(|c| blocks::push_undo(c, entry, group, seconds, iso))
    }
}

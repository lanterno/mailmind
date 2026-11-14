//! What a change to the mail writes (`mm_mailbox::command_ports::CommandStore`): the Python app's command log
//! (`mailbox.adapters.sqlite.command_log`) and the queue over it (`platform.workers.durable_queue`), the rows a move
//! hides (`pending_removal`), the undo stack (`platform.undo`), a sync's record of each account (`sync_state`'s
//! health columns) and what a reconcile prunes, with its audit trail (`platform.db.audit`).
//!
//! JSON goes in as Python's `json.dumps` writes it, so either app reads what the other wrote.

use std::collections::{BTreeMap, HashSet};

use mm_kernel::errors::Fault;
use mm_mailbox::command::{Command, Message};
use mm_mailbox::command_ports::{CommandStore, NewCommand, Tx, UndoRow};
use mm_mailbox::sync_plan::COLUMNS;
use rusqlite::{Connection, OptionalExtension, params};
use serde_json::{Value, json};

use crate::db::Database;
use crate::mailbox_sync::{delete_message, py_json};
use crate::placeholders;

const STORAGE: Fault = Fault::new("StorageError");
const MESSAGE: &str = "pk, account, folder, uid, message_id, subject, from_name, from_addr, thread_id, \
                       pending_removal, seen, flagged, answered, draft, deleted, message_key";
const COMMAND: &str = "id, account, kind, message_id, src_folder, dest, params, state, attempts, next_attempt_at, \
                       applied, begun, outcome, last_error, created_at";

fn storage(_: rusqlite::Error) -> Fault {
    STORAGE
}

fn message(r: &rusqlite::Row<'_>) -> rusqlite::Result<Message> {
    let flag = |i: usize| -> rusqlite::Result<i64> { Ok(r.get::<_, Option<i64>>(i)?.unwrap_or(0)) };
    Ok(Message {
        pk: r.get(0)?,
        account: r.get(1)?,
        folder: r.get(2)?,
        uid: r.get(3)?,
        message_id: r.get(4)?,
        subject: r.get(5)?,
        from_name: r.get(6)?,
        from_addr: r.get(7)?,
        thread_id: r.get(8)?,
        pending_removal: r.get(9)?,
        flags: [flag(10)?, flag(11)?, flag(12)?, flag(13)?, flag(14)?],
        message_key: r.get(15)?,
    })
}

/// A JSON object as a column holds it; `{}` for none, or for what doesn't read as one.
fn object(text: Option<String>) -> Value {
    text.and_then(|t| serde_json::from_str::<Value>(&t).ok()).filter(Value::is_object).unwrap_or_else(|| json!({}))
}

fn command(r: &rusqlite::Row<'_>) -> rusqlite::Result<Command> {
    Ok(Command {
        id: r.get(0)?,
        account: r.get(1)?,
        kind: r.get(2)?,
        message_id: r.get::<_, Option<String>>(3)?.unwrap_or_default(),
        src_folder: r.get(4)?,
        dest: r.get(5)?,
        params: object(r.get(6)?),
        state: r.get(7)?,
        attempts: r.get(8)?,
        next_attempt_at: r.get(9)?,
        applied: r.get::<_, i64>(10)? != 0,
        begun: r.get::<_, i64>(11)? != 0,
        outcome: object(r.get(12)?),
        last_error: r.get(13)?,
        created_at: r.get(14)?,
    })
}

/// One block of work on one connection.
struct Block<'a>(&'a Connection);

impl Block<'_> {
    fn run(&self, sql: &str, values: impl rusqlite::Params) -> Result<usize, Fault> {
        self.0.prepare_cached(sql).and_then(|mut s| s.execute(values)).map_err(storage)
    }

    fn one_command(&self, sql: &str, values: impl rusqlite::Params) -> Result<Option<Command>, Fault> {
        self.0.prepare_cached(sql).and_then(|mut s| s.query_row(values, command).optional()).map_err(storage)
    }

    fn one_message(&self, sql: &str, values: impl rusqlite::Params) -> Result<Option<Message>, Fault> {
        self.0.prepare_cached(sql).and_then(|mut s| s.query_row(values, message).optional()).map_err(storage)
    }

    fn pks(&self, sql: &str, values: impl rusqlite::Params) -> Result<Vec<i64>, Fault> {
        let mut statement = self.0.prepare_cached(sql).map_err(storage)?;
        let rows = statement.query_map(values, |r| r.get(0)).map_err(storage)?;
        rows.collect::<Result<_, _>>().map_err(storage)
    }

    fn undo_rows(&self, sql: &str, values: impl rusqlite::Params) -> Result<Vec<UndoRow>, Fault> {
        let mut statement = self.0.prepare_cached(sql).map_err(storage)?;
        let rows = statement
            .query_map(values, |r| Ok(UndoRow { id: r.get(0)?, group: r.get(1)?, entry: object(r.get(2)?) }))
            .map_err(storage)?;
        rows.collect::<Result<_, _>>().map_err(storage)
    }
}

impl Tx for Block<'_> {
    fn message(&self, pk: i64) -> Result<Option<Message>, Fault> {
        self.one_message(&format!("SELECT {MESSAGE} FROM messages WHERE pk=?"), [pk])
    }

    fn message_by_msgid(&self, account: &str, message_id: &str) -> Result<Option<Message>, Fault> {
        if message_id.is_empty() {
            return Ok(None);
        }
        self.one_message(
            &format!("SELECT {MESSAGE} FROM messages WHERE account=? AND message_id=? ORDER BY pk DESC LIMIT 1"),
            [account, message_id],
        )
    }

    fn newest_copy_in(&self, account: &str, folder: &str, message_id: &str) -> Result<Option<i64>, Fault> {
        Ok(self
            .pks(
                "SELECT pk FROM messages WHERE account=? AND folder=? AND message_id=? ORDER BY pk DESC LIMIT 1",
                [account, folder, message_id],
            )?
            .first()
            .copied())
    }

    fn copy_in(&self, account: &str, folder: &str, message_id: &str, listed: bool) -> Result<bool, Fault> {
        let sql = if listed {
            "SELECT pk FROM messages WHERE account=? AND folder=? AND message_id=? AND pending_removal IS NULL LIMIT 1"
        } else {
            "SELECT pk FROM messages WHERE account=? AND folder=? AND message_id=? LIMIT 1"
        };
        Ok(!self.pks(sql, [account, folder, message_id])?.is_empty())
    }

    fn thread_pks_in(&self, account: &str, folder: &str, thread_id: &str) -> Result<Vec<i64>, Fault> {
        self.pks(
            "SELECT pk FROM messages WHERE account=? AND folder=? AND thread_id=? AND pending_removal IS NULL \
             ORDER BY ts DESC, pk DESC",
            [account, folder, thread_id],
        )
    }

    fn copy_pk(&self, key: i64, folder: &str, uid: Option<i64>) -> Result<Option<i64>, Fault> {
        if let Some(uid) = uid {
            let found = self
                .pks("SELECT pk FROM messages WHERE message_key=? AND folder=? AND uid=?", params![key, folder, uid])?;
            return Ok(found.first().copied());
        }
        let found =
            self.pks("SELECT pk FROM messages WHERE message_key=? AND folder=? LIMIT 2", params![key, folder])?;
        Ok(if found.len() == 1 { Some(found[0]) } else { None })
    }

    fn names_of(&self, pks: &[i64]) -> Result<BTreeMap<i64, (i64, String, i64)>, Fault> {
        let mut out = BTreeMap::new();
        let mut statement =
            self.0.prepare_cached("SELECT message_key, folder, uid FROM messages WHERE pk=?").map_err(storage)?;
        for pk in pks {
            let row: Option<(Option<i64>, String, i64)> =
                statement.query_row([pk], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?))).optional().map_err(storage)?;
            if let Some((key, folder, uid)) = row {
                out.insert(*pk, (key.unwrap_or_default(), folder, uid));
            }
        }
        Ok(out)
    }

    fn delete_message(&self, pk: i64, now: i64) -> Result<(), Fault> {
        delete_message(self.0, pk, now).map_err(storage)
    }

    fn set_local_flags(&self, pk: i64, flags: &[(String, bool)]) -> Result<(), Fault> {
        let mut columns = Vec::new();
        let mut values = Vec::new();
        for (name, on) in flags.iter().filter(|(name, _)| COLUMNS.contains(&name.as_str())) {
            if let Some(i) = columns.iter().position(|c| c == name) {
                values[i] = i64::from(*on);
            } else {
                columns.push(name.clone());
                values.push(i64::from(*on));
            }
        }
        if columns.is_empty() {
            return Ok(());
        }
        let sets: Vec<String> = columns.iter().map(|c| format!("{c}=?")).collect();
        values.push(pk);
        self.run(&format!("UPDATE messages SET {} WHERE pk=?", sets.join(", ")), rusqlite::params_from_iter(values))?;
        Ok(())
    }

    fn mark_pending_removal(&self, pk: i64, command: i64) -> Result<(), Fault> {
        self.run("UPDATE messages SET pending_removal=? WHERE pk=?", [command, pk]).map(|_| ())
    }

    fn restore_pending(&self, command: i64) -> Result<usize, Fault> {
        self.run("UPDATE messages SET pending_removal=NULL WHERE pending_removal=?", [command])
    }

    fn pending_removal_pks(&self, command: i64) -> Result<Vec<i64>, Fault> {
        self.pks("SELECT pk FROM messages WHERE pending_removal=?", [command])
    }

    fn uidvalidity(&self, account: &str, folder: &str) -> Result<Option<i64>, Fault> {
        self.0
            .prepare_cached("SELECT uidvalidity FROM sync_state WHERE account=? AND folder=?")
            .and_then(|mut s| s.query_row([account, folder], |r| r.get::<_, Option<i64>>(0)).optional())
            .map(Option::flatten)
            .map_err(storage)
    }

    fn insert_command(&self, new: &NewCommand<'_>) -> Result<(Option<i64>, bool), Fault> {
        let existing =
            self.pks("SELECT id FROM mail_commands WHERE idempotency_key=?", [new.idempotency_key])?.first().copied();
        if let Some(id) = existing {
            return Ok((Some(id), false));
        }
        let id = self
            .0
            .prepare_cached(
                "INSERT INTO mail_commands(account, idempotency_key, kind, message_id, src_folder, dest, params, \
                 state, next_attempt_at, created_at) VALUES(?,?,?,?,?,?,?,'queued',0,?) RETURNING id",
            )
            .and_then(|mut s| {
                s.query_row(
                    params![
                        new.account,
                        new.idempotency_key,
                        new.kind,
                        new.message_id,
                        new.src_folder,
                        new.dest,
                        py_json(new.params),
                        new.created_at
                    ],
                    |r| r.get::<_, i64>(0),
                )
            })
            .map_err(storage)?;
        Ok((Some(id), true))
    }

    fn command(&self, id: i64) -> Result<Option<Command>, Fault> {
        self.one_command(&format!("SELECT {COMMAND} FROM mail_commands WHERE id=?"), [id])
    }

    fn latest_move(&self, account: &str, message_id: &str) -> Result<Option<Command>, Fault> {
        self.one_command(
            &format!(
                "SELECT {COMMAND} FROM mail_commands WHERE account=? AND message_id=? AND kind!='flag' \
                 ORDER BY id DESC LIMIT 1"
            ),
            [account, message_id],
        )
    }

    fn set_begun(&self, id: i64, begun: bool) -> Result<(), Fault> {
        self.run("UPDATE mail_commands SET begun=? WHERE id=?", [i64::from(begun), id]).map(|_| ())
    }

    fn record(&self, id: i64, dest: Option<&str>, outcome: Option<&Value>) -> Result<(), Fault> {
        self.run(
            "UPDATE mail_commands SET dest=COALESCE(?, dest), outcome=? WHERE id=?",
            params![dest, outcome.map(py_json), id],
        )
        .map(|_| ())
    }

    fn set_params(&self, id: i64, params: &Value) -> Result<(), Fault> {
        self.run("UPDATE mail_commands SET params=? WHERE id=?", params![py_json(params), id]).map(|_| ())
    }

    fn mark_reversed(&self, id: i64) -> Result<(), Fault> {
        self.run("UPDATE mail_commands SET params=json_set(COALESCE(params, '{}'), '$.undone', 1) WHERE id=?", [id])
            .map(|_| ())
    }

    fn open_commands(&self, limit: i64) -> Result<Vec<Command>, Fault> {
        let mut statement = self
            .0
            .prepare_cached(&format!(
                "SELECT {COMMAND} FROM mail_commands WHERE applied=0 AND state IN ('queued', 'running', 'error') \
                 ORDER BY id DESC LIMIT ?"
            ))
            .map_err(storage)?;
        let rows = statement.query_map([limit], command).map_err(storage)?;
        rows.collect::<Result<_, _>>().map_err(storage)
    }

    fn open_counts(&self) -> Result<BTreeMap<String, i64>, Fault> {
        let mut statement = self
            .0
            .prepare_cached(
                "SELECT state, COUNT(*) FROM mail_commands WHERE applied=0 \
                 AND state IN ('queued', 'running', 'error') GROUP BY state",
            )
            .map_err(storage)?;
        let rows = statement.query_map([], |r| Ok((r.get(0)?, r.get(1)?))).map_err(storage)?;
        rows.collect::<Result<_, _>>().map_err(storage)
    }

    fn due(&self, now: i64) -> Result<Vec<i64>, Fault> {
        self.pks("SELECT id FROM mail_commands WHERE state='queued' AND next_attempt_at<=? ORDER BY id", [now])
    }

    fn claim(&self, id: i64) -> Result<Option<Command>, Fault> {
        self.one_command(
            &format!("UPDATE mail_commands SET state='running' WHERE id=? AND state='queued' RETURNING {COMMAND}"),
            [id],
        )
    }

    fn put_back(&self, id: i64, due: i64) -> Result<(), Fault> {
        self.run("UPDATE mail_commands SET state='queued', next_attempt_at=? WHERE id=? AND state='running'", [due, id])
            .map(|_| ())
    }

    fn attempt_failed(&self, id: i64, terminal: bool, attempts: i64, due: i64, error: &str) -> Result<(), Fault> {
        self.run(
            "UPDATE mail_commands SET state=?, attempts=?, next_attempt_at=?, last_error=? WHERE id=?",
            params![if terminal { "error" } else { "queued" }, attempts, due, error, id],
        )
        .map(|_| ())
    }

    fn mark_applied(&self, id: i64) -> Result<(), Fault> {
        self.run("UPDATE mail_commands SET applied=1 WHERE id=?", [id]).map(|_| ())
    }

    fn finish(&self, id: i64, at: &str) -> Result<(), Fault> {
        self.run("UPDATE mail_commands SET state='done', done_at=?, last_error=NULL WHERE id=?", params![at, id])
            .map(|_| ())
    }

    fn reset_running(&self) -> Result<(), Fault> {
        self.run("UPDATE mail_commands SET state='queued' WHERE state='running'", []).map(|_| ())
    }

    fn requeue(&self, id: i64) -> Result<bool, Fault> {
        Ok(self.run(
            "UPDATE mail_commands SET state='queued', attempts=0, next_attempt_at=0, last_error=NULL \
             WHERE id=? AND state='error'",
            [id],
        )? == 1)
    }

    fn cancel_unapplied(&self, id: i64) -> Result<bool, Fault> {
        Ok(self.run("DELETE FROM mail_commands WHERE id=? AND applied=0", [id])? == 1)
    }

    fn next_due(&self, after: i64) -> Result<Option<i64>, Fault> {
        self.0
            .prepare_cached("SELECT MIN(next_attempt_at) FROM mail_commands WHERE state='queued' AND next_attempt_at>?")
            .and_then(|mut s| s.query_row([after], |r| r.get::<_, Option<i64>>(0)))
            .map_err(storage)
    }

    fn counts(&self) -> Result<BTreeMap<String, i64>, Fault> {
        let mut statement =
            self.0.prepare_cached("SELECT state, COUNT(*) FROM mail_commands GROUP BY state").map_err(storage)?;
        let rows = statement.query_map([], |r| Ok((r.get(0)?, r.get(1)?))).map_err(storage)?;
        rows.collect::<Result<_, _>>().map_err(storage)
    }

    fn undo_expire(&self, oldest_kept: &str) -> Result<(), Fault> {
        self.run("DELETE FROM undo_entries WHERE created_at < ?", [oldest_kept]).map(|_| ())
    }

    fn undo_rows(&self) -> Result<Vec<UndoRow>, Fault> {
        self.undo_rows("SELECT id, grp, entry FROM undo_entries ORDER BY id DESC", [])
    }

    fn undo_group_row(&self, group: &str) -> Result<Option<UndoRow>, Fault> {
        Ok(self
            .undo_rows("SELECT id, grp, entry FROM undo_entries WHERE grp=? ORDER BY id DESC LIMIT 1", [group])?
            .into_iter()
            .next())
    }

    fn undo_insert(&self, group: Option<&str>, entry: &Value, at: &str) -> Result<(), Fault> {
        self.run("INSERT INTO undo_entries(grp, entry, created_at) VALUES(?,?,?)", params![group, py_json(entry), at])
            .map(|_| ())
    }

    fn undo_update(&self, id: i64, entry: &Value) -> Result<(), Fault> {
        self.run("UPDATE undo_entries SET entry=? WHERE id=?", params![py_json(entry), id]).map(|_| ())
    }

    fn undo_delete(&self, id: i64) -> Result<(), Fault> {
        self.run("DELETE FROM undo_entries WHERE id=?", [id]).map(|_| ())
    }

    fn undo_keep(&self, keep: i64) -> Result<(), Fault> {
        self.run(
            "DELETE FROM undo_entries WHERE id NOT IN (SELECT id FROM undo_entries ORDER BY id DESC LIMIT ?)",
            [keep],
        )
        .map(|_| ())
    }

    fn undo_entries(&self, oldest_kept: &str) -> Result<Vec<Value>, Fault> {
        Ok(self
            .undo_rows("SELECT id, grp, entry FROM undo_entries WHERE created_at >= ? ORDER BY id", [oldest_kept])?
            .into_iter()
            .map(|r| r.entry)
            .collect())
    }

    fn record_sync_result(&self, account: &str, failed: Option<(&str, &str)>, when: &str) -> Result<(), Fault> {
        self.run("INSERT OR IGNORE INTO sync_state(account, folder) VALUES(?, 'INBOX')", [account])?;
        match failed {
            None => self.run(
                "UPDATE sync_state SET last_ok_ts=?, sync_count=COALESCE(sync_count,0)+1, last_error=NULL, \
                 last_error_ts=NULL, last_error_kind=NULL WHERE account=? AND folder='INBOX'",
                [when, account],
            )?,
            Some((class, kind)) => {
                let class: String =
                    if class.is_empty() { "Error".to_owned() } else { class.chars().take(40).collect() };
                self.run(
                    "UPDATE sync_state SET last_error=?, last_error_ts=?, last_error_kind=?, \
                     sync_count=COALESCE(sync_count,0)+1 WHERE account=? AND folder='INBOX'",
                    params![class, when, if kind.is_empty() { "UNKNOWN" } else { kind }, account],
                )?
            }
        };
        Ok(())
    }

    fn clear_sync_error(&self, account: &str) -> Result<(), Fault> {
        self.run(
            "UPDATE sync_state SET last_error=NULL, last_error_ts=NULL, last_error_kind=NULL \
             WHERE account=? AND folder='INBOX'",
            [account],
        )
        .map(|_| ())
    }

    fn synced_folders(&self, account: &str) -> Result<Vec<String>, Fault> {
        let mut statement = self
            .0
            .prepare_cached("SELECT folder FROM sync_state WHERE account=? ORDER BY folder <> 'INBOX', folder")
            .map_err(storage)?;
        let rows = statement.query_map([account], |r| r.get(0)).map_err(storage)?;
        rows.collect::<Result<_, _>>().map_err(storage)
    }

    fn stored_uids(&self, account: &str, folder: &str) -> Result<Vec<i64>, Fault> {
        self.pks("SELECT uid FROM messages WHERE account=? AND folder=?", [account, folder])
    }

    fn prune(
        &self,
        account: &str,
        folder: &str,
        live: &[u32],
        among: &[i64],
        when: &str,
        now: i64,
    ) -> Result<usize, Fault> {
        let live_set: HashSet<i64> = live.iter().map(|u| i64::from(*u)).collect();
        let among: HashSet<i64> = among.iter().copied().collect();
        let mut statement =
            self.0.prepare_cached("SELECT pk, uid FROM messages WHERE account=? AND folder=?").map_err(storage)?;
        let rows: Vec<(i64, i64)> = statement
            .query_map([account, folder], |r| Ok((r.get(0)?, r.get(1)?)))
            .map_err(storage)?
            .collect::<Result<_, _>>()
            .map_err(storage)?;
        let going: Vec<i64> =
            rows.iter().filter(|(_, uid)| !live_set.contains(uid) && among.contains(uid)).map(|(pk, _)| *pk).collect();
        for pk in &going {
            delete_message(self.0, *pk, now).map_err(storage)?;
        }
        self.run("UPDATE messages SET verified=0 WHERE account=? AND folder=?", [account, folder])?;
        let mut uids: Vec<i64> = live_set.into_iter().collect();
        uids.sort_unstable();
        for slice in uids.chunks(500) {
            let mut values: Vec<rusqlite::types::Value> =
                vec![when.to_owned().into(), account.to_owned().into(), folder.to_owned().into()];
            values.extend(slice.iter().map(|u| (*u).into()));
            self.run(
                &format!(
                    "UPDATE messages SET verified=1, last_seen=? WHERE account=? AND folder=? AND uid IN ({})",
                    placeholders(slice.len())
                ),
                rusqlite::params_from_iter(values),
            )?;
        }
        Ok(going.len())
    }

    fn audit(&self, trail: &str, records: &[Value]) -> Result<(), Fault> {
        for record in records {
            let ts = record.get("ts").and_then(Value::as_str).unwrap_or_default();
            self.run("INSERT INTO audit_events(trail, ts, body) VALUES(?,?,?)", params![trail, ts, py_json(record)])?;
        }
        Ok(())
    }
}

impl CommandStore for Database {
    fn write(&self, work: &mut dyn FnMut(&dyn Tx) -> Result<(), Fault>) -> Result<(), Fault> {
        let mut failed = None;
        let done = Database::write(self, |c| {
            work(&Block(c)).map_err(|fault| {
                failed = Some(fault);
                rusqlite::Error::InvalidQuery
            })
        });
        match failed {
            Some(fault) => Err(fault),
            None => done,
        }
    }

    fn read(&self, work: &mut dyn FnMut(&dyn Tx) -> Result<(), Fault>) -> Result<(), Fault> {
        let mut failed = None;
        let done = Database::read(self, |c| {
            work(&Block(c)).map_err(|fault| {
                failed = Some(fault);
                rusqlite::Error::InvalidQuery
            })
        });
        match failed {
            Some(fault) => Err(fault),
            None => done,
        }
    }
}

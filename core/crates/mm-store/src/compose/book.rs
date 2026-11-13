//! What a send and a save write (`mm_compose::sending_ports::Book`): the outbox, the queue over it
//! (`platform.workers.durable_queue`), the drafts MailMind tracks and their journal (`compose.adapters.sqlite`), and the
//! Mailbox's rows Compose reads (`bootstrap.compose._Messages`).

use mm_compose::ports::{Row, Tracked};
use mm_compose::sending_ports::{Book, ComposeStore, DraftRow, Mark, NewSend, OutboxRow, Quote, Record, Save, Waiting};
use mm_kernel::errors::Fault;
use rusqlite::types::Value as Sql;
use rusqlite::{Connection, ErrorCode, OptionalExtension, named_params, params, params_from_iter};

use crate::copies;
use crate::db::Database;
use crate::mailbox::lists::rows_of;
use crate::mailbox_sync::delete_message;

const STORAGE: Fault = Fault::new("StorageError");

fn storage(_: rusqlite::Error) -> Fault {
    STORAGE
}

fn flag(r: &rusqlite::Row<'_>, name: &str) -> rusqlite::Result<bool> {
    Ok(r.get::<_, Option<i64>>(name)?.unwrap_or(0) != 0)
}

fn outbox_row(r: &rusqlite::Row<'_>) -> rusqlite::Result<OutboxRow> {
    Ok(OutboxRow {
        id: r.get("id")?,
        account: r.get("account")?,
        idempotency_key: r.get("idempotency_key")?,
        message_id: r.get("message_id")?,
        envelope: r.get("envelope")?,
        recipients: r.get("recipients")?,
        subject: r.get("subject")?,
        raw: r.get("raw")?,
        state: r.get("state")?,
        attempts: r.get("attempts")?,
        next_attempt_at: r.get("next_attempt_at")?,
        delivered: flag(r, "delivered")?,
        sent_appended: flag(r, "sent_appended")?,
        answered_set: flag(r, "answered_set")?,
        followup_recorded: flag(r, "followup_recorded")?,
        last_error: r.get("last_error")?,
        draft_id: r.get("draft_id")?,
        draft_deleted: flag(r, "draft_deleted")?,
        created_at: r.get("created_at")?,
        sent_at: r.get("sent_at")?,
        orig_account: r.get("orig_account")?,
        orig_message_id: r.get("orig_message_id")?,
        orig_thread_id: r.get("orig_thread_id")?,
        wire_begun: flag(r, "wire_begun")?,
        wait_kind: r.get("wait_kind")?,
        original_key: r.get("original_key")?,
    })
}

fn draft(r: &rusqlite::Row<'_>) -> rusqlite::Result<DraftRow> {
    Ok(DraftRow {
        id: r.get("id")?,
        account: r.get("account")?,
        folder: r.get("folder")?,
        uid: r.get("uid")?,
        message_id: r.get("message_id")?,
        uidvalidity: r.get("uidvalidity")?,
        mode: r.get("mode")?,
        subject: r.get("subject")?,
        created_at: r.get("created_at")?,
        updated_at: r.get("updated_at")?,
        own_text: r.get("own_text")?,
        tail_text: r.get("tail_text")?,
        tail_html: r.get("tail_html")?,
        bcc: r.get("bcc")?,
        raw_sha: r.get("raw_sha")?,
        idempotency_key: r.get("idempotency_key")?,
        version: r.get("version")?,
        saved_by: r.get("saved_by")?,
        pushed_at: r.get("pushed_at")?,
    })
}

fn waiting(r: &rusqlite::Row<'_>) -> rusqlite::Result<Waiting> {
    Ok(Waiting {
        draft_id: r.get("draft_id")?,
        version: r.get("version")?,
        account: r.get("account")?,
        discard: flag(r, "discard")?,
        message_id: r.get("message_id")?,
        raw: r.get("raw")?,
        raw_sha: r.get("raw_sha")?,
        own_text: r.get("own_text")?,
        tail_text: r.get("tail_text")?,
        tail_html: r.get("tail_html")?,
        bcc: r.get("bcc")?,
        tried: r.get("tried")?,
        saved_at: r.get("saved_at")?,
        due_at: r.get("due_at")?,
        attempts: r.get("attempts")?,
    })
}

/// One block of work on one connection.
struct Block<'a>(&'a Connection);

impl Block<'_> {
    fn run(&self, sql: &str, values: impl rusqlite::Params) -> Result<usize, Fault> {
        self.0.prepare_cached(sql).and_then(|mut s| s.execute(values)).map_err(storage)
    }

    fn one<T>(
        &self,
        sql: &str,
        values: impl rusqlite::Params,
        map: impl FnOnce(&rusqlite::Row<'_>) -> rusqlite::Result<T>,
    ) -> Result<Option<T>, Fault> {
        self.0.prepare_cached(sql).and_then(|mut s| s.query_row(values, map).optional()).map_err(storage)
    }

    fn many<T>(
        &self,
        sql: &str,
        values: impl rusqlite::Params,
        map: impl FnMut(&rusqlite::Row<'_>) -> rusqlite::Result<T>,
    ) -> Result<Vec<T>, Fault> {
        let mut statement = self.0.prepare_cached(sql).map_err(storage)?;
        let rows = statement.query_map(values, map).map_err(storage)?;
        rows.collect::<Result<_, _>>().map_err(storage)
    }

    fn outbox_id(&self, key: &str) -> Result<Option<i64>, Fault> {
        self.one("SELECT id FROM outbox WHERE idempotency_key=?", [key], |r| r.get(0))
    }

    fn row(&self, sql: &str, values: impl rusqlite::Params) -> Result<Option<Row>, Fault> {
        Ok(rows_of(self.0, sql, values).map_err(storage)?.into_iter().next())
    }
}

/// `_NOT_ABOUT_TO_GO`: a send about to go holds its draft, and one scheduled for later leaves its copy to catch up.
const NOT_ABOUT_TO_GO: &str = "NOT EXISTS (SELECT 1 FROM outbox o WHERE o.draft_id=j.draft_id \
                               AND o.state IN ('queued','sending') AND o.next_attempt_at <= ?)";

impl Book for Block<'_> {
    fn outbox_insert(&self, row: &NewSend) -> Result<(Option<i64>, bool), Fault> {
        if let Some(id) = self.outbox_id(&row.idempotency_key)? {
            return Ok((Some(id), false));
        }
        let inserted = self
            .0
            .prepare_cached(
                "INSERT INTO outbox(account,idempotency_key,message_id,orig_account,orig_message_id,orig_thread_id,\
             original_key,envelope,recipients,subject,raw,state,next_attempt_at,wait_kind,draft_id,created_at) \
             VALUES(:account,:idempotency_key,:message_id,:orig_account,:orig_message_id,:orig_thread_id,\
             :original_key,:envelope,:recipients,:subject,:raw,'queued',:next_attempt_at,:wait_kind,:draft_id,\
             :created_at) RETURNING id",
            )
            .and_then(|mut s| {
                s.query_row(
                    named_params! {
                        ":account": row.account,
                        ":idempotency_key": row.idempotency_key,
                        ":message_id": row.message_id,
                        ":orig_account": row.orig_account,
                        ":orig_message_id": row.orig_message_id,
                        ":orig_thread_id": row.orig_thread_id,
                        ":original_key": row.original_key,
                        ":envelope": row.envelope,
                        ":recipients": row.recipients,
                        ":subject": row.subject,
                        ":raw": row.raw,
                        ":next_attempt_at": row.next_attempt_at,
                        ":wait_kind": row.wait_kind,
                        ":draft_id": row.draft_id,
                        ":created_at": row.created_at,
                    },
                    |r| r.get::<_, i64>(0),
                )
            });
        match inserted {
            Ok(id) => Ok((Some(id), true)),
            // The same key went in between the look and the insert: that row is the send.
            Err(rusqlite::Error::SqliteFailure(e, _)) if e.code == ErrorCode::ConstraintViolation => {
                Ok((self.outbox_id(&row.idempotency_key)?, false))
            }
            Err(e) => Err(storage(e)),
        }
    }

    fn outbox_set(&self, id: i64, marks: &[Mark]) -> Result<(), Fault> {
        let mut columns: Vec<(&str, Sql)> = Vec::new();
        for mark in marks {
            let (column, value) = match *mark {
                Mark::WireBegun(on) => ("wire_begun", Sql::Integer(i64::from(on))),
                Mark::SentAppended => ("sent_appended", Sql::Integer(1)),
                Mark::AnsweredSet => ("answered_set", Sql::Integer(1)),
                Mark::DraftDeleted => ("draft_deleted", Sql::Integer(1)),
                Mark::FollowupRecorded => ("followup_recorded", Sql::Integer(1)),
                Mark::WaitKind(kind) => ("wait_kind", Sql::Text(kind.to_owned())),
            };
            match columns.iter_mut().find(|(c, _)| *c == column) {
                Some(slot) => slot.1 = value,
                None => columns.push((column, value)),
            }
        }
        if columns.is_empty() {
            return Ok(());
        }
        let sets: Vec<String> = columns.iter().map(|(c, _)| format!("{c}=?")).collect();
        let sql = format!("UPDATE outbox SET {} WHERE id=?", sets.join(", "));
        let values = columns.into_iter().map(|(_, v)| v).chain([Sql::Integer(id)]);
        self.run(&sql, params_from_iter(values)).map(drop)
    }

    fn outbox_send_now(&self, id: i64, now: i64) -> Result<bool, Fault> {
        let sql = "UPDATE outbox SET next_attempt_at=?, wait_kind='grace' WHERE id=? AND state='queued'";
        Ok(self.run(sql, params![now, id])? == 1)
    }

    fn outbox_holds_draft(&self, draft_id: i64) -> Result<bool, Fault> {
        let sql = "SELECT 1 FROM outbox WHERE draft_id=? AND state IN ('queued','sending') LIMIT 1";
        Ok(self.one(sql, [draft_id], |_| Ok(()))?.is_some())
    }

    fn outbox_get(&self, id: i64) -> Result<Option<OutboxRow>, Fault> {
        self.one("SELECT * FROM outbox WHERE id=?", [id], outbox_row)
    }

    fn outbox_keys(&self, prefix: &str) -> Result<Vec<String>, Fault> {
        // SQLite's substr counts characters, as Python's len does.
        let length = i64::try_from(prefix.chars().count()).unwrap_or(i64::MAX);
        let sql = "SELECT idempotency_key FROM outbox WHERE substr(idempotency_key, 1, ?) = ? ORDER BY id DESC";
        self.many(sql, params![length, prefix], |r| r.get(0))
    }

    fn outbox_due(&self, now: i64) -> Result<Vec<(i64, String)>, Fault> {
        let sql = "SELECT id, account FROM outbox WHERE state='queued' AND next_attempt_at<=? ORDER BY id";
        self.many(sql, [now], |r| Ok((r.get(0)?, r.get(1)?)))
    }

    fn outbox_claim(&self, id: i64) -> Result<Option<OutboxRow>, Fault> {
        self.one("UPDATE outbox SET state='sending' WHERE id=? AND state='queued' RETURNING *", [id], outbox_row)
    }

    fn outbox_retry_later(
        &self,
        id: i64,
        attempts: i64,
        terminal: bool,
        next_at: i64,
        error: &str,
    ) -> Result<(), Fault> {
        let state = if terminal { "error" } else { "queued" };
        let sql = "UPDATE outbox SET state=?, attempts=?, next_attempt_at=?, last_error=? WHERE id=?";
        self.run(sql, params![state, attempts, next_at, error, id]).map(drop)
    }

    fn outbox_give_up(&self, id: i64, attempts: i64, error: &str) -> Result<(), Fault> {
        let sql = "UPDATE outbox SET state='error', attempts=?, next_attempt_at=0, last_error=? WHERE id=?";
        self.run(sql, params![attempts, error, id]).map(drop)
    }

    fn outbox_delivered(&self, id: i64) -> Result<(), Fault> {
        self.run("UPDATE outbox SET delivered=1 WHERE id=?", [id]).map(drop)
    }

    fn outbox_finish(&self, id: i64, at: &str) -> Result<(), Fault> {
        self.run("UPDATE outbox SET state='sent', sent_at=?, last_error=NULL WHERE id=?", params![at, id]).map(drop)
    }

    fn outbox_reset_stuck(&self, unsure: &str) -> Result<(), Fault> {
        self.run(
            "UPDATE outbox SET state='error', next_attempt_at=0, last_error=? \
             WHERE state='sending' AND wire_begun=1 AND delivered=0",
            [unsure],
        )?;
        self.run("UPDATE outbox SET state='queued' WHERE state='sending'", []).map(drop)
    }

    fn outbox_requeue(&self, id: i64) -> Result<bool, Fault> {
        let sql = "UPDATE outbox SET state='queued', attempts=0, next_attempt_at=0, last_error=NULL \
                   WHERE id=? AND state='error'";
        Ok(self.run(sql, [id])? == 1)
    }

    fn outbox_cancel_queued(&self, id: i64) -> Result<bool, Fault> {
        Ok(self.run("DELETE FROM outbox WHERE id=? AND state='queued' AND delivered=0", [id])? == 1)
    }

    fn outbox_next_due(&self, after: i64) -> Result<Option<i64>, Fault> {
        let sql = "SELECT MIN(next_attempt_at) FROM outbox WHERE state='queued' AND next_attempt_at>?";
        Ok(self.one(sql, [after], |r| r.get::<_, Option<i64>>(0))?.flatten())
    }

    fn draft_get(&self, id: i64) -> Result<Option<DraftRow>, Fault> {
        self.one("SELECT * FROM drafts WHERE id=?", [id], draft)
    }

    fn draft_by_key(&self, key: &str) -> Result<Option<DraftRow>, Fault> {
        self.one("SELECT * FROM drafts WHERE idempotency_key=? ORDER BY id DESC LIMIT 1", [key], draft)
    }

    fn draft_set_key(&self, id: i64, key: &str) -> Result<(), Fault> {
        self.run("UPDATE drafts SET idempotency_key=? WHERE id=?", params![key, id]).map(drop)
    }

    fn draft_by_msgid(&self, account: &str, message_id: Option<&str>) -> Result<Option<DraftRow>, Fault> {
        let Some(message_id) = message_id.filter(|m| !m.is_empty()) else { return Ok(None) };
        self.one("SELECT * FROM drafts WHERE account=? AND message_id=?", [account, message_id], draft)
    }

    fn draft_insert(&self, d: &Tracked) -> Result<i64, Fault> {
        let sql = "INSERT INTO drafts(account,folder,uid,message_id,uidvalidity,mode,subject,created_at,updated_at) \
                   VALUES(:account,:folder,:uid,:message_id,:uidvalidity,:mode,:subject,:at,:at) RETURNING id";
        let values = named_params! {
            ":account": d.account,
            ":folder": d.folder,
            ":uid": d.uid,
            ":message_id": d.message_id,
            ":uidvalidity": d.uidvalidity,
            ":mode": d.mode,
            ":subject": d.subject,
            ":at": d.at,
        };
        self.one(sql, values, |r| r.get(0))?.ok_or(STORAGE)
    }

    fn draft_set_resume(&self, id: i64, record: &Record) -> Result<(), Fault> {
        let sql = "UPDATE drafts SET own_text=?, tail_text=?, tail_html=?, bcc=?, raw_sha=? WHERE id=?";
        let r = record;
        self.run(sql, params![r.own_text, r.tail_text, r.tail_html, r.bcc, r.raw_sha, id]).map(drop)
    }

    fn flagged_draft(&self, account: &str, folder: &str, uid: i64) -> Result<bool, Fault> {
        let sql = "SELECT draft FROM messages WHERE account=? AND folder=? AND uid=?";
        let flagged = self.one(sql, params![account, folder, uid], |r| r.get::<_, Option<i64>>(0))?;
        Ok(flagged.flatten().unwrap_or(0) != 0)
    }

    fn draft_repoint(
        &self,
        seen: &DraftRow,
        folder: &str,
        uid: i64,
        uidvalidity: Option<i64>,
        forget: bool,
    ) -> Result<bool, Fault> {
        let sql = "UPDATE drafts SET folder=?, uid=?, uidvalidity=? WHERE id=? AND uid IS ? AND message_id IS ?";
        let moved = self.run(sql, params![folder, uid, uidvalidity, seen.id, seen.uid, seen.message_id])?;
        if moved > 0 && forget {
            self.run("UPDATE drafts SET own_text=NULL, bcc=NULL, raw_sha=NULL WHERE id=?", [seen.id])?;
        }
        Ok(moved > 0)
    }

    fn draft_delete(&self, id: i64) -> Result<(), Fault> {
        self.run("DELETE FROM drafts WHERE id=?", [id]).map(drop)
    }

    fn draft_note_save(
        &self,
        id: i64,
        mode: Option<&str>,
        subject: Option<&str>,
        version: i64,
        saved_by: Option<&str>,
        updated_at: &str,
    ) -> Result<(), Fault> {
        let sql = "UPDATE drafts SET mode=?, subject=?, version=?, saved_by=?, updated_at=? WHERE id=?";
        self.run(sql, params![mode, subject, version, saved_by, updated_at, id]).map(drop)
    }

    fn draft_note_discard(&self, id: i64, version: i64, updated_at: &str) -> Result<(), Fault> {
        self.run("UPDATE drafts SET version=?, updated_at=? WHERE id=?", params![version, updated_at, id]).map(drop)
    }

    fn draft_settle_push(
        &self,
        id: i64,
        account: &str,
        folder: &str,
        uid: Option<i64>,
        message_id: &str,
        uidvalidity: Option<i64>,
        pushed_at: i64,
    ) -> Result<(), Fault> {
        let sql = "UPDATE drafts SET account=?, folder=?, uid=?, message_id=?, uidvalidity=?, pushed_at=? WHERE id=?";
        self.run(sql, params![account, folder, uid, message_id, uidvalidity, pushed_at, id]).map(drop)
    }

    fn draft_quote(&self, id: i64) -> Result<Option<Quote>, Fault> {
        let pair = |r: &rusqlite::Row<'_>| Ok((r.get(0)?, r.get(1)?));
        let journal = "SELECT tail_text, tail_html FROM draft_journal WHERE draft_id=? AND raw IS NOT NULL";
        if let Some(quote) = self.one(journal, [id], pair)? {
            return Ok(Some(quote));
        }
        self.one("SELECT tail_text, tail_html FROM drafts WHERE id=?", [id], pair)
    }

    fn journal_get(&self, draft_id: i64) -> Result<Option<Waiting>, Fault> {
        self.one("SELECT * FROM draft_journal WHERE draft_id=?", [draft_id], waiting)
    }

    fn journal_put(&self, save: &Save<'_>) -> Result<(), Fault> {
        let r = save.record;
        self.run(
            "INSERT INTO draft_journal(draft_id,version,account,discard,message_id,raw,raw_sha,own_text,tail_text,\
             tail_html,bcc,saved_at,due_at,attempts) VALUES(?,?,?,0,?,?,?,?,?,?,?,?,?,?) \
             ON CONFLICT(draft_id) DO UPDATE SET version=excluded.version, account=excluded.account, discard=0, \
             message_id=excluded.message_id, raw=excluded.raw, raw_sha=excluded.raw_sha, \
             own_text=excluded.own_text, tail_text=excluded.tail_text, tail_html=excluded.tail_html, \
             bcc=excluded.bcc, saved_at=excluded.saved_at, due_at=excluded.due_at, attempts=excluded.attempts",
            params![
                save.draft_id,
                save.version,
                save.account,
                save.message_id,
                save.raw,
                r.raw_sha,
                r.own_text,
                r.tail_text,
                r.tail_html,
                r.bcc,
                save.saved_at,
                save.due_at,
                save.attempts
            ],
        )
        .map(drop)
    }

    fn journal_discard(&self, draft_id: i64, version: i64, account: &str, now: i64) -> Result<(), Fault> {
        self.run(
            "INSERT INTO draft_journal(draft_id,version,account,discard,saved_at,due_at) VALUES(?,?,?,1,?,?) \
             ON CONFLICT(draft_id) DO UPDATE SET version=excluded.version, discard=1, message_id=NULL, raw=NULL, \
             raw_sha=NULL, own_text=NULL, tail_text=NULL, tail_html=NULL, bcc=NULL, saved_at=excluded.saved_at, \
             due_at=excluded.due_at, attempts=0",
            params![draft_id, version, account, now, now],
        )
        .map(drop)
    }

    fn journal_tried(&self, draft_id: i64, version: i64, message_id: &str) -> Result<bool, Fault> {
        let sql = "UPDATE draft_journal SET tried=? WHERE draft_id=? AND version=? AND discard=0";
        Ok(self.run(sql, params![message_id, draft_id, version])? == 1)
    }

    fn journal_settled(&self, draft_id: i64, version: i64) -> Result<(), Fault> {
        if self.run("DELETE FROM draft_journal WHERE draft_id=? AND version=?", [draft_id, version])? == 0 {
            self.run("UPDATE draft_journal SET tried=NULL, attempts=0 WHERE draft_id=?", [draft_id])?;
        }
        Ok(())
    }

    fn journal_failed(&self, draft_id: i64, attempts: i64, due_at: i64) -> Result<(), Fault> {
        self.run("UPDATE draft_journal SET attempts=?, due_at=? WHERE draft_id=?", [attempts, due_at, draft_id])
            .map(drop)
    }

    fn journal_due(&self, now: i64, soon: i64) -> Result<Vec<i64>, Fault> {
        let sql = format!("SELECT draft_id FROM draft_journal j WHERE due_at<=? AND {NOT_ABOUT_TO_GO} ORDER BY due_at");
        self.many(&sql, [now, soon], |r| r.get(0))
    }

    fn journal_next_due(&self, after: i64, soon: i64) -> Result<Option<i64>, Fault> {
        let sql = format!("SELECT MIN(due_at) FROM draft_journal j WHERE due_at>? AND {NOT_ABOUT_TO_GO}");
        Ok(self.one(&sql, [after, soon], |r| r.get::<_, Option<i64>>(0))?.flatten())
    }

    fn journal_move(&self, draft_id: i64, to: i64) -> Result<(), Fault> {
        self.run("UPDATE draft_journal SET draft_id=?, tried=NULL WHERE draft_id=?", [to, draft_id]).map(drop)
    }

    fn message(&self, pk: i64) -> Result<Option<Row>, Fault> {
        self.row("SELECT * FROM messages WHERE pk=?", [pk])
    }

    fn message_by_msgid(&self, account: &str, message_id: Option<&str>) -> Result<Option<Row>, Fault> {
        let Some(message_id) = message_id.filter(|m| !m.is_empty()) else { return Ok(None) };
        self.row(
            "SELECT * FROM messages WHERE account=? AND message_id=? ORDER BY pk DESC LIMIT 1",
            [account, message_id],
        )
    }

    fn message_by_key(&self, account: &str, key: i64) -> Result<Option<Row>, Fault> {
        self.row(
            "SELECT * FROM messages WHERE account=? AND message_key=? ORDER BY pk DESC LIMIT 1",
            params![account, key],
        )
    }

    fn message_pk(&self, key: i64) -> Result<Option<i64>, Fault> {
        copies::message_pk(self.0, key, None, None).map_err(storage)
    }

    fn body(&self, pk: i64) -> Result<Option<Vec<u8>>, Fault> {
        Ok(self.one("SELECT raw FROM bodies WHERE message_pk=?", [pk], |r| r.get::<_, Option<Vec<u8>>>(0))?.flatten())
    }

    fn uidvalidity(&self, account: &str, folder: &str) -> Result<Option<i64>, Fault> {
        let sql = "SELECT uidvalidity FROM sync_state WHERE account=? AND folder=?";
        Ok(self.one(sql, [account, folder], |r| r.get::<_, Option<i64>>(0))?.flatten())
    }

    fn remove_message(&self, pk: i64, now: i64) -> Result<(), Fault> {
        delete_message(self.0, pk, now).map_err(storage)
    }

    fn remove_copies(&self, account: &str, folder: &str, uids: &[i64], now: i64) -> Result<(), Fault> {
        for uid in uids {
            let sql = "SELECT pk FROM messages WHERE account=? AND folder=? AND uid=?";
            if let Some(pk) = self.one(sql, params![account, folder, uid], |r| r.get::<_, i64>(0))? {
                delete_message(self.0, pk, now).map_err(storage)?;
            }
        }
        Ok(())
    }
}

/// `work` on one connection, a fault it answers carried past the store's own error.
fn bridged(
    run: impl FnOnce(&mut dyn FnMut(&Connection) -> rusqlite::Result<()>) -> Result<(), Fault>,
    work: &mut dyn FnMut(&dyn Book) -> Result<(), Fault>,
) -> Result<(), Fault> {
    let mut failed = None;
    let done = run(&mut |c| {
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

impl ComposeStore for Database {
    fn write(&self, work: &mut dyn FnMut(&dyn Book) -> Result<(), Fault>) -> Result<(), Fault> {
        bridged(|block| Database::write(self, block), work)
    }

    fn read(&self, work: &mut dyn FnMut(&dyn Book) -> Result<(), Fault>) -> Result<(), Fault> {
        bridged(|block| Database::read(self, block), work)
    }
}

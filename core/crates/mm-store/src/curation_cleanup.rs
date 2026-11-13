//! What a sender's cleanup, its unsubscribe and a sweep read and write of the database (`mailmind.curation.adapters`'
//! `sqlite` and `audit_events`): stored mail by sender, Curation's own `unsub_log` and `sender_unsub`, the sweep trail,
//! and the rows a sweep forgets. The rows are the Mailbox's, and change only through its functions.

use mm_curation::cleanup_ports::{Audited, CleanupMail, StoredWay, SweepTrail, SweptRows, Target, UnsubRow};
use mm_curation::unsubscribe::{self, Attempt, Logged, UnsubWay};
use mm_kernel::errors::Fault;
use mm_kernel::seams::Moment;
use rusqlite::{Connection, OptionalExtension, params};
use serde_json::{Value, json};

use crate::blocks;
use crate::curation::CurationStore;
use crate::mailbox_sync::{delete_message, py_json};

/// The trail a sweep, expiry and a sender's cleanup write to (`audit.SWEEP`).
const SWEEP: &str = "sweep";
/// What a sweep record keeps of a subject, in characters.
const SUBJECT_KEPT: usize = 140;

fn append(c: &Connection, records: &[Value]) -> rusqlite::Result<()> {
    let mut insert = c.prepare_cached("INSERT INTO audit_events(trail, ts, body) VALUES(?,?,?)")?;
    for r in records {
        insert.execute(params![SWEEP, r.get("ts").and_then(Value::as_str), py_json(r)])?;
    }
    Ok(())
}

fn unsub_row(r: &rusqlite::Row) -> rusqlite::Result<UnsubRow> {
    Ok(UnsubRow {
        pk: r.get("pk")?,
        account: r.get::<_, Option<String>>("account")?.unwrap_or_default(),
        folder: r.get::<_, Option<String>>("folder")?.unwrap_or_default(),
        uid: r.get("uid")?,
        list_unsub_raw: r.get("list_unsub_raw")?,
        list_unsub_method: r.get("list_unsub_method")?,
    })
}

fn set_list_unsub(c: &Connection, pk: i64, parsed: &UnsubWay) -> rusqlite::Result<()> {
    c.prepare_cached("UPDATE messages SET list_unsub_raw=?, list_unsub_method=? WHERE pk=?")?.execute(params![
        parsed.raw,
        parsed.method,
        pk
    ])?;
    Ok(())
}

/// A list of text kept as JSON, or none where it isn't one.
fn accounts_of(text: &str) -> Vec<String> {
    serde_json::from_str::<Vec<String>>(text).unwrap_or_default()
}

impl CurationStore {
    /// Put `entry` on the undo stack under `group`, as a block's reversal goes with a cleanup's moves.
    pub fn push_undo(&self, entry: &Value, group: &str) -> Result<(), Fault> {
        let now = self.clock.now();
        let seconds = now.whole_seconds();
        let iso = |s: i64| mm_kernel::when::iso(Moment::from_micros(now.micros() + (s - seconds) * 1_000_000));
        self.db.write(|c| blocks::push_undo(c, entry, Some(group), seconds, iso))
    }
}

impl CleanupMail for CurationStore {
    fn counts(&self, addr: &str) -> Result<(i64, i64), Fault> {
        self.db.read(|c| {
            let n = c.query_row("SELECT COUNT(*) FROM messages WHERE lower(from_addr)=?", [addr], |r| r.get(0))?;
            let unseen =
                c.query_row("SELECT COUNT(*) FROM messages WHERE lower(from_addr)=? AND seen=0", [addr], |r| r.get(0))?;
            Ok((n, unseen))
        })
    }

    fn sweep_targets(&self, addr: &str, limit: usize) -> Result<Vec<Target>, Fault> {
        let limit = i64::try_from(limit).unwrap_or(i64::MAX);
        self.db.read(|c| {
            c.prepare_cached(
                "SELECT pk, message_key, uid, account, folder, subject, seen FROM messages \
                 WHERE lower(from_addr)=? AND folder='INBOX' AND pending_removal IS NULL ORDER BY ts DESC LIMIT ?",
            )?
            .query_map(params![addr, limit], |r| {
                Ok(Target {
                    pk: r.get("pk")?,
                    key: r.get("message_key")?,
                    uid: r.get("uid")?,
                    account: r.get::<_, Option<String>>("account")?.unwrap_or_default(),
                    folder: r.get::<_, Option<String>>("folder")?.unwrap_or_default(),
                    subject: r.get("subject")?,
                    seen: r.get("seen")?,
                })
            })?
            .collect()
        })
    }

    fn unsub_stored(&self, addr: &str) -> Result<StoredWay, Fault> {
        // What a body said is kept on its row after the read, in a block of its own.
        let mut said: Vec<(i64, UnsubWay)> = Vec::new();
        let (rows, accounts, info) = self.db.read(|c| {
            let rows: Vec<UnsubRow> = c
                .prepare_cached(
                    "SELECT pk, account, folder, uid, list_unsub_raw, list_unsub_method FROM messages \
                     WHERE lower(from_addr)=? ORDER BY ts DESC",
                )?
                .query_map([addr], unsub_row)?
                .collect::<rusqlite::Result<_>>()?;
            let mut accounts: Vec<String> = Vec::new();
            for r in &rows {
                if !r.account.is_empty() && !accounts.contains(&r.account) {
                    accounts.push(r.account.clone());
                }
            }
            let mut body = c.prepare_cached("SELECT raw FROM bodies WHERE message_pk=?")?;
            let info = unsubscribe::stored(
                &rows,
                |pk| body.query_row([pk], |r| r.get::<_, Option<Vec<u8>>>(0)).optional().map(Option::flatten),
                |pk, parsed| said.push((pk, parsed.clone())),
            )?;
            Ok((rows, accounts, info))
        })?;
        if !said.is_empty() {
            self.db.write(|c| said.iter().try_for_each(|(pk, parsed)| set_list_unsub(c, *pk, parsed)))?;
        }
        Ok((rows, accounts, info))
    }

    fn remember_unsub(&self, pk: i64, parsed: &UnsubWay) -> Result<(), Fault> {
        self.db.write(|c| set_list_unsub(c, pk, parsed))
    }

    fn keep_unsub(&self, addr: &str, info: &UnsubWay, accounts: &[String]) -> Result<(), Fault> {
        let now = mm_kernel::when::iso(self.clock.now());
        self.db.write(|c| {
            if info.offers_a_way() {
                c.prepare_cached(
                    "INSERT OR REPLACE INTO sender_unsub(from_addr, raw, accounts, updated_at) VALUES(?,?,?,?)",
                )?
                .execute(params![addr, info.raw, py_json(&json!(accounts)), now])?;
            } else {
                // The list has stopped offering a way.
                c.prepare_cached("DELETE FROM sender_unsub WHERE from_addr=?")?.execute([addr])?;
            }
            Ok(())
        })
    }

    fn kept_unsub(&self, addr: &str) -> Result<Option<(String, Vec<String>)>, Fault> {
        self.db.read(|c| {
            c.query_row("SELECT raw, accounts FROM sender_unsub WHERE from_addr=?", [addr], |r| {
                Ok((r.get::<_, String>(0)?, accounts_of(&r.get::<_, String>(1)?)))
            })
            .optional()
        })
    }

    fn attempt(&self, key: &str) -> Result<Option<Logged>, Fault> {
        self.db.read(|c| {
            c.query_row("SELECT status, method FROM unsub_log WHERE idempotency_key=?", [key], |r| {
                Ok(Logged {
                    status: r.get::<_, Option<String>>(0)?.unwrap_or_default(),
                    method: r.get::<_, Option<String>>(1)?.unwrap_or_default(),
                })
            })
            .optional()
        })
    }

    fn record_attempt(&self, from_addr: &str, tried: &Attempt, key: &str, accounts: &[String]) -> Result<(), Fault> {
        let now = mm_kernel::when::iso(self.clock.now());
        self.db.write(|c| {
            c.prepare_cached(
                "INSERT OR IGNORE INTO unsub_log(from_addr,method,status,http_status,ts,idempotency_key,accounts) \
                 VALUES(?,?,?,?,?,?,?)",
            )?
            .execute(params![
                from_addr,
                tried.method,
                tried.status,
                tried.http_status,
                now,
                key,
                py_json(&json!(accounts))
            ])?;
            Ok(())
        })
    }
}

impl SweepTrail for CurationStore {
    fn swept(&self, account: &str, rows: &[Audited]) -> Result<(), Fault> {
        if rows.is_empty() {
            return Ok(());
        }
        let stamp = mm_kernel::when::iso(self.clock.now());
        let records: Vec<Value> = rows
            .iter()
            .map(|r| {
                json!({"kind": "sweep", "ts": stamp, "account": account, "uid": r.uid, "from": r.from,
                       "subject": r.subject.chars().take(SUBJECT_KEPT).collect::<String>(), "bucket": r.bucket,
                       "reason": r.reason})
            })
            .collect();
        self.db.write(|c| append(c, &records))
    }

    fn cleaned_up(&self, from_addr: &str, token: &str, swept: usize, status: &str) -> Result<(), Fault> {
        let record = json!({"kind": "sender_cleanup", "ts": mm_kernel::when::iso(self.clock.now()), "from": from_addr,
                            "token": token, "swept": swept, "status": status});
        self.db.write(|c| append(c, &[record]))
    }
}

impl SweptRows for CurationStore {
    fn forget_moved(&self, account: &str, moved: &[(String, String)], stragglers: &[String]) -> Result<(), Fault> {
        let now = self.clock.now().whole_seconds();
        self.db.write(|c| {
            for (uid, mid) in moved {
                if stragglers.contains(uid) {
                    continue;
                }
                let pk: Option<i64> = if mid.is_empty() {
                    // No Message-ID: by UID, as the server numbers it now.
                    let Ok(uid) = uid.parse::<i64>() else { continue };
                    c.query_row(
                        "SELECT pk FROM messages WHERE account=? AND folder='INBOX' AND uid=?",
                        params![account, uid],
                        |r| r.get(0),
                    )
                    .optional()?
                } else {
                    c.query_row(
                        "SELECT pk FROM messages WHERE account=? AND folder='INBOX' AND message_id=?",
                        params![account, mid],
                        |r| r.get(0),
                    )
                    .optional()?
                };
                if let Some(pk) = pk {
                    delete_message(c, pk, now)?;
                }
            }
            Ok(())
        })
    }
}

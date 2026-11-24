//! The outbox and its delivery (`compose.application.outbox.Outbox`): a durable queue of sends and the pass that
//! delivers what is due.
//!
//! A send is queued once `build_message` has judged it; a pass claims it (queued to sending, which only one caller
//! wins), delivers it over SMTP holding no IMAP gate, then does what a delivered send owes: the copy in Sent where the
//! server doesn't file one itself, \Answered on the original, a follow-up, and the draft it came from removed. Each is
//! a job of its own on its account's gate, and each is owed until its done-once flag is set. Exactly once on the wire
//! is the `delivered` flag, set the instant the wire send returns: a row requeued after it only finishes its
//! bookkeeping. A try that fails backs off (20 s doubling to 15 minutes) and gives up after MAX_ATTEMPTS; a server
//! that refused for good gives up at once, and one that went silent once it had the whole message is left in error
//! for a person, who is told it may go twice. A try that panics is one that failed, judged by how far its row got:
//! one that went out owes its bookkeeping, one whose last bytes had begun to go waits for a person, and any other
//! backs off.
//!
//! Each account's sends go in a lane of their own, so one waiting on its IMAP server for a Sent copy holds up no other
//! account's. `deliver` runs each lane on a thread of its own and waits for them all; a lane is free again however its
//! thread ends. The worker that calls it is the program's: it waits `until_due`, and a send queued, retried or brought
//! forward wakes it (`Worker`).
//!
//! What happened is said as Python's outbox says it: Sent, Failed and DraftCopyRemoved (`Events`).

use std::collections::{BTreeMap, HashSet};
use std::fmt;
use std::sync::{Arc, Condvar, Mutex, PoisonError};
use std::time::Duration;

use mm_kernel::errors::{Fault, PANIC, READ_ONLY, caught};
use mm_kernel::log::{self, Field};
use mm_kernel::seams::Clock;
use mm_kernel::when::iso;
use serde_json::{Value, json};

use crate::builder;
use crate::draft;
use crate::draft_journal;
use crate::events::ComposeEvent;
use crate::outbox_item::{self, FailedTry, OriginalRow, SendMeta};
use crate::ports::Row;
use crate::sending_ports::{
    self, Accounts, Book, ComposeStore, Copies, DraftRow, Effects, Events, Gate, GateWait, Mark, OutboxRow, Priority,
    Screen, Transport, Worker, on_gate,
};
use crate::spec::Built;

const TAG: &str = "outbox";

/// How long a Retry waits for the account's gate, to look in Sent first.
pub const LOOK_IN_SENT_WAIT: Duration = Duration::from_secs(5);
/// The worker wakes at least this often, in seconds, to catch due retries.
pub const POLL: i64 = 30;
/// What a try that panicked before its send went out is stored with, as one that failed outside the SMTP
/// conversation is.
pub const PANICKED: &str = "send failed";
/// What a try that panicked once its send went out is stored with: the rest is tried again.
pub const PANICKED_AFTER: &str = "sent; finishing it failed: Panic";

/// What wires the outbox.
#[derive(Debug)]
pub struct DeliveryParts {
    pub store: Arc<dyn ComposeStore>,
    pub transport: Arc<dyn Transport>,
    pub copies: Arc<dyn Copies>,
    pub accounts: Arc<dyn Accounts>,
    pub effects: Arc<dyn Effects>,
    pub screen: Arc<dyn Screen>,
    /// Where Sent, Failed and DraftCopyRemoved go.
    pub events: Arc<dyn Events>,
    /// The worker a send wakes. In the served suites none waits on it: they run each pass themselves.
    pub worker: Arc<dyn Worker>,
    pub gate: Arc<dyn Gate>,
    pub clock: Arc<dyn Clock>,
    /// Whether this process writes to mail servers (`profile.writes_allowed`): a read-only one queues no send, and a
    /// send queued before waits for a run that writes.
    pub writes: bool,
}

pub struct Delivery {
    parts: DeliveryParts,
    /// The lanes a pass is running now, each an account.
    busy: Mutex<HashSet<String>>,
    freed: Condvar,
}

impl fmt::Debug for Delivery {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Delivery").field("writes", &self.parts.writes).finish_non_exhaustive()
    }
}

/// An account's lane, held by one pass: free again when dropped, however the pass's thread ends, so a later pass
/// never waits on it for good.
struct Lane<'a> {
    delivery: &'a Delivery,
    lane: String,
}

impl Drop for Lane<'_> {
    fn drop(&mut self) {
        self.delivery.free_lane(&self.lane);
    }
}

fn text(row: &Row, name: &str) -> Option<String> {
    row.get(name).and_then(Value::as_str).map(str::to_owned)
}

impl Delivery {
    pub fn new(parts: DeliveryParts) -> Delivery {
        Delivery { parts, busy: Mutex::new(HashSet::new()), freed: Condvar::new() }
    }

    /// The clock's seconds now.
    pub fn now(&self) -> i64 {
        self.parts.clock.now().whole_seconds()
    }

    fn stamp(&self) -> String {
        iso(self.parts.clock.now())
    }

    fn write<T>(&self, work: impl FnOnce(&dyn Book) -> Result<T, Fault>) -> Result<T, Fault> {
        sending_ports::write(&*self.parts.store, work)
    }

    fn read<T>(&self, work: impl FnOnce(&dyn Book) -> Result<T, Fault>) -> Result<T, Fault> {
        sending_ports::read(&*self.parts.store, work)
    }

    /// The seconds a send waits before it goes, so Undo can stop it: what a person has set, or the default. Undo
    /// Send's countdown counts it, so the page takes it from the answer.
    pub fn send_delay(&self) -> i64 {
        self.parts.accounts.undo_send_seconds().unwrap_or(outbox_item::SEND_DELAY)
    }

    /// The row of the message a send replies to (`_Messages.original`): by Message-ID, since a pk may have been
    /// renumbered, else by the pk it was queued with.
    fn original(
        &self,
        pk: Option<i64>,
        account: Option<&str>,
        message_id: Option<&str>,
    ) -> Result<Option<OriginalRow>, Fault> {
        let row = self.read(|book| {
            if let (Some(account), Some(mid)) = (account, message_id.filter(|m| !m.is_empty()))
                && let Some(row) = book.message_by_msgid(account, Some(mid))?
            {
                return Ok(Some(row));
            }
            match pk.filter(|pk| *pk != 0) {
                Some(pk) => book.message(pk),
                None => Ok(None),
            }
        })?;
        Ok(row.map(|row| OriginalRow {
            account: text(&row, "account").unwrap_or_default(),
            message_id: text(&row, "message_id"),
            thread_id: text(&row, "thread_id"),
            message_key: row.get("message_key").and_then(Value::as_i64),
        }))
    }

    /// A read-only process sends nothing, and queues nothing to send later: it refuses before a row is written.
    fn refuse_writes(&self) -> Result<(), Fault> {
        if self.parts.writes { Ok(()) } else { Err(READ_ONLY) }
    }

    /// Queue a send that `build_message` built (`enqueue`): (its id, whether it is new, false for a double submit). A
    /// read-only process refuses it (`READ_ONLY`): a send, a reply, an invite's answer or an unsubscribe by mail.
    pub fn enqueue(&self, built: &Built, meta: &SendMeta) -> Result<(Option<i64>, bool), Fault> {
        self.refuse_writes()?;
        let orig = self.original(meta.in_reply_to_pk, None, None)?;
        let row = outbox_item::new_row(built, meta, orig.as_ref(), self.now() + self.send_delay(), &self.stamp());
        let queued = self.write(|book| book.outbox_insert(&row))?;
        self.wake();
        Ok(queued)
    }

    /// Undo Send: drop the row while it is still in its window. False once it's too late.
    pub fn cancel(&self, id: i64) -> Result<bool, Fault> {
        self.write(|book| book.outbox_cancel_queued(id))
    }

    /// A person's Retry. On an unsure row it may send what went out already: they were told. Where the server files
    /// sent mail itself, a copy in Sent settles that first, and only the row's bookkeeping is left. A read-only process
    /// refuses it.
    pub fn retry(&self, id: i64) -> Result<Value, Fault> {
        self.refuse_writes()?;
        let went_out = self.in_sent_already(id);
        let ok = self.write(|book| {
            let ok = book.outbox_requeue(id)?;
            if ok {
                book.outbox_set(id, &outbox_item::RETRIED)?;
                if went_out {
                    book.outbox_delivered(id)?;
                }
            }
            Ok(ok)
        })?;
        if ok {
            self.wake();
        }
        Ok(if ok && went_out { json!({"ok": true, "gone": true}) } else { json!({"ok": ok}) })
    }

    /// Is an unsure send in Sent? False whenever that can't be told.
    fn in_sent_already(&self, id: i64) -> bool {
        let row = match self.read(|book| book.outbox_get(id)) {
            Ok(Some(row)) if outbox_item::is_unsure(&row) => row,
            _ => return false,
        };
        let Ok(Some(account)) = self.parts.accounts.sending(&row.account) else { return false };
        if !account.saves_sent {
            return false;
        }
        let looked =
            on_gate(&*self.parts.gate, &account.email, Priority::Interactive, GateWait::For(LOOK_IN_SENT_WAIT), || {
                self.parts.copies.in_sent(&account.email, &row.message_id)
            });
        match looked {
            Ok(Ok(found)) => found,
            Ok(Err(failed)) => {
                log::warning(TAG, "couldn't look in Sent", &[(Field::Id, &id.to_string())], Some(failed.class));
                false
            }
            Err(_) => {
                log::warning(TAG, "couldn't look in Sent", &[(Field::Id, &id.to_string())], Some("Busy"));
                false
            }
        }
    }

    /// Bring a scheduled send forward to now (Send now in the tray): false when it isn't queued. A read-only process
    /// refuses it: it would only wait for a run that writes.
    pub fn send_now(&self, id: i64) -> Result<bool, Fault> {
        self.refuse_writes()?;
        let now = self.now();
        let ok = self.write(|book| book.outbox_send_now(id, now))?;
        if ok {
            self.wake();
        }
        Ok(ok)
    }

    /// Whether a draft's copy may be removed (`draft_copy_is_ours`).
    pub fn draft_copy_is_ours(&self, d: &DraftRow) -> bool {
        draft::copy_is_ours(
            d,
            |folder| self.parts.screen.names_drafts(folder),
            || {
                let folder = d.folder.clone().unwrap_or_default();
                let uid = d.uid.unwrap_or(0);
                self.read(|book| book.flagged_draft(&d.account, &folder, uid)).unwrap_or(false)
            },
        )
    }

    /// The pk a send's original has now, for one with no Message-ID to go by (`_original_pk`).
    fn original_pk(&self, row: &OutboxRow) -> Option<i64> {
        if row.orig_message_id.as_deref().is_some_and(|m| !m.is_empty()) || row.original_key.unwrap_or(0) == 0 {
            return None;
        }
        let account = row.orig_account.clone().unwrap_or_default();
        let key = row.original_key.unwrap_or(0);
        let named = self.read(|book| book.message_by_key(&account, key)).ok().flatten()?;
        named.get("pk").and_then(Value::as_i64)
    }

    /// What a delivered send owes, each done once and only until its flag is set (`_side_effects`). Each IMAP step
    /// is a job of its own on its account's gate. What failed is said back, scrubbed, for `send_one` to try again.
    fn side_effects(&self, id: i64, row: &OutboxRow, saves_sent: bool) -> Result<Option<String>, Fault> {
        let mut owed: Option<String> = None;
        let owes = outbox_item::owed(row, saves_sent);
        if owes.sent_filed_for_us {
            self.write(|book| book.outbox_set(id, &[Mark::SentAppended]))?;
        } else if owes.sent_copy {
            let look_for = outbox_item::sent_copy_to_look_for(row);
            let copied = on_gate(&*self.parts.gate, &row.account, Priority::AfterUser, GateWait::Ever, || {
                self.parts.copies.append_to_sent(&row.account, &row.raw, look_for)
            });
            match copied {
                Ok(Ok(_)) => self.write(|book| book.outbox_set(id, &[Mark::SentAppended]))?,
                Ok(Err(failed)) => {
                    owed = Some(format!("sent copy failed: {}", failed.class));
                    log::warning(TAG, "Sent copy failed", &[(Field::Id, &id.to_string())], Some(failed.class));
                }
                Err(_) => owed = Some("sent copy failed: Busy".to_owned()),
            }
        }
        let mut answered_now = false;
        let orig_account = row.orig_account.clone().unwrap_or_default();
        if owes.answered {
            let pk = self.original_pk(row);
            let account = &orig_account;
            let marked = on_gate(&*self.parts.gate, account, Priority::AfterUser, GateWait::Ever, || {
                self.parts.effects.mark_answered(pk, account, row.orig_message_id.as_deref())
            });
            match marked {
                Ok(Ok(found)) => {
                    answered_now = found;
                    self.write(|book| book.outbox_set(id, &[Mark::AnsweredSet]))?;
                }
                Ok(Err(failed)) => {
                    owed.get_or_insert_with(|| format!("marking the original answered failed: {}", failed.class));
                    log::warning(TAG, "\\Answered failed", &[(Field::Id, &id.to_string())], Some(failed.class));
                }
                Err(_) => {
                    owed.get_or_insert_with(|| "marking the original answered failed: Busy".to_owned());
                }
            }
        }
        if owes.followup {
            // Best effort: it never fails the send, and nothing is asked again for it.
            let recorded = (|| -> Result<(), Fault> {
                let orig =
                    self.original(self.original_pk(row), row.orig_account.as_deref(), row.orig_message_id.as_deref())?;
                let thread = outbox_item::thread_of_reply(row, orig.as_ref().and_then(|o| o.thread_id.as_deref()));
                if let Some(thread) = thread {
                    self.parts.effects.followup_sent(&row.account, &thread, self.now(), &self.stamp())?;
                }
                self.write(|book| book.outbox_set(id, &[Mark::FollowupRecorded]))
            })();
            if let Err(fault) = recorded {
                log::warning(TAG, "follow-up record failed", &[(Field::Id, &id.to_string())], Some(fault.class));
            }
        }
        if answered_now {
            // After the follow-up step, so a reply to a thread that waited on you starts no `sent` one of its own.
            self.parts.effects.answered(&orig_account);
        }
        if owes.draft
            && let Err(why) = self.draft_cleanup(id, row)
        {
            owed.get_or_insert_with(|| format!("removing the draft's copy failed: {why}"));
            log::warning(TAG, "draft cleanup failed", &[(Field::Id, &id.to_string())], Some(why));
        }
        Ok(owed)
    }

    /// The draft a delivered send came from: its copy goes, and its row with it, under its account's gate.
    fn draft_cleanup(&self, id: i64, row: &OutboxRow) -> Result<(), &'static str> {
        let draft_id = row.draft_id.unwrap_or(0);
        let found = self.read(|book| book.draft_get(draft_id)).map_err(|f| f.class)?;
        if let Some(d) = found {
            let account = d.account.clone();
            let done = on_gate(
                &*self.parts.gate,
                &account,
                Priority::AfterUser,
                GateWait::Ever,
                || -> Result<bool, &'static str> {
                    let removed = self.draft_copy_is_ours(&d)
                        && self
                            .parts
                            .copies
                            .delete_draft_uid(&d.account, d.folder.as_deref(), d.uid, d.uidvalidity)
                            .map_err(|f| f.class)?;
                    let waiting = self.read(|book| book.journal_get(draft_id)).map_err(|f| f.class)?;
                    if let Some(left) = draft_journal::left_by_a_try(waiting.as_ref(), &d.account) {
                        self.parts.copies.delete_left_copy(&d.account, left).map_err(|f| f.class)?;
                    }
                    let now = self.now();
                    self.write(|book| {
                        book.draft_delete(draft_id)?;
                        if removed {
                            book.remove_copies(
                                &d.account,
                                d.folder.as_deref().unwrap_or_default(),
                                &[d.uid.unwrap_or(0)],
                                now,
                            )?;
                        }
                        Ok(())
                    })
                    .map_err(|f| f.class)?;
                    Ok(removed)
                },
            );
            let removed = done.map_err(|_| "Busy")??;
            if removed {
                let (account, folder) = (d.account.clone(), d.folder.clone().unwrap_or_default());
                self.parts.events.publish(ComposeEvent::DraftCopyRemoved { account, folder });
            }
        }
        self.write(|book| book.outbox_set(id, &[Mark::DraftDeleted])).map_err(|f| f.class)
    }

    /// A try at a send failed: it waits for its next, or, `gave_up`, for a person.
    fn failed(&self, row: &OutboxRow, gave_up: bool) {
        self.parts.events.publish(ComposeEvent::Failed { outbox_id: row.id, account: row.account.clone(), gave_up });
    }

    /// One try at a queued send (`send_one`). A try that panics, wherever it does, fails as any try does: it never
    /// leaves its send claimed until a restart.
    pub fn send_one(&self, id: i64) -> Result<(), Fault> {
        let Some(row) = self.write(|book| book.outbox_claim(id))? else { return Ok(()) };
        caught(|| self.try_claimed(&row)).unwrap_or_else(|_| self.panicked(&row))
    }

    /// A try at a send this caller has claimed: `row` is it as claimed.
    fn try_claimed(&self, row: &OutboxRow) -> Result<(), Fault> {
        let id = row.id;
        let account = match self.parts.accounts.sending(&row.account) {
            Ok(Some(account)) => account,
            _ => {
                let attempts = row.attempts + 1;
                self.write(|book| book.outbox_give_up(id, attempts, "account removed"))?;
                self.failed(row, true);
                return Ok(());
            }
        };
        // Its Date says when it leaves, not when it was written: a send scheduled for later, or one that waited out its
        // server, went out dated hours back. The copy in Sent is these bytes too, and the Message-ID stays.
        let restamped;
        let row = if row.delivered {
            row
        } else {
            let raw = builder::restamped(&row.raw, self.parts.clock.now());
            self.write(|book| book.outbox_set_raw(id, &raw))?;
            restamped = OutboxRow { raw, ..row.clone() };
            &restamped
        };
        if !row.delivered {
            let envelope: Vec<String> = serde_json::from_str(&row.envelope).unwrap_or_default();
            let mut marked: Result<(), Fault> = Ok(());
            let sent = self.parts.transport.send(&row.account, &row.raw, &envelope, &mut || {
                marked = self.write(|book| book.outbox_set(id, &[Mark::WireBegun(true)]));
            });
            marked?;
            if let Err(failed) = sent {
                return self
                    .try_failed(row, &outbox_item::failed_try(failed.unanswered, failed.permanent, failed.said));
            }
            self.write(|book| book.outbox_delivered(id))?;
        }
        match self.side_effects(id, row, account.saves_sent) {
            Ok(Some(owed)) => self.still_owes(row, &owed)?,
            Ok(None) => {
                let at = self.stamp();
                self.write(|book| book.outbox_finish(id, &at))?;
                log::info(TAG, "sent", &[(Field::Id, &id.to_string())]);
            }
            Err(fault) => {
                log::warning(TAG, "post-send bookkeeping deferred", &[(Field::Id, &id.to_string())], Some(fault.class));
                return Ok(());
            }
        }
        self.parts.events.publish(ComposeEvent::Sent { outbox_id: id, account: row.account.clone() });
        Ok(())
    }

    /// A try that didn't deliver `row`, as claimed: back in the queue after its back-off, or to error when it gives
    /// up or has had its last attempt.
    fn try_failed(&self, row: &OutboxRow, tried: &FailedTry) -> Result<(), Fault> {
        let id = row.id;
        let now = self.now();
        let terminal = self.write(|book| {
            book.outbox_set(id, &tried.marks)?;
            let attempts = row.attempts + 1;
            if tried.give_up {
                book.outbox_give_up(id, attempts, tried.error)?;
                return Ok(true);
            }
            let terminal = attempts >= outbox_item::MAX_ATTEMPTS;
            book.outbox_retry_later(id, attempts, terminal, now + outbox_item::backoff(attempts), tried.error)?;
            Ok(terminal)
        })?;
        let what = if tried.unsure {
            "went unanswered; not sending it again"
        } else if terminal {
            "failed; giving up"
        } else {
            "failed; trying again later"
        };
        log::warning(TAG, "send", &[(Field::Id, &id.to_string()), (Field::What, what)], Some(tried.error));
        self.failed(row, terminal);
        Ok(())
    }

    /// `row`, as claimed, went out, and `owed` says what it still owes: that is tried again after a back-off.
    fn still_owes(&self, row: &OutboxRow, owed: &str) -> Result<(), Fault> {
        let attempts = outbox_item::attempts_since_delivery(row) + 1;
        let terminal = attempts >= outbox_item::MAX_ATTEMPTS;
        let next = self.now() + outbox_item::backoff(attempts);
        self.write(|book| book.outbox_retry_later(row.id, attempts, terminal, next, owed))?;
        log::warning(TAG, "sent; what it still owes is tried again", &[(Field::Id, &row.id.to_string())], None);
        Ok(())
    }

    /// A try at `row`, as claimed, panicked. As it stands now: one that went out still owes its bookkeeping; one
    /// whose last bytes had begun to go may have gone, so it waits for a person; any other failed as a send does.
    /// One the try had already put back or finished is left as it is.
    fn panicked(&self, row: &OutboxRow) -> Result<(), Fault> {
        log::warning(TAG, "a try at a send panicked", &[(Field::Id, &row.id.to_string())], Some(PANIC.class));
        let Some(now) = self.read(|book| book.outbox_get(row.id))? else { return Ok(()) };
        if now.state != "sending" {
            return Ok(());
        }
        if now.delivered {
            return self.still_owes(row, PANICKED_AFTER);
        }
        self.try_failed(row, &outbox_item::failed_try(now.wire_begun, false, PANICKED))
    }

    /// One pass of the outbox (`drain`): every send due now, each account's in its lane on a thread of its own, one
    /// lane after another's busy pass. It returns once every lane has run. One send that fails is logged and the rest
    /// still go. A read-only process sends nothing and queued none: what a full run queued waits for the next.
    pub fn deliver(&self) -> Result<(), Fault> {
        if !self.parts.writes {
            return Ok(());
        }
        let due = self.read(|book| book.outbox_due(self.now()))?;
        let mut lanes: BTreeMap<String, Vec<i64>> = BTreeMap::new();
        for (id, lane) in due {
            lanes.entry(lane).or_default().push(id);
        }
        std::thread::scope(|scope| {
            for (lane, ids) in lanes {
                let held = self.take_lane(lane);
                scope.spawn(move || {
                    // Free again however this ends.
                    let _held = held;
                    for id in ids {
                        // A send that panicked is put right in send_one; this catches what panics around it.
                        if let Err(fault) = caught(|| self.send_one(id)).and_then(|sent| sent) {
                            log::error(TAG, "send errored", &[(Field::Id, &id.to_string())], Some(fault.class));
                        }
                    }
                });
            }
        });
        Ok(())
    }

    /// Wait until no other pass runs `lane`, then hold it until what this answers is dropped.
    fn take_lane(&self, lane: String) -> Lane<'_> {
        let mut busy = self.busy.lock().unwrap_or_else(PoisonError::into_inner);
        while busy.contains(&lane) {
            busy = self.freed.wait(busy).unwrap_or_else(PoisonError::into_inner);
        }
        busy.insert(lane.clone());
        Lane { delivery: self, lane }
    }

    fn free_lane(&self, lane: &str) {
        self.busy.lock().unwrap_or_else(PoisonError::into_inner).remove(lane);
        self.freed.notify_all();
    }

    /// At start: sends a crash left going go back to the queue, but one that may have gone out to error, for a
    /// person to decide (`reset_stuck`).
    pub fn recover(&self) -> Result<(), Fault> {
        self.write(|book| book.outbox_reset_stuck(outbox_item::UNSURE))
    }

    /// Seconds from now until the next queued send comes due, at most POLL, and none if one has come due since the
    /// last pass began to look (`looked`): that pass may have missed it, and no wake is coming (`until_due`). A send
    /// due before it looked isn't waited for: a wake brings the pass that takes it.
    pub fn until_due(&self, looked: i64) -> i64 {
        let now = self.now();
        match self.read(|book| book.outbox_next_due(looked)) {
            Ok(Some(due)) => (due - now).clamp(0, POLL),
            Ok(None) => POLL,
            Err(fault) => {
                // The poll still comes.
                log::warning(TAG, "next due", &[], Some(fault.class));
                POLL
            }
        }
    }

    /// Wake the worker: a send was queued, retried or brought forward.
    fn wake(&self) {
        self.parts.worker.wake();
    }
}

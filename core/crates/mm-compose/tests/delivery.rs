//! A try at a send that panics, as the outbox's worker meets one: it fails as any try does, judged by how far its
//! row got, its account's lane is free again for the next pass, and the lane's other sends still go.

#![allow(clippy::unwrap_used)]

use std::sync::atomic::{AtomicBool, AtomicI64, AtomicUsize, Ordering};
use std::sync::mpsc;
use std::sync::{Arc, Mutex, PoisonError};
use std::time::Duration;

use mm_compose::delivery::{Delivery, DeliveryParts, PANICKED, PANICKED_AFTER};
use mm_compose::events::ComposeEvent;
use mm_compose::outbox_item::{MAX_ATTEMPTS, SendMeta, UNSURE};
use mm_compose::ports::{Row, Tracked};
use mm_compose::sending_ports::{
    Accounts, Appended, Book, Busy, ComposeStore, Copies, CopyFailed, DraftRow, Effects, Events, Gate, GateWait,
    InPlace, Mark, NewSend, NotSent, OutboxRow, Priority, Quote, Record, Save, Screen, SendingAccount, Transport,
    Waiting, Worker,
};
use mm_compose::spec::Built;
use mm_kernel::errors::{Fault, READ_ONLY};
use mm_kernel::seams::{Clock, Moment};

const ADA: &str = "ada@example.test";
/// What the wire panics on before a byte goes, and once the last bytes have begun to.
const PANICS: &[u8] = b"panics";
const PANICS_AT_THE_DOT: &[u8] = b"panics at the dot";

/// The outbox's rows, as the store keeps them.
#[derive(Debug, Default)]
struct Outbox {
    rows: Mutex<Vec<OutboxRow>>,
}

impl Outbox {
    fn queue(&self, id: i64, raw: &[u8]) {
        self.rows.lock().unwrap().push(OutboxRow {
            id,
            account: ADA.to_owned(),
            message_id: format!("<{id}@example.test>"),
            envelope: r#"["lin@example.test"]"#.to_owned(),
            raw: raw.to_vec(),
            state: "queued".to_owned(),
            wait_kind: "grace".to_owned(),
            ..OutboxRow::default()
        });
    }

    fn row(&self, id: i64) -> OutboxRow {
        self.rows.lock().unwrap().iter().find(|r| r.id == id).unwrap().clone()
    }
}

impl ComposeStore for Outbox {
    fn write(&self, work: &mut dyn FnMut(&dyn Book) -> Result<(), Fault>) -> Result<(), Fault> {
        work(&Rows(&self.rows))
    }

    fn read(&self, work: &mut dyn FnMut(&dyn Book) -> Result<(), Fault>) -> Result<(), Fault> {
        work(&Rows(&self.rows))
    }
}

/// A block of work over the rows: only the outbox's queue is asked of it.
struct Rows<'a>(&'a Mutex<Vec<OutboxRow>>);

impl Rows<'_> {
    fn with<T>(&self, id: i64, change: impl FnOnce(&mut OutboxRow) -> T) -> Option<T> {
        self.0.lock().unwrap_or_else(PoisonError::into_inner).iter_mut().find(|r| r.id == id).map(change)
    }
}

impl Book for Rows<'_> {
    fn outbox_get(&self, id: i64) -> Result<Option<OutboxRow>, Fault> {
        Ok(self.with(id, |r| r.clone()))
    }

    fn outbox_set(&self, id: i64, marks: &[Mark]) -> Result<(), Fault> {
        self.with(id, |r| {
            for mark in marks {
                match *mark {
                    Mark::WireBegun(begun) => r.wire_begun = begun,
                    Mark::SentAppended => r.sent_appended = true,
                    Mark::AnsweredSet => r.answered_set = true,
                    Mark::DraftDeleted => r.draft_deleted = true,
                    Mark::FollowupRecorded => r.followup_recorded = true,
                    Mark::WaitKind(kind) => kind.clone_into(&mut r.wait_kind),
                }
            }
        });
        Ok(())
    }

    fn outbox_set_raw(&self, id: i64, raw: &[u8]) -> Result<(), Fault> {
        self.with(id, |r| raw.clone_into(&mut r.raw));
        Ok(())
    }

    fn outbox_due(&self, now: i64) -> Result<Vec<(i64, String)>, Fault> {
        let rows = self.0.lock().unwrap_or_else(PoisonError::into_inner);
        let due = rows.iter().filter(|r| r.state == "queued" && r.next_attempt_at <= now);
        Ok(due.map(|r| (r.id, r.account.clone())).collect())
    }

    fn outbox_claim(&self, id: i64) -> Result<Option<OutboxRow>, Fault> {
        Ok(self
            .with(id, |r| {
                (r.state == "queued").then(|| {
                    "sending".clone_into(&mut r.state);
                    r.clone()
                })
            })
            .flatten())
    }

    fn outbox_retry_later(
        &self,
        id: i64,
        attempts: i64,
        terminal: bool,
        next_at: i64,
        error: &str,
    ) -> Result<(), Fault> {
        self.with(id, |r| {
            r.state = if terminal { "error" } else { "queued" }.to_owned();
            (r.attempts, r.next_attempt_at, r.last_error) = (attempts, next_at, Some(error.to_owned()));
        });
        Ok(())
    }

    fn outbox_give_up(&self, id: i64, attempts: i64, error: &str) -> Result<(), Fault> {
        self.with(id, |r| {
            "error".clone_into(&mut r.state);
            (r.attempts, r.next_attempt_at, r.last_error) = (attempts, 0, Some(error.to_owned()));
        });
        Ok(())
    }

    fn outbox_delivered(&self, id: i64) -> Result<(), Fault> {
        self.with(id, |r| r.delivered = true);
        Ok(())
    }

    fn outbox_finish(&self, id: i64, at: &str) -> Result<(), Fault> {
        self.with(id, |r| {
            "sent".clone_into(&mut r.state);
            r.sent_at = Some(at.to_owned());
            r.last_error = None;
        });
        Ok(())
    }

    fn outbox_insert(&self, _row: &NewSend) -> Result<(Option<i64>, bool), Fault> {
        unreachable!("a send never asks it")
    }

    fn outbox_send_now(&self, _id: i64, _now: i64) -> Result<bool, Fault> {
        unreachable!("a send never asks it")
    }

    fn outbox_holds_draft(&self, _draft_id: i64) -> Result<bool, Fault> {
        unreachable!("a send never asks it")
    }

    fn outbox_keys(&self, _prefix: &str) -> Result<Vec<String>, Fault> {
        unreachable!("a send never asks it")
    }

    fn outbox_reset_stuck(&self, _unsure: &str) -> Result<(), Fault> {
        unreachable!("a send never asks it")
    }

    fn outbox_requeue(&self, _id: i64) -> Result<bool, Fault> {
        unreachable!("a send never asks it")
    }

    fn outbox_cancel_queued(&self, _id: i64) -> Result<bool, Fault> {
        unreachable!("a send never asks it")
    }

    fn outbox_next_due(&self, _after: i64) -> Result<Option<i64>, Fault> {
        unreachable!("a send never asks it")
    }

    fn draft_get(&self, _id: i64) -> Result<Option<DraftRow>, Fault> {
        unreachable!("a send never asks it")
    }

    fn draft_by_key(&self, _key: &str) -> Result<Option<DraftRow>, Fault> {
        unreachable!("a send never asks it")
    }

    fn draft_set_key(&self, _id: i64, _key: &str) -> Result<(), Fault> {
        unreachable!("a send never asks it")
    }

    fn draft_by_msgid(&self, _account: &str, _message_id: Option<&str>) -> Result<Option<DraftRow>, Fault> {
        unreachable!("a send never asks it")
    }

    fn draft_insert(&self, _draft: &Tracked) -> Result<i64, Fault> {
        unreachable!("a send never asks it")
    }

    fn draft_set_resume(&self, _id: i64, _record: &Record) -> Result<(), Fault> {
        unreachable!("a send never asks it")
    }

    fn flagged_draft(&self, _account: &str, _folder: &str, _uid: i64) -> Result<bool, Fault> {
        unreachable!("a send never asks it")
    }

    fn draft_repoint(
        &self,
        _seen: &DraftRow,
        _folder: &str,
        _uid: i64,
        _uidvalidity: Option<i64>,
        _forget: bool,
    ) -> Result<bool, Fault> {
        unreachable!("a send never asks it")
    }

    fn draft_delete(&self, _id: i64) -> Result<(), Fault> {
        unreachable!("a send never asks it")
    }

    fn draft_note_save(
        &self,
        _id: i64,
        _mode: Option<&str>,
        _subject: Option<&str>,
        _version: i64,
        _saved_by: Option<&str>,
        _updated_at: &str,
        _in_reply_to: Option<&str>,
    ) -> Result<(), Fault> {
        unreachable!("a send never asks it")
    }

    fn draft_answers_nothing(&self, _seen: &DraftRow) -> Result<(), Fault> {
        unreachable!("a send never asks it")
    }

    fn draft_note_discard(&self, _id: i64, _version: i64, _updated_at: &str) -> Result<(), Fault> {
        unreachable!("a send never asks it")
    }

    fn draft_settle_push(
        &self,
        _id: i64,
        _account: &str,
        _folder: &str,
        _uid: Option<i64>,
        _message_id: &str,
        _uidvalidity: Option<i64>,
        _pushed_at: i64,
    ) -> Result<(), Fault> {
        unreachable!("a send never asks it")
    }

    fn draft_quote(&self, _id: i64) -> Result<Option<Quote>, Fault> {
        unreachable!("a send never asks it")
    }

    fn journal_get(&self, _draft_id: i64) -> Result<Option<Waiting>, Fault> {
        unreachable!("a send never asks it")
    }

    fn journal_put(&self, _save: &Save<'_>) -> Result<(), Fault> {
        unreachable!("a send never asks it")
    }

    fn journal_discard(&self, _draft_id: i64, _version: i64, _account: &str, _now: i64) -> Result<(), Fault> {
        unreachable!("a send never asks it")
    }

    fn journal_tried(&self, _draft_id: i64, _version: i64, _message_id: &str) -> Result<bool, Fault> {
        unreachable!("a send never asks it")
    }

    fn journal_settled(&self, _draft_id: i64, _version: i64) -> Result<(), Fault> {
        unreachable!("a send never asks it")
    }

    fn journal_failed(&self, _draft_id: i64, _attempts: i64, _due_at: i64) -> Result<(), Fault> {
        unreachable!("a send never asks it")
    }

    fn journal_due(&self, _now: i64, _soon: i64) -> Result<Vec<i64>, Fault> {
        unreachable!("a send never asks it")
    }

    fn journal_next_due(&self, _after: i64, _soon: i64) -> Result<Option<i64>, Fault> {
        unreachable!("a send never asks it")
    }

    fn journal_move(&self, _draft_id: i64, _to: i64) -> Result<(), Fault> {
        unreachable!("a send never asks it")
    }

    fn message(&self, _pk: i64) -> Result<Option<Row>, Fault> {
        unreachable!("a send never asks it")
    }

    fn message_by_msgid(&self, _account: &str, _message_id: Option<&str>) -> Result<Option<Row>, Fault> {
        unreachable!("a send never asks it")
    }

    fn message_by_key(&self, _account: &str, _key: i64) -> Result<Option<Row>, Fault> {
        unreachable!("a send never asks it")
    }

    fn message_pk(&self, _key: i64) -> Result<Option<i64>, Fault> {
        unreachable!("a send never asks it")
    }

    fn body(&self, _pk: i64) -> Result<Option<Vec<u8>>, Fault> {
        unreachable!("a send never asks it")
    }

    fn uidvalidity(&self, _account: &str, _folder: &str) -> Result<Option<i64>, Fault> {
        unreachable!("a send never asks it")
    }

    fn remove_message(&self, _pk: i64, _now: i64) -> Result<(), Fault> {
        unreachable!("a send never asks it")
    }

    fn remove_copies(&self, _account: &str, _folder: &str, _uids: &[i64], _now: i64) -> Result<(), Fault> {
        unreachable!("a send never asks it")
    }
}

/// The SMTP server: it panics on what is meant to, and otherwise takes the message.
#[derive(Debug, Default)]
struct Wire {
    took: Mutex<Vec<Vec<u8>>>,
    /// Each message that reached it, whatever became of it.
    tries: AtomicUsize,
}

impl Transport for Wire {
    fn send(
        &self,
        _account: &str,
        raw: &[u8],
        _envelope: &[String],
        before_dot: &mut dyn FnMut(),
    ) -> Result<(), NotSent> {
        self.tries.fetch_add(1, Ordering::SeqCst);
        assert_ne!(raw, PANICS, "a panic whose words could quote mail");
        before_dot();
        assert_ne!(raw, PANICS_AT_THE_DOT, "a panic whose words could quote mail");
        self.took.lock().unwrap().push(raw.to_vec());
        Ok(())
    }
}

/// The account's Sent, on its IMAP server: its next copy panics while `panics` is set.
#[derive(Debug, Default)]
struct SentFolder {
    panics: AtomicBool,
    took: Mutex<Vec<Vec<u8>>>,
}

impl Copies for SentFolder {
    fn append_to_sent(&self, _account: &str, raw: &[u8], _message_id: Option<&str>) -> Result<String, CopyFailed> {
        assert!(!self.panics.swap(false, Ordering::SeqCst), "a panic whose words could quote mail");
        self.took.lock().unwrap().push(raw.to_vec());
        Ok("Sent".to_owned())
    }

    fn in_sent(&self, _account: &str, _message_id: &str) -> Result<bool, CopyFailed> {
        unreachable!("a send never asks it")
    }

    fn append_to_drafts(
        &self,
        _account: &str,
        _raw: &[u8],
        _prev: &InPlace,
        _message_id: Option<&str>,
        _tried: &[&str],
    ) -> Result<Appended, CopyFailed> {
        unreachable!("a send never asks it")
    }

    fn delete_draft_uid(
        &self,
        _account: &str,
        _folder: Option<&str>,
        _uid: Option<i64>,
        _uidvalidity: Option<i64>,
    ) -> Result<bool, CopyFailed> {
        unreachable!("a send never asks it")
    }

    fn delete_left_copy(&self, _account: &str, _message_id: &str) -> Result<bool, CopyFailed> {
        unreachable!("a send never asks it")
    }

    fn probe(&self, _account: &str, _folder: &str, _uid: i64, _timeout: Duration) -> Option<(Option<i64>, bool)> {
        unreachable!("a send never asks it")
    }

    fn fetch_body(&self, _pk: i64) -> Result<Option<Vec<u8>>, CopyFailed> {
        unreachable!("a send never asks it")
    }

    fn sync_whole(&self, _account: &str, _folder: &str) -> Result<(), CopyFailed> {
        unreachable!("a send never asks it")
    }
}

/// Ada's account, whose server files sent mail itself or doesn't.
#[derive(Debug)]
struct Ada {
    saves_sent: bool,
}

impl Accounts for Ada {
    fn sending(&self, email: &str) -> Result<Option<SendingAccount>, Fault> {
        Ok(Some(SendingAccount { email: email.to_owned(), name: "Ada".to_owned(), saves_sent: self.saves_sent }))
    }

    fn undo_send_seconds(&self) -> Option<i64> {
        None
    }
}

/// Nothing these sends set off: none is a reply.
#[derive(Debug)]
struct NoEffects;

impl Effects for NoEffects {
    fn mark_answered(&self, _pk: Option<i64>, _account: &str, _message_id: Option<&str>) -> Result<bool, CopyFailed> {
        unreachable!("none of these sends is a reply")
    }

    fn followup_sent(&self, _account: &str, _thread_id: &str, _now: i64, _at: &str) -> Result<(), Fault> {
        unreachable!("none of these sends is a reply")
    }

    fn answered(&self, _account: &str) {
        unreachable!("none of these sends is a reply")
    }
}

#[derive(Debug)]
struct NoScreen;

impl Screen for NoScreen {
    fn names_drafts(&self, _folder: &str) -> bool {
        false
    }

    fn plays_drafts(&self, _account: &str, _folder: &str) -> bool {
        false
    }

    fn touched(&self, _account: &str, _folder: &str) {}

    fn list_changed(&self, _account: &str, _folder: &str) {}

    fn outbox_changed(&self) {}

    fn stats_changed(&self) {}
}

/// What Compose said happened, in order.
#[derive(Debug, Default)]
struct Said(Mutex<Vec<ComposeEvent>>);

impl Events for Said {
    fn publish(&self, event: ComposeEvent) {
        self.0.lock().unwrap().push(event);
    }
}

#[derive(Debug)]
struct NoWorker;

impl Worker for NoWorker {
    fn wake(&self) {}
}

/// Every account's gate, always free.
#[derive(Debug)]
struct Open;

impl Gate for Open {
    fn run(&self, _account: &str, _priority: Priority, _wait: GateWait, job: &mut dyn FnMut()) -> Result<(), Busy> {
        job();
        Ok(())
    }
}

/// A clock a test moves on, in whole seconds.
#[derive(Debug, Default)]
struct Seconds(AtomicI64);

impl Clock for Seconds {
    fn now(&self) -> Moment {
        Moment::from_micros(self.0.load(Ordering::SeqCst) * 1_000_000)
    }
}

struct Sending {
    delivery: Arc<Delivery>,
    outbox: Arc<Outbox>,
    wire: Arc<Wire>,
    sent_folder: Arc<SentFolder>,
    said: Arc<Said>,
    clock: Arc<Seconds>,
}

impl Sending {
    fn new(saves_sent: bool) -> Sending {
        Sending::writing(saves_sent, true)
    }

    fn writing(saves_sent: bool, writes: bool) -> Sending {
        let (outbox, wire) = (Arc::new(Outbox::default()), Arc::new(Wire::default()));
        let sent_folder = Arc::new(SentFolder::default());
        let (said, clock) = (Arc::new(Said::default()), Arc::new(Seconds(AtomicI64::new(1_000))));
        let delivery = Arc::new(Delivery::new(DeliveryParts {
            store: outbox.clone(),
            transport: wire.clone(),
            copies: sent_folder.clone(),
            accounts: Arc::new(Ada { saves_sent }),
            effects: Arc::new(NoEffects),
            screen: Arc::new(NoScreen),
            events: said.clone(),
            worker: Arc::new(NoWorker),
            gate: Arc::new(Open),
            clock: clock.clone(),
            writes,
        }));
        Sending { delivery, outbox, wire, sent_folder, said, clock }
    }

    /// One pass of the outbox, which must come back: a lane a pass never freed would keep it waiting for good.
    fn pass(&self) {
        let (delivery, (done, finished)) = (self.delivery.clone(), mpsc::channel());
        std::thread::spawn(move || done.send(delivery.deliver()).unwrap());
        let delivered = finished.recv_timeout(Duration::from_secs(10)).expect("the pass never came back");
        assert_eq!(delivered, Ok(()));
    }

    fn later(&self, seconds: i64) {
        self.clock.0.fetch_add(seconds, Ordering::SeqCst);
    }

    fn said(&self) -> Vec<ComposeEvent> {
        std::mem::take(&mut *self.said.0.lock().unwrap())
    }
}

fn failed(outbox_id: i64, gave_up: bool) -> ComposeEvent {
    ComposeEvent::Failed { outbox_id, account: ADA.to_owned(), gave_up }
}

fn sent(outbox_id: i64) -> ComposeEvent {
    ComposeEvent::Sent { outbox_id, account: ADA.to_owned() }
}

#[test]
fn a_send_that_panics_fails_as_a_try_does_and_the_next_from_its_account_still_goes() {
    let sending = Sending::new(true);
    sending.outbox.queue(1, PANICS);
    sending.outbox.queue(2, b"Hi");
    sending.pass();
    let panicked = sending.outbox.row(1);
    assert_eq!(
        (panicked.state.as_str(), panicked.attempts, panicked.last_error.as_deref()),
        ("queued", 1, Some(PANICKED))
    );
    assert_eq!((panicked.next_attempt_at, panicked.wait_kind.as_str(), panicked.wire_begun), (1_020, "backoff", false));
    assert_eq!(sending.outbox.row(2).state, "sent");
    assert_eq!(*sending.wire.took.lock().unwrap(), [b"Hi".to_vec()]);
    assert_eq!(sending.said(), [failed(1, false), sent(2)]);

    // The lane was freed: each later pass takes it again, and the send gives up at its last attempt.
    for attempts in 2..=MAX_ATTEMPTS {
        sending.later(900);
        sending.pass();
        assert_eq!(sending.outbox.row(1).attempts, attempts);
    }
    let gave_up = sending.outbox.row(1);
    assert_eq!((gave_up.state.as_str(), gave_up.last_error.as_deref()), ("error", Some(PANICKED)));
    assert_eq!(sending.said().last(), Some(&failed(1, true)));
}

#[test]
fn a_send_that_panics_once_its_last_bytes_began_to_go_waits_for_a_person() {
    let sending = Sending::new(true);
    sending.outbox.queue(1, PANICS_AT_THE_DOT);
    sending.pass();
    let unsure = sending.outbox.row(1);
    assert_eq!((unsure.state.as_str(), unsure.attempts, unsure.last_error.as_deref()), ("error", 1, Some(UNSURE)));
    assert!(unsure.wire_begun && !unsure.delivered);
    assert_eq!(sending.said(), [failed(1, true)]);
    sending.later(900);
    sending.pass();
    assert_eq!(sending.wire.tries.load(Ordering::SeqCst), 1, "it went on the wire again");
}

#[test]
fn a_send_that_panics_once_it_went_out_owes_only_its_bookkeeping() {
    let sending = Sending::new(false);
    sending.sent_folder.panics.store(true, Ordering::SeqCst);
    sending.outbox.queue(1, b"Hi");
    sending.pass();
    let owing = sending.outbox.row(1);
    assert_eq!(
        (owing.state.as_str(), owing.attempts, owing.last_error.as_deref()),
        ("queued", 1, Some(PANICKED_AFTER))
    );
    assert!(owing.delivered && !owing.sent_appended);
    assert_eq!(sending.said(), []);

    sending.later(20);
    sending.pass();
    let finished = sending.outbox.row(1);
    assert_eq!((finished.state.as_str(), finished.sent_appended), ("sent", true));
    assert_eq!(sending.wire.tries.load(Ordering::SeqCst), 1, "it went on the wire again");
    assert_eq!(sending.said(), [sent(1)]);
}

#[test]
fn a_read_only_process_queues_no_send_asks_none_again_and_sends_none() {
    let sending = Sending::writing(true, false);
    sending.outbox.queue(1, b"Hi");
    // A row written would reach `outbox_insert`, which this world never answers: the refusal comes first.
    let meta = SendMeta { account: ADA.to_owned(), idempotency_key: "k-1".to_owned(), ..SendMeta::default() };
    assert_eq!(sending.delivery.enqueue(&Built::default(), &meta), Err(READ_ONLY));
    assert_eq!(sending.delivery.retry(1), Err(READ_ONLY));
    assert_eq!(sending.delivery.send_now(1), Err(READ_ONLY));
    sending.pass();
    assert_eq!(sending.outbox.row(1).state, "queued", "what a full run queued waits for the next");
    assert_eq!(sending.wire.tries.load(Ordering::SeqCst), 0);
    assert_eq!(sending.said(), []);
}

#[test]
fn a_send_goes_out_dated_as_it_leaves_with_its_message_id_kept_and_its_copy_in_sent_the_same() {
    let sending = Sending::new(false);
    let written: &[u8] = b"Date: Mon, 02 Nov 2026 12:00:00 +0000\r\nMessage-ID: <1@example.test>\r\n\r\nHi\r\n";
    sending.outbox.queue(1, written);
    sending.later(3 * 3600);
    sending.pass();
    let went: &[u8] = b"Date: Thu, 01 Jan 1970 03:16:40 +0000\r\nMessage-ID: <1@example.test>\r\n\r\nHi\r\n";
    assert_eq!(*sending.wire.took.lock().unwrap(), [went.to_vec()]);
    assert_eq!(*sending.sent_folder.took.lock().unwrap(), [went.to_vec()]);
    let row = sending.outbox.row(1);
    assert_eq!((row.state.as_str(), row.raw.as_slice()), ("sent", went));
}

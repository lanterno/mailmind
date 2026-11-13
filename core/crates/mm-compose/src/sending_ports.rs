//! What sending and saving need of the world (`compose.application.ports`): Compose's own tables a block of work at a
//! time (`Book`, which also reads the Mailbox's rows Compose needs), the wire (`Transport`), the copies kept on a mail
//! server (`Copies`), the sending accounts, what a delivered send sets off elsewhere (`Effects`), what the page is
//! told (`Screen`), and the gate each account's IMAP work passes (`Gate`). The store and the program implement them.
//!
//! A block (`Book`) is one transaction: what a use case reads in it decides what it writes, and no one comes between.
//! No block stays open across a network call: a use case commits before it asks a server anything.

use std::fmt;
use std::time::Duration;

use mm_kernel::errors::Fault;

use crate::ports::{Row, Tracked};

/// An outbox row as stored (`outbox`).
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct OutboxRow {
    pub id: i64,
    pub account: String,
    pub idempotency_key: String,
    pub message_id: String,
    /// JSON: every addr-spec it goes to, Bcc included.
    pub envelope: String,
    /// JSON: {to, cc, bcc}, for the tray.
    pub recipients: String,
    pub subject: Option<String>,
    /// The Bcc-less bytes: sent on the wire and appended to Sent.
    pub raw: Vec<u8>,
    pub state: String,
    pub attempts: i64,
    pub next_attempt_at: i64,
    pub delivered: bool,
    pub sent_appended: bool,
    pub answered_set: bool,
    pub followup_recorded: bool,
    pub last_error: Option<String>,
    pub draft_id: Option<i64>,
    pub draft_deleted: bool,
    pub created_at: String,
    pub sent_at: Option<String>,
    pub orig_account: Option<String>,
    pub orig_message_id: Option<String>,
    pub orig_thread_id: Option<String>,
    pub wire_begun: bool,
    pub wait_kind: String,
    pub original_key: Option<i64>,
}

/// A send as it goes into the outbox, queued (`outbox_item.new_row`).
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct NewSend {
    pub account: String,
    pub idempotency_key: String,
    pub message_id: String,
    pub orig_account: Option<String>,
    pub orig_message_id: Option<String>,
    pub orig_thread_id: Option<String>,
    pub original_key: Option<i64>,
    pub envelope: String,
    pub recipients: String,
    pub subject: String,
    pub raw: Vec<u8>,
    pub draft_id: Option<i64>,
    pub next_attempt_at: i64,
    pub wait_kind: &'static str,
    pub created_at: String,
}

/// A mark a try leaves on its outbox row (`outbox_set`).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Mark {
    /// The message's last bytes are going, or the mark goes with an answer.
    WireBegun(bool),
    SentAppended,
    AnsweredSet,
    DraftDeleted,
    FollowupRecorded,
    /// What a queued send waits for: `outbox_item.GRACE`, `SCHEDULED` or `BACKOFF`.
    WaitKind(&'static str),
}

/// A draft MailMind tracks (`drafts`), every column.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct DraftRow {
    pub id: i64,
    pub account: String,
    pub folder: Option<String>,
    pub uid: Option<i64>,
    pub message_id: Option<String>,
    pub uidvalidity: Option<i64>,
    pub mode: Option<String>,
    pub subject: Option<String>,
    pub created_at: Option<String>,
    pub updated_at: Option<String>,
    pub own_text: Option<String>,
    pub tail_text: Option<String>,
    pub tail_html: Option<String>,
    pub bcc: Option<String>,
    pub raw_sha: Option<String>,
    pub idempotency_key: Option<String>,
    pub version: i64,
    pub saved_by: Option<String>,
    pub pushed_at: Option<i64>,
}

/// What a draft still asks of its server (`draft_journal`): a save's bytes and what the composer held at it, or that
/// the copy is to go.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Waiting {
    pub draft_id: i64,
    pub version: i64,
    /// Where the copy goes: From may have changed since the last.
    pub account: String,
    /// All that's left is to remove the copy.
    pub discard: bool,
    pub message_id: Option<String>,
    pub raw: Option<Vec<u8>>,
    pub raw_sha: Option<String>,
    pub own_text: Option<String>,
    pub tail_text: Option<String>,
    pub tail_html: Option<String>,
    pub bcc: Option<String>,
    /// The Message-ID of a copy a try may have left.
    pub tried: Option<String>,
    pub saved_at: i64,
    pub due_at: i64,
    pub attempts: i64,
}

/// What a save records of what the composer held (`draft_journal.Record`): it describes these very bytes and no
/// others.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Record {
    pub raw_sha: String,
    pub own_text: String,
    pub tail_text: Option<String>,
    pub tail_html: Option<String>,
    pub bcc: Option<String>,
}

/// The quote a save holds, as text and as HTML.
pub type Quote = (Option<String>, Option<String>);

/// A save as the journal holds it (`journal_put`).
#[derive(Clone, Copy, Debug)]
pub struct Save<'a> {
    pub draft_id: i64,
    pub version: i64,
    pub account: &'a str,
    pub message_id: &'a str,
    pub raw: &'a [u8],
    pub record: &'a Record,
    pub saved_at: i64,
    pub due_at: i64,
    pub attempts: i64,
}

/// Compose's tables, `outbox`, `drafts` and `draft_journal`, and the Mailbox's rows Compose reads, inside one block
/// of work (`compose.adapters.sqlite`, and `bootstrap.compose._Messages`).
pub trait Book {
    // The outbox.
    /// (its id, created): created is false when the idempotency key was seen already, a double submit.
    fn outbox_insert(&self, row: &NewSend) -> Result<(Option<i64>, bool), Fault>;
    fn outbox_set(&self, id: i64, marks: &[Mark]) -> Result<(), Fault>;
    /// Bring a queued send forward to `now`: false when it isn't queued.
    fn outbox_send_now(&self, id: i64, now: i64) -> Result<bool, Fault>;
    /// Whether a send of this draft is queued or going out now.
    fn outbox_holds_draft(&self, draft_id: i64) -> Result<bool, Fault>;
    fn outbox_get(&self, id: i64) -> Result<Option<OutboxRow>, Fault>;
    /// The idempotency keys under `prefix`, newest first (an RSVP's `keys_sent`).
    fn outbox_keys(&self, prefix: &str) -> Result<Vec<String>, Fault>;

    // The queue over the outbox (`DurableQueue`), each row in its account's lane.
    /// The queued rows due at `now`, oldest first, each with its account.
    fn outbox_due(&self, now: i64) -> Result<Vec<(i64, String)>, Fault>;
    /// A queued row flipped to `sending`, as it is now; None to every caller but the one that wins it.
    fn outbox_claim(&self, id: i64) -> Result<Option<OutboxRow>, Fault>;
    /// An attempt failed: back to the queue at `next_at`, or, `terminal`, to error, with its attempts and why.
    fn outbox_retry_later(
        &self,
        id: i64,
        attempts: i64,
        terminal: bool,
        next_at: i64,
        error: &str,
    ) -> Result<(), Fault>;
    /// An attempt failed in a way no retry can fix: to error at once.
    fn outbox_give_up(&self, id: i64, attempts: i64, error: &str) -> Result<(), Fault>;
    /// The wire send happened: it never happens again.
    fn outbox_delivered(&self, id: i64) -> Result<(), Fault>;
    fn outbox_finish(&self, id: i64, at: &str) -> Result<(), Fault>;
    /// Rows a crash left sending go back to the queue, but one that may have gone out to error, saying `unsure`.
    fn outbox_reset_stuck(&self, unsure: &str) -> Result<(), Fault>;
    /// A row in error back to the queue, its attempts counted afresh; false when it isn't in error.
    fn outbox_requeue(&self, id: i64) -> Result<bool, Fault>;
    /// Delete a queued row that hasn't gone out; false once it has, or a worker has it.
    fn outbox_cancel_queued(&self, id: i64) -> Result<bool, Fault>;
    /// The soonest a queued row comes due after `after`.
    fn outbox_next_due(&self, after: i64) -> Result<Option<i64>, Fault>;

    // The drafts MailMind tracks.
    fn draft_get(&self, id: i64) -> Result<Option<DraftRow>, Fault>;
    /// The draft a composer's first save made, by the composer's key: the newest if there are two.
    fn draft_by_key(&self, key: &str) -> Result<Option<DraftRow>, Fault>;
    fn draft_set_key(&self, id: i64, key: &str) -> Result<(), Fault>;
    fn draft_by_msgid(&self, account: &str, message_id: Option<&str>) -> Result<Option<DraftRow>, Fault>;
    /// Track a new draft, and say its id.
    fn draft_insert(&self, draft: &Tracked) -> Result<i64, Fault>;
    /// What the composer held at the save its copy is now (`draft_set_resume`).
    fn draft_set_resume(&self, id: i64, record: &Record) -> Result<(), Fault>;
    /// Whether the server flags this copy \Draft, as last synced.
    fn flagged_draft(&self, account: &str, folder: &str, uid: i64) -> Result<bool, Fault>;
    /// Track the copy that was opened rather than the one last saved, unless a save moved the row on since `seen`;
    /// `forget` drops what was recorded of the composer's text. Whether it was re-pointed.
    fn draft_repoint(
        &self,
        seen: &DraftRow,
        folder: &str,
        uid: i64,
        uidvalidity: Option<i64>,
        forget: bool,
    ) -> Result<bool, Fault>;
    fn draft_delete(&self, id: i64) -> Result<(), Fault>;
    fn draft_note_save(
        &self,
        id: i64,
        mode: Option<&str>,
        subject: Option<&str>,
        version: i64,
        saved_by: Option<&str>,
        updated_at: &str,
    ) -> Result<(), Fault>;
    fn draft_note_discard(&self, id: i64, version: i64, updated_at: &str) -> Result<(), Fault>;
    #[allow(clippy::too_many_arguments)]
    fn draft_settle_push(
        &self,
        id: i64,
        account: &str,
        folder: &str,
        uid: Option<i64>,
        message_id: &str,
        uidvalidity: Option<i64>,
        pushed_at: i64,
    ) -> Result<(), Fault>;
    /// The quote the draft's newest save holds, (text, html): the journal's, or its copy's.
    fn draft_quote(&self, id: i64) -> Result<Option<Quote>, Fault>;

    // The journal: what a draft still asks of its server.
    fn journal_get(&self, draft_id: i64) -> Result<Option<Waiting>, Fault>;
    /// This save replaces the one before it; `tried` stays.
    fn journal_put(&self, save: &Save<'_>) -> Result<(), Fault>;
    /// All that's left to ask is that the copy goes.
    fn journal_discard(&self, draft_id: i64, version: i64, account: &str, now: i64) -> Result<(), Fault>;
    /// A try may reach the server from here on; false when the row or this version has gone meanwhile.
    fn journal_tried(&self, draft_id: i64, version: i64, message_id: &str) -> Result<bool, Fault>;
    /// The push of `version` landed.
    fn journal_settled(&self, draft_id: i64, version: i64) -> Result<(), Fault>;
    fn journal_failed(&self, draft_id: i64, attempts: i64, due_at: i64) -> Result<(), Fault>;
    /// The drafts whose saves are due at `now`, leaving out one whose send goes before `soon`, soonest first.
    fn journal_due(&self, now: i64, soon: i64) -> Result<Vec<i64>, Fault>;
    fn journal_next_due(&self, after: i64, soon: i64) -> Result<Option<i64>, Fault>;
    /// A save goes on as a draft of its own.
    fn journal_move(&self, draft_id: i64, to: i64) -> Result<(), Fault>;

    // The Mailbox's rows, as Compose reads them.
    /// A message's row, every column.
    fn message(&self, pk: i64) -> Result<Option<Row>, Fault>;
    /// The newest row of a message by its Message-ID: none for none.
    fn message_by_msgid(&self, account: &str, message_id: Option<&str>) -> Result<Option<Row>, Fault>;
    /// The newest row of a message by its key.
    fn message_by_key(&self, account: &str, key: i64) -> Result<Option<Row>, Fault>;
    /// A copy of the message a key names (`pk_of_message`).
    fn message_pk(&self, key: i64) -> Result<Option<i64>, Fault>;
    /// Its raw bytes, where they are stored.
    fn body(&self, pk: i64) -> Result<Option<Vec<u8>>, Fault>;
    /// A folder's UIDVALIDITY as its last sync read it.
    fn uidvalidity(&self, account: &str, folder: &str) -> Result<Option<i64>, Fault>;
    /// A message's row goes, as of `now`.
    fn remove_message(&self, pk: i64, now: i64) -> Result<(), Fault>;
    /// The rows of copies gone from a folder go with them (`delete_by_uids`).
    fn remove_copies(&self, account: &str, folder: &str, uids: &[i64], now: i64) -> Result<(), Fault>;
}

/// The store, a block of work at a time.
pub trait ComposeStore: Send + Sync + fmt::Debug {
    /// `work` in one write transaction: committed when it answers, rolled back when it fails.
    fn write(&self, work: &mut dyn FnMut(&dyn Book) -> Result<(), Fault>) -> Result<(), Fault>;
    /// `work` on a connection of its own, for reading.
    fn read(&self, work: &mut dyn FnMut(&dyn Book) -> Result<(), Fault>) -> Result<(), Fault>;
}

fn once<T>(
    run: impl FnOnce(&mut dyn FnMut(&dyn Book) -> Result<(), Fault>) -> Result<(), Fault>,
    work: impl FnOnce(&dyn Book) -> Result<T, Fault>,
) -> Result<T, Fault> {
    let mut work = Some(work);
    let mut answer = None;
    run(&mut |book| {
        if let Some(work) = work.take() {
            answer = Some(work(book)?);
        }
        Ok(())
    })?;
    answer.ok_or(Fault::new("NoAnswer"))
}

/// `work` in one write transaction, and what it answered.
pub fn write<T>(store: &dyn ComposeStore, work: impl FnOnce(&dyn Book) -> Result<T, Fault>) -> Result<T, Fault> {
    once(|w| store.write(w), work)
}

/// `work` on a connection for reading, and what it answered.
pub fn read<T>(store: &dyn ComposeStore, work: impl FnOnce(&dyn Book) -> Result<T, Fault>) -> Result<T, Fault> {
    once(|w| store.read(w), work)
}

/// Why a send didn't go (`SmtpTransport.permanent`, `sanitize_error`, `Unanswered`), in fixed words that hold nothing
/// a server said.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct NotSent {
    /// The fixed, secret-free words stored and shown.
    pub said: &'static str,
    /// The server refused for good: sent again it gets the same answer.
    pub permanent: bool,
    /// The whole message went out and no answer came, so it may have been delivered.
    pub unanswered: bool,
}

/// The wire: an account's SMTP server, reached only as its configuration says.
pub trait Transport: Send + Sync + fmt::Debug {
    /// Deliver `raw` (Bcc-less) to every address of `envelope`, from `account`. `before_dot` is called just before
    /// the message's last bytes go.
    fn send(&self, account: &str, raw: &[u8], envelope: &[String], before_dot: &mut dyn FnMut())
    -> Result<(), NotSent>;
}

/// Why a mail server didn't do what a copy asked, by its class alone.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct CopyFailed {
    pub class: &'static str,
    /// The connection failed or was lost, or this process only reads: asking again later may help.
    pub unreachable: bool,
}

impl From<Fault> for CopyFailed {
    fn from(fault: Fault) -> CopyFailed {
        CopyFailed { class: fault.class, unreachable: false }
    }
}

/// The copy a push asks its own APPEND to replace (`draft.InPlace`).
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct InPlace {
    pub prev_uid: Option<i64>,
    pub prev_uidvalidity: Option<i64>,
    pub prev_msgid: Option<String>,
    pub prev_folder: Option<String>,
}

/// What a draft's APPEND came to: the folder, the new copy's UID and the folder's UIDVALIDITY where the server said,
/// and the UID of the copy it replaced, where that went.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Appended {
    pub folder: String,
    pub uid: Option<i64>,
    pub uidvalidity: Option<i64>,
    pub removed: Option<i64>,
}

/// The copies Compose keeps on a mail server, in the Mailbox (`mailbox.application.copies`). Each opens a session of
/// its own; all but `probe` run with the account's gate held.
pub trait Copies: Send + Sync + fmt::Debug {
    /// APPEND the sent bytes to the account's Sent, \Seen; given `message_id`, a copy filed under it already is kept
    /// and nothing is appended. The folder.
    fn append_to_sent(&self, account: &str, raw: &[u8], message_id: Option<&str>) -> Result<String, CopyFailed>;
    /// Whether a message under this Message-ID is in the account's Sent.
    fn in_sent(&self, account: &str, message_id: &str) -> Result<bool, CopyFailed>;
    /// APPEND a draft to the account's Drafts, \Draft, then remove the copy it replaces.
    fn append_to_drafts(
        &self,
        account: &str,
        raw: &[u8],
        prev: &InPlace,
        message_id: Option<&str>,
        tried: Option<&str>,
    ) -> Result<Appended, CopyFailed>;
    /// Remove one draft by its UID, a single-UID expunge: whether it went.
    fn delete_draft_uid(
        &self,
        account: &str,
        folder: Option<&str>,
        uid: Option<i64>,
        uidvalidity: Option<i64>,
    ) -> Result<bool, CopyFailed>;
    /// Remove the copy a try may have left in Drafts under a Message-ID only MailMind minted: whether one went.
    fn delete_left_copy(&self, account: &str, message_id: &str) -> Result<bool, CopyFailed>;
    /// Whether this UID is there now, as (the folder's UIDVALIDITY, whether it is), or None when that can't be told.
    /// Read-only, with a short time limit, and no gate.
    fn probe(&self, account: &str, folder: &str, uid: i64, timeout: Duration) -> Option<(Option<i64>, bool)>;
    /// A stored message's raw bytes, read from its server where they aren't stored (`Ingest.fetch_body`): None
    /// when its row has gone, empty when the server has nothing.
    fn fetch_body(&self, pk: i64) -> Result<Option<Vec<u8>>, CopyFailed>;
    /// A folder synced whole, as only a full scan brings back older UIDs.
    fn sync_whole(&self, account: &str, folder: &str) -> Result<(), CopyFailed>;
}

/// An account a message is sent from, as the configuration has it.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct SendingAccount {
    pub email: String,
    /// The name From shows: its alias, else its address.
    pub name: String,
    /// The server files sent mail itself (Gmail): no copy of ours goes in Sent.
    pub saves_sent: bool,
}

/// The accounts a message may be sent from.
pub trait Accounts: Send + Sync + fmt::Debug {
    /// The account, or None when the configuration has none by this address.
    fn sending(&self, email: &str) -> Result<Option<SendingAccount>, Fault>;
    /// How long a person has asked a send to wait, or None when they haven't said, or it can't be read.
    fn undo_send_seconds(&self) -> Option<i64>;
}

/// What a delivered send sets off elsewhere: \Answered on the original, and a follow-up.
pub trait Effects: Send + Sync + fmt::Debug {
    /// \Answered on the message a send replied to, wherever it is by now (`server_ops.mark_answered`): whether it
    /// was found and flagged.
    fn mark_answered(&self, pk: Option<i64>, account: &str, message_id: Option<&str>) -> Result<bool, CopyFailed>;
    /// A sent-no-reply follow-up on the thread, unless it has one (`followup_record_sent`).
    fn followup_sent(&self, account: &str, thread_id: &str, now: i64, at: &str) -> Result<(), Fault>;
}

/// What the Mailbox keeps about folders, and what the page is told.
pub trait Screen: Send + Sync + fmt::Debug {
    /// Whether a folder's name says Drafts on its own (`roles.names_drafts`).
    fn names_drafts(&self, folder: &str) -> bool;
    /// Whether the folder is the account's Drafts, as the last LIST said.
    fn plays_drafts(&self, account: &str, folder: &str) -> bool;
    /// MailMind put mail in the folder: its next open catches it up at once.
    fn touched(&self, account: &str, folder: &str);
    /// A copy left the folder and its row went with it: a list of it on screen loads again.
    fn list_changed(&self, account: &str, folder: &str);
    /// The outbox tray changed.
    fn outbox_changed(&self);
    /// The counts of mail stored changed.
    fn stats_changed(&self);
}

/// Which job goes first when several wait for one gate.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Priority {
    /// A person waits on it.
    Interactive,
    /// Set going by a person, done after: a send's Sent copy, a draft's push.
    AfterUser,
}

/// How long a job waits for its gate.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum GateWait {
    Not,
    For(Duration),
    Ever,
}

/// A gate didn't come free in time, or a job would have waited on one from inside another.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Busy;

/// What passes for every account at once: no gate is held, and it holds them all.
pub const EVERY_ACCOUNT: &str = "*";

/// The gate each account's IMAP work passes (`platform.workers.gates`).
pub trait Gate: Send + Sync + fmt::Debug {
    /// Run `job` with `account`'s gate held.
    fn run(&self, account: &str, priority: Priority, wait: GateWait, job: &mut dyn FnMut()) -> Result<(), Busy>;
}

/// Run `job` on `account`'s gate, and what it answered.
pub fn on_gate<T>(
    gate: &dyn Gate,
    account: &str,
    priority: Priority,
    wait: GateWait,
    job: impl FnOnce() -> T,
) -> Result<T, Busy> {
    let mut job = Some(job);
    let mut answer = None;
    gate.run(account, priority, wait, &mut || {
        if let Some(job) = job.take() {
            answer = Some(job());
        }
    })?;
    answer.ok_or(Busy)
}

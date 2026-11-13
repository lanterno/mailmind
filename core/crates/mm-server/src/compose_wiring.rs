//! Compose bound to its adapters (`mailmind.bootstrap.compose`): the sends and the drafts, the outbox that delivers
//! them, and what they ask of the world. SMTP is mm-smtp's, over what the configuration says of each account; the
//! copies in Sent and Drafts are the Mailbox's sessions; \Answered on an original is a flag on its server, mirrored
//! in its row; a follow-up is Attention's; and what the page hears goes out on the app's events.

use std::sync::Arc;
use std::time::Duration;

use mm_accounts::directory::Pin;
use mm_accounts::ports::ConfigFile;
use mm_accounts::{Directory, account, preferences, tls_pin};
use mm_attention::followups::{FollowUp, PENDING, SENT};
use mm_attention::ports::{AttentionBook, FollowUpChange};
use mm_compose::composing::Composing;
use mm_compose::delivery::{Delivery, DeliveryParts};
use mm_compose::drafting::{Drafting, DraftingParts};
use mm_compose::sending::{Mint, Outgoing, Sending};
use mm_compose::sending_ports::{
    self, Accounts, Appended, Busy, Copies, CopyFailed, Effects, Gate, GateWait, InPlace, NotSent, Priority, Screen,
    SendingAccount, Transport,
};
use mm_compose::spec::Draws;
use mm_kernel::errors::Fault;
use mm_kernel::events::{Events, Topic};
use mm_kernel::seams::{Clock, Outside};
use mm_mailbox::command_ports;
use mm_mailbox::folders::Folders;
use mm_mailbox::gates::{Gates, Wait};
use mm_mailbox::ingest::Ingest;
use mm_mailbox::session::{MailServer, MailServers, Search, ServerError, find_role, found_by_message_id};
use mm_store::Database;
use mm_store::attention::AttentionStore;
use serde_json::{Value, json};

/// What a session waits for each answer (`pool.KEPT_TIMEOUT`), and an SMTP connection for each reply.
const TIMEOUT_SECONDS: u64 = 60;
const SMTP_TIMEOUT: Duration = Duration::from_secs(60);
/// The words a send that failed outside the SMTP conversation is stored with (`sanitize_error`'s last).
const SEND_FAILED: NotSent = NotSent { said: "send failed", permanent: false, unanswered: false };

fn failed(e: ServerError) -> CopyFailed {
    CopyFailed { class: e.class(), unreachable: e.is_unreachable() }
}

/// A session that logs out as it goes, whatever happened on it.
struct Open(Box<dyn MailServer>);

impl Drop for Open {
    fn drop(&mut self) {
        self.0.logout();
    }
}

/// The wire: each account's SMTP server, as only its configuration names it (`SmtpTransport`). A loopback server's
/// certificate is checked against its pin before the password is read or sent.
#[derive(Debug)]
pub(crate) struct SmtpTransport {
    pub(crate) config: Arc<dyn ConfigFile>,
    pub(crate) directory: Arc<Directory>,
    pub(crate) outside: Arc<dyn Outside>,
    pub(crate) read_only: bool,
}

/// A port as the configuration gives one: a number, or digits as text, as `int()` reads them.
fn port_of(value: &Value) -> Option<u16> {
    match value {
        Value::Number(n) => n.as_u64().and_then(|p| u16::try_from(p).ok()),
        Value::String(s) => mm_kernel::text::strip(s).parse().ok(),
        _ => None,
    }
}

impl Transport for SmtpTransport {
    fn send(&self, email: &str, raw: &[u8], envelope: &[String], before_dot: &mut dyn FnMut()) -> Result<(), NotSent> {
        // A read-only process sends nothing.
        if self.read_only {
            return Err(SEND_FAILED);
        }
        if envelope.is_empty() {
            let error = mm_smtp::Error::NoRecipients;
            return Err(NotSent { said: error.said(), permanent: false, unanswered: false });
        }
        let Ok(Some(account)) = self.directory.get(email) else { return Err(SEND_FAILED) };
        let mut raw_config = self.config.read().map_err(|_| SEND_FAILED)?;
        let Some(entry) = account::find(&mut raw_config, email) else { return Err(SEND_FAILED) };
        let endpoint = account::smtp_endpoint(entry);
        let host = endpoint.host.as_str().map(str::to_owned).ok_or(SEND_FAILED)?;
        let port = port_of(&endpoint.port).ok_or(SEND_FAILED)?;
        let loopback = tls_pin::is_loopback(&host);
        let starttls = account::truthy(&endpoint.starttls);
        let said = |e: mm_smtp::Error| NotSent { said: e.said(), permanent: e.permanent(), unanswered: e.unanswered() };
        let reached = mm_smtp::Endpoint { host: &host, port, starttls, loopback };
        let server = mm_smtp::Server::connect(&*self.outside, reached, SMTP_TIMEOUT).map_err(said)?;
        if loopback {
            let seen = tls_pin::fingerprint(server.certificate());
            let pin = self.directory.tls_pin(&account.email, &tls_pin::endpoint(&host, port), &seen);
            if !matches!(pin, Ok(Pin::Kept | Pin::NoAccount)) {
                return Err(SEND_FAILED);
            }
        }
        let password = self.directory.password(&account).map_err(|_| SEND_FAILED)?;
        let mut session = server.login(&account.email, &password).map_err(said)?;
        let sent = session.send(&account.email, envelope, raw, before_dot).map_err(said);
        session.quit();
        sent
    }
}

/// The copies Compose keeps on a server, in Sent and in Drafts (`mailbox.application.copies`), each on a session of
/// its own over the Mailbox's.
#[derive(Debug)]
pub(crate) struct ImapCopies {
    pub(crate) servers: Arc<dyn MailServers>,
    pub(crate) database: Arc<Database>,
    pub(crate) ingest: Arc<Ingest>,
    pub(crate) clock: Arc<dyn Clock>,
}

const MONTHS: [&str; 12] = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

impl ImapCopies {
    fn open(&self, account: &str, timeout_seconds: u64) -> Result<Open, CopyFailed> {
        Ok(Open(self.servers.open(account, timeout_seconds).map_err(failed)?))
    }

    /// Now, as an APPEND dates a copy (`Time2Internaldate`).
    fn now(&self) -> String {
        let seconds = self.clock.now().whole_seconds();
        let (year, month, day) = mm_kernel::when::civil(seconds.div_euclid(86_400));
        let within = seconds.rem_euclid(86_400);
        let name = MONTHS.get(usize::try_from(month).unwrap_or(1).saturating_sub(1)).copied().unwrap_or("Jan");
        format!("{day:02}-{name}-{year:04} {:02}:{:02}:{:02} +0000", within / 3600, within % 3600 / 60, within % 60)
    }

    /// The account's folder playing `role`, else `fallback`.
    fn role(s: &mut Open, role: &str, fallback: &str) -> Result<String, CopyFailed> {
        Ok(find_role(&mut *s.0, &[role]).map_err(failed)?.unwrap_or_else(|| fallback.to_owned()))
    }

    /// The UID of a copy of ours under this Message-ID in the selected folder (`_left_by`): a refused search finds
    /// none, a lost connection fails.
    fn left_by(s: &mut Open, message_id: Option<&str>) -> Result<Option<u32>, CopyFailed> {
        let Some(message_id) = message_id.filter(|m| !m.is_empty()) else { return Ok(None) };
        match s.0.search(Search::UndeletedMessageId(message_id)) {
            Ok(uids) => Ok(uids.first().copied()),
            Err(ServerError::Refused(_)) => Ok(None),
            Err(e) => Err(failed(e)),
        }
    }

    /// \Deleted and a UID EXPUNGE of this UID alone, never a bulk one.
    fn remove(s: &mut Open, uid: u32) -> Result<(), ServerError> {
        s.0.store_flags(&[uid], true, &["\\Deleted"])?;
        s.0.expunge(&[uid])
    }
}

fn uid32(uid: i64) -> Option<u32> {
    u32::try_from(uid).ok().filter(|u| *u != 0)
}

impl Copies for ImapCopies {
    fn append_to_sent(&self, account: &str, raw: &[u8], message_id: Option<&str>) -> Result<String, CopyFailed> {
        let mut s = self.open(account, TIMEOUT_SECONDS)?;
        let sent = ImapCopies::role(&mut s, "sent", "Sent")?;
        if let Some(message_id) = message_id.filter(|m| !m.is_empty()) {
            s.0.select(&sent, true).map_err(failed)?;
            if !s.0.search(Search::UndeletedMessageId(message_id)).map_err(failed)?.is_empty() {
                return Ok(sent);
            }
        }
        s.0.append(&sent, raw, &["\\Seen"], Some(&self.now())).map_err(failed)?;
        Ok(sent)
    }

    fn in_sent(&self, account: &str, message_id: &str) -> Result<bool, CopyFailed> {
        let mut s = self.open(account, TIMEOUT_SECONDS)?;
        let sent = ImapCopies::role(&mut s, "sent", "Sent")?;
        s.0.select(&sent, true).map_err(failed)?;
        Ok(!s.0.search(Search::UndeletedMessageId(message_id)).map_err(failed)?.is_empty())
    }

    fn append_to_drafts(
        &self,
        account: &str,
        raw: &[u8],
        prev: &InPlace,
        message_id: Option<&str>,
        tried: Option<&str>,
    ) -> Result<Appended, CopyFailed> {
        let mut s = self.open(account, TIMEOUT_SECONDS)?;
        let drafts = ImapCopies::role(&mut s, "drafts", "Drafts")?;
        let elsewhere = prev.prev_folder.as_deref().is_some_and(|f| f != drafts);
        let (prev_uid, prev_msgid) = if elsewhere { (None, None) } else { (prev.prev_uid, prev.prev_msgid.as_deref()) };
        // Refused, no copy is deleted, but the APPEND still goes.
        let uidvalidity = match s.0.select(&drafts, false) {
            Ok(selected) => selected.uidvalidity.map(i64::from),
            Err(ServerError::Refused(_)) => None,
            Err(e) => return Err(failed(e)),
        };
        // The copy it replaces, where it can be told for sure: asked before the new copy can answer too.
        let replaced = match prev_uid.and_then(uid32) {
            Some(uid) if prev.prev_uidvalidity.is_none() || prev.prev_uidvalidity == uidvalidity => Some(uid),
            _ => match prev_msgid.filter(|m| !m.is_empty()) {
                Some(m) => s.0.search(Search::MessageId(m)).ok().and_then(|uids| uids.first().copied()),
                None => None,
            },
        };
        let left = ImapCopies::left_by(&mut s, tried)?;
        let landed = left.filter(|_| tried == message_id);
        let uid = match landed {
            Some(uid) => Some(uid),
            None => s.0.append(&drafts, raw, &["\\Draft"], Some(&self.now())).map_err(failed)?,
        };
        if let Some(left) = left
            && landed.is_none()
        {
            let _ = ImapCopies::remove(&mut s, left);
        }
        let mut removed = None;
        if let Some(old) = replaced
            && Some(old) != uid
            && ImapCopies::remove(&mut s, old).is_ok()
        {
            removed = Some(i64::from(old));
        }
        Ok(Appended { folder: drafts, uid: uid.map(i64::from), uidvalidity, removed })
    }

    fn delete_draft_uid(
        &self,
        account: &str,
        folder: Option<&str>,
        uid: Option<i64>,
        uidvalidity: Option<i64>,
    ) -> Result<bool, CopyFailed> {
        let Some(uid) = uid.and_then(uid32) else { return Ok(false) };
        let mut s = self.open(account, TIMEOUT_SECONDS)?;
        let current = match s.0.select(folder.filter(|f| !f.is_empty()).unwrap_or("Drafts"), false) {
            Ok(selected) => selected.uidvalidity.map(i64::from),
            Err(ServerError::Refused(_)) => return Ok(false),
            Err(e) => return Err(failed(e)),
        };
        // UIDs rolled, or unsaid: don't risk the wrong one.
        if uidvalidity.is_some() && uidvalidity != current {
            return Ok(false);
        }
        match ImapCopies::remove(&mut s, uid) {
            Ok(()) => Ok(true),
            Err(ServerError::Refused(_)) => Ok(false),
            Err(e) => Err(failed(e)),
        }
    }

    fn delete_left_copy(&self, account: &str, message_id: &str) -> Result<bool, CopyFailed> {
        let mut s = self.open(account, TIMEOUT_SECONDS)?;
        let drafts = ImapCopies::role(&mut s, "drafts", "Drafts")?;
        s.0.select(&drafts, false).map_err(failed)?;
        let Some(left) = ImapCopies::left_by(&mut s, Some(message_id))? else { return Ok(false) };
        ImapCopies::remove(&mut s, left).map_err(failed)?;
        Ok(true)
    }

    fn probe(&self, account: &str, folder: &str, uid: i64, timeout: Duration) -> Option<(Option<i64>, bool)> {
        let wanted = u32::try_from(uid).ok()?;
        let mut s = self.open(account, timeout.as_secs().max(1)).ok()?;
        let current = s.0.select(folder, true).ok()?.uidvalidity.map(i64::from);
        let found = s.0.search(Search::Uid(wanted)).ok()?;
        Some((current, found.contains(&wanted)))
    }

    fn fetch_body(&self, pk: i64) -> Result<Option<Vec<u8>>, CopyFailed> {
        let (row, stored) = sending_ports::read(&*self.database, |book| Ok((book.message(pk)?, book.body(pk)?)))?;
        let Some(row) = row else { return Ok(None) };
        if let Some(raw) = stored.filter(|r| !r.is_empty()) {
            return Ok(Some(raw));
        }
        let field = |name: &str| row.get(name).and_then(Value::as_str).unwrap_or_default().to_owned();
        let Some(uid) = row.get("uid").and_then(Value::as_i64).and_then(uid32) else { return Ok(Some(Vec::new())) };
        let mut s = self.open(&field("account"), TIMEOUT_SECONDS)?;
        s.0.select(&field("folder"), true).map_err(failed)?;
        Ok(Some(s.0.body(uid).map_err(failed)?.unwrap_or_default()))
    }

    fn sync_whole(&self, account: &str, folder: &str) -> Result<(), CopyFailed> {
        self.ingest
            .sync_folder(account, folder, true, None)
            .map(drop)
            .map_err(|e| CopyFailed { class: e.class(), unreachable: false })
    }
}

/// The accounts a message may be sent from, and the preference Undo Send counts (`bootstrap.compose._Accounts`).
#[derive(Debug)]
pub(crate) struct SendingAccounts {
    pub(crate) directory: Arc<Directory>,
    pub(crate) config: Arc<dyn ConfigFile>,
}

impl Accounts for SendingAccounts {
    fn sending(&self, email: &str) -> Result<Option<SendingAccount>, Fault> {
        Ok(self.directory.get(email)?.map(|a| SendingAccount {
            name: a.alias.clone().unwrap_or_else(|| a.email.clone()),
            saves_sent: a.kind == "gmail",
            email: a.email,
        }))
    }

    /// A file that can't be read leaves a send at the default, as it does the AI's daily limit: mail still goes.
    fn undo_send_seconds(&self) -> Option<i64> {
        let raw = self.config.read().ok()?;
        preferences::said(&raw, "undo_send_seconds").and_then(Value::as_i64)
    }
}

/// What a delivered send sets off elsewhere (`bootstrap.compose._Effects`): \Answered on its original, on its server
/// and then in its row (`server_ops.mark_answered`), and a follow-up on its thread (Attention's).
#[derive(Debug)]
pub(crate) struct SendEffects {
    pub(crate) servers: Arc<dyn MailServers>,
    pub(crate) database: Arc<Database>,
    pub(crate) attention: Arc<AttentionStore>,
    pub(crate) read_only: bool,
}

const READ_ONLY: CopyFailed = CopyFailed { class: "ReadOnlyProfile", unreachable: false };
/// Where an original no row is left of is looked for, in order.
const ANSWERED_IN: [&str; 5] = ["inbox", "archive", "all", "trash", "junk"];

impl SendEffects {
    /// \Answered on a stored message (`set_flags`): on its server, then in its row. False where the server refused.
    fn answer_stored(&self, pk: i64, row: &mm_compose::ports::Row) -> Result<bool, CopyFailed> {
        let field = |name: &str| row.get(name).and_then(Value::as_str).unwrap_or_default().to_owned();
        if self.read_only {
            return Err(READ_ONLY);
        }
        let uid = row.get("uid").and_then(Value::as_i64).and_then(uid32);
        let mut s = Open(self.servers.open(&field("account"), TIMEOUT_SECONDS).map_err(failed)?);
        let flagged = s.0.select(&field("folder"), false).and_then(|_| match uid {
            Some(uid) => s.0.store_flags(&[uid], true, &["\\Answered"]),
            None => Ok(()),
        });
        match flagged {
            Ok(()) => {}
            Err(ServerError::Refused(_)) => return Ok(false),
            Err(e) => return Err(failed(e)),
        }
        drop(s);
        let flags = [("answered".to_owned(), true)];
        command_ports::write(&*self.database, |tx| tx.set_local_flags(pk, &flags))?;
        Ok(true)
    }
}

impl Effects for SendEffects {
    fn mark_answered(&self, pk: Option<i64>, account: &str, message_id: Option<&str>) -> Result<bool, CopyFailed> {
        let row = sending_ports::read(&*self.database, |book| match book.message_by_msgid(account, message_id)? {
            Some(row) => Ok(Some(row)),
            None => pk.map_or(Ok(None), |pk| book.message(pk)),
        })?;
        if let Some(row) = row {
            let pk = row.get("pk").and_then(Value::as_i64).unwrap_or_default();
            return self.answer_stored(pk, &row);
        }
        let Some(message_id) = message_id.filter(|m| !m.is_empty() && !account.is_empty()) else { return Ok(false) };
        if self.read_only {
            return Err(READ_ONLY);
        }
        let mut s = Open(self.servers.open(account, TIMEOUT_SECONDS).map_err(failed)?);
        for role in ANSWERED_IN {
            let Some(folder) = find_role(&mut *s.0, &[role]).map_err(failed)? else { continue };
            let done = s.0.select(&folder, false).and_then(|_| match found_by_message_id(&mut *s.0, message_id)? {
                Some(uid) => s.0.store_flags(&[uid], true, &["\\Answered"]).map(|()| true),
                None => Ok(false),
            });
            match done {
                Ok(true) => return Ok(true),
                Ok(false) | Err(ServerError::Refused(_)) => {}
                Err(e) => return Err(failed(e)),
            }
        }
        Ok(false)
    }

    fn followup_sent(&self, account: &str, thread_id: &str, now: i64, at: &str) -> Result<(), Fault> {
        self.attention.follow_up(account, thread_id, &mut |current, _| match current {
            // One to a thread, and a dismissal stays dismissed.
            Some(_) => FollowUpChange::Nothing,
            None => FollowUpChange::Put(FollowUp {
                kind: SENT.to_owned(),
                state: PENDING.to_owned(),
                first_sent_ts: Some(now),
                last_msg_ts: Some(now),
                due_ts: None,
                created_at: Some(at.to_owned()),
                resolved_at: None,
            }),
        })
    }
}

/// What the Mailbox keeps of folders and the page is told (`bootstrap.compose._Screen`).
#[derive(Debug)]
pub(crate) struct ComposeScreen {
    pub(crate) folders: Arc<Folders>,
    pub(crate) events: Arc<dyn Events>,
}

impl Screen for ComposeScreen {
    fn names_drafts(&self, folder: &str) -> bool {
        mm_mailbox::roles::names_drafts(folder)
    }

    fn plays_drafts(&self, account: &str, folder: &str) -> bool {
        self.folders.plays_drafts(account, folder).unwrap_or_else(|_| mm_mailbox::roles::names_drafts(folder))
    }

    /// A folder's next open catches it up at once: the Rust server keeps no catch-up times yet, so it always does.
    fn touched(&self, _account: &str, _folder: &str) {}

    fn list_changed(&self, account: &str, folder: &str) {
        self.events.publish(Topic::Reconcile, json!({"account": account, "folder": folder, "pruned": 1}));
    }

    fn outbox_changed(&self) {
        self.events.publish(Topic::Outbox, json!({}));
    }

    /// The counts are read live on the Rust server: none is kept to drop.
    fn stats_changed(&self) {}
}

/// Each account's IMAP gate, as Compose asks for it.
#[derive(Debug)]
pub(crate) struct ComposeGate(pub(crate) Arc<dyn Gates>);

impl Gate for ComposeGate {
    fn run(&self, account: &str, priority: Priority, wait: GateWait, job: &mut dyn FnMut()) -> Result<(), Busy> {
        let priority = match priority {
            Priority::Interactive => mm_mailbox::gates::Priority::Interactive,
            Priority::AfterUser => mm_mailbox::gates::Priority::AfterUser,
        };
        let wait = match wait {
            GateWait::Not => Wait::Not,
            GateWait::For(at_most) => Wait::For(at_most),
            GateWait::Ever => Wait::Ever,
        };
        self.0.run(account, priority, wait, job).map_err(|_| Busy)
    }
}

/// Numbers from the operating system's random source, for Message-IDs, boundaries and keys.
#[derive(Debug)]
pub(crate) struct RandomDraws;

impl Draws for RandomDraws {
    fn number(&self) -> u64 {
        let mut bytes = [0u8; 8];
        if getrandom::fill(&mut bytes).is_err() {
            // The source failing is as rare as it is fatal elsewhere; a send still needs a unique id.
            let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default();
            return u64::try_from(now.as_nanos() % u128::from(u64::MAX)).unwrap_or_default();
        }
        u64::from_le_bytes(bytes)
    }
}

/// What Compose is assembled over.
#[derive(Debug)]
pub(crate) struct Parts {
    pub(crate) database: Arc<Database>,
    pub(crate) directory: Arc<Directory>,
    pub(crate) config: Arc<dyn ConfigFile>,
    pub(crate) outside: Arc<dyn Outside>,
    pub(crate) servers: Arc<dyn MailServers>,
    pub(crate) gates: Arc<dyn Gates>,
    pub(crate) folders: Arc<Folders>,
    pub(crate) events: Arc<dyn Events>,
    pub(crate) ingest: Arc<Ingest>,
    pub(crate) clock: Arc<dyn Clock>,
    pub(crate) read_only: bool,
}

/// Compose's use cases, bound.
#[derive(Debug)]
pub struct Compose {
    pub composing: Arc<Composing>,
    pub sending: Sending,
    /// The drafts, and `push_round`, the push job's round.
    pub drafting: Drafting,
    /// The outbox: Undo Send, Retry and Send now, and `deliver`, a pass of the worker.
    pub delivery: Arc<Delivery>,
    /// What another context sends through the outbox: an invite's answer, an unsubscribe by mail.
    pub outgoing: Outgoing,
    database: Arc<Database>,
}

impl Compose {
    pub(crate) fn assemble(parts: Parts) -> Compose {
        let accounts: Arc<dyn Accounts> =
            Arc::new(SendingAccounts { directory: parts.directory.clone(), config: parts.config.clone() });
        let copies: Arc<dyn Copies> = Arc::new(ImapCopies {
            servers: parts.servers.clone(),
            database: parts.database.clone(),
            ingest: parts.ingest.clone(),
            clock: parts.clock.clone(),
        });
        let screen: Arc<dyn Screen> = Arc::new(ComposeScreen { folders: parts.folders.clone(), events: parts.events });
        let gate: Arc<dyn Gate> = Arc::new(ComposeGate(parts.gates));
        let writes = !parts.read_only;
        let delivery = Arc::new(Delivery::new(DeliveryParts {
            store: parts.database.clone(),
            transport: Arc::new(SmtpTransport {
                config: parts.config,
                directory: parts.directory,
                outside: parts.outside,
                read_only: parts.read_only,
            }),
            copies: copies.clone(),
            accounts: accounts.clone(),
            effects: Arc::new(SendEffects {
                servers: parts.servers,
                database: parts.database.clone(),
                attention: Arc::new(AttentionStore::new(parts.database.clone())),
                read_only: parts.read_only,
            }),
            screen: screen.clone(),
            gate: gate.clone(),
            clock: parts.clock.clone(),
            writes,
        }));
        let mint = Mint { clock: parts.clock.clone(), draws: Arc::new(RandomDraws), pid: std::process::id() };
        let composing =
            Arc::new(Composing::new(parts.database.clone(), accounts.clone(), copies.clone(), gate.clone()));
        let sending = Sending::new(parts.database.clone(), composing.clone(), delivery.clone(), mint.clone());
        let outgoing = Outgoing::new(parts.database.clone(), delivery.clone(), mint.clone());
        let drafting = Drafting::new(DraftingParts {
            store: parts.database.clone(),
            composing: composing.clone(),
            delivery: delivery.clone(),
            accounts,
            copies,
            screen,
            gate,
            clock: parts.clock,
            mint,
            writes,
        });
        Compose { composing, sending, drafting, delivery, outgoing, database: parts.database }
    }

    /// A copy of a message the request names, by its key alone (`pk_of_message`).
    pub(crate) fn message_pk(&self, key: i64) -> Result<Option<i64>, Fault> {
        sending_ports::read(&*self.database, |book| book.message_pk(key))
    }

    /// The copy a request names by its key, folder and uid (`pk_of_copy`).
    pub(crate) fn copy_pk(&self, key: i64, folder: &str, uid: Option<i64>) -> Result<Option<i64>, Fault> {
        self.database.read(|c| mm_store::copies::copy_pk(c, key, folder, uid))
    }
}

//! A send (`compose.application.sending`): a new message, a reply or a forward, built and queued. And what another
//! context hands over to be sent, an invite's answer or an unsubscribe by mail, through the same outbox (`Outgoing`).

use std::fmt;
use std::sync::Arc;

use mm_kernel::errors::{Failure, Fault, Problem};
use mm_kernel::seams::Clock;
use serde_json::{Value, json};

use crate::attachments;
use crate::builder::build_message;
use crate::composing::{Composing, Composition};
use crate::delivery::Delivery;
use crate::draft;
use crate::outbox_item::SendMeta;
use crate::sending_ports::{self, ComposeStore};
use crate::spec::{Built, Draws, MessageSpec, Minting};

/// What a send's Message-ID and boundaries are drawn from, and the process id the Message-ID names.
#[derive(Clone)]
pub struct Mint {
    pub clock: Arc<dyn Clock>,
    pub draws: Arc<dyn Draws>,
    pub pid: u32,
}

impl fmt::Debug for Mint {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Mint").field("pid", &self.pid).finish_non_exhaustive()
    }
}

impl Mint {
    /// What building a message takes from the world, now.
    pub fn minting(&self) -> Minting<'_> {
        Minting { now: self.clock.now(), pid: self.pid, draws: &*self.draws }
    }

    /// A key no one can guess (`uuid.uuid4().hex`), for a send whose composer gave none.
    pub fn key(&self) -> String {
        format!("{:016x}{:016x}", self.draws.number(), self.draws.number())
    }
}

pub struct Sending {
    store: Arc<dyn ComposeStore>,
    composing: Arc<Composing>,
    delivery: Arc<Delivery>,
    mint: Mint,
}

impl fmt::Debug for Sending {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Sending").finish_non_exhaustive()
    }
}

impl Sending {
    pub fn new(
        store: Arc<dyn ComposeStore>,
        composing: Arc<Composing>,
        delivery: Arc<Delivery>,
        mint: Mint,
    ) -> Sending {
        Sending { store, composing, delivery, mint }
    }

    /// A compose, reply or forward request judged, built and queued (`Sending.send`).
    pub fn send(&self, asked: &Composition) -> Result<Value, Failure> {
        let send_after =
            attachments::send_after(asked.send_after, self.mint.clock.now().micros()).map_err(Problem::refused)?;
        let (composed, in_reply_to_pk, account) = self.composing.spec(asked)?;
        let built = build_message(&composed.spec, false, self.mint.minting()).map_err(Problem::refused)?;
        let mut draft_id = asked.draft_id.filter(|id| *id != 0);
        let key = draft::composer_key(asked.idempotency_key.as_deref());
        if draft_id.is_none()
            && let Some(key) = key
        {
            // A first save whose answer the composer never heard.
            draft_id = sending_ports::read(&*self.store, |book| book.draft_by_key(&key))?.map(|d| d.id);
        }
        let meta = SendMeta {
            account: account.email.clone(),
            idempotency_key: asked.idempotency_key.clone().filter(|k| !k.is_empty()).unwrap_or_else(|| self.mint.key()),
            in_reply_to_pk,
            orig_message_id: composed.spec.in_reply_to.clone(),
            subject: composed.spec.subject.clone(),
            draft_id,
            send_after,
        };
        let (id, created) = self.delivery.enqueue(&built, &meta)?;
        Ok(json!({
            "ok": true,
            "outbox_id": id,
            "queued": created,
            "scheduled": send_after != 0,
            "recipients": built.envelope.len(),
            "undo_seconds": self.delivery.send_delay(),
        }))
    }
}

/// A message another context sends through the outbox: an invite's answer (RSVP) or an unsubscribe by mail. Build the
/// message with `spec` (From is `account`, the sending account's address, and `account_name` the name it shows), then
/// `send`.
pub struct Outgoing {
    store: Arc<dyn ComposeStore>,
    delivery: Arc<Delivery>,
    mint: Mint,
}

impl fmt::Debug for Outgoing {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Outgoing").finish_non_exhaustive()
    }
}

impl Outgoing {
    pub fn new(store: Arc<dyn ComposeStore>, delivery: Arc<Delivery>, mint: Mint) -> Outgoing {
        Outgoing { store, delivery, mint }
    }

    /// Build `spec` as `build_message` does, its refusal in Python's words.
    pub fn build(&self, spec: &MessageSpec) -> Result<Built, String> {
        build_message(spec, false, self.mint.minting())
    }

    /// Queue a message built: (its outbox id, whether it is new; false for a key seen already). `idempotency_key`
    /// dedupes a double submit; `in_reply_to_pk` names the message it answers, which gets \Answered once it goes.
    pub fn send(
        &self,
        built: &Built,
        idempotency_key: &str,
        subject: &str,
        in_reply_to_pk: Option<i64>,
        account: &str,
    ) -> Result<(Option<i64>, bool), Fault> {
        let meta = SendMeta {
            account: account.to_owned(),
            idempotency_key: idempotency_key.to_owned(),
            in_reply_to_pk,
            orig_message_id: None,
            subject: subject.to_owned(),
            draft_id: None,
            send_after: 0,
        };
        self.delivery.enqueue(built, &meta)
    }

    /// The seconds a send waits before it goes, which Undo Send counts.
    pub fn send_delay(&self) -> i64 {
        self.delivery.send_delay()
    }

    /// The outbox's idempotency keys under `prefix`, newest first: the answers an invite has had queued.
    pub fn keys_sent(&self, prefix: &str) -> Result<Vec<String>, Fault> {
        sending_ports::read(&*self.store, |book| book.outbox_keys(prefix))
    }
}

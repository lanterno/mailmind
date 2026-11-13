//! What a save and a send share (`compose.application.composing`): a request made into the message it asks for, and
//! the composer's Preview.

use std::collections::BTreeSet;
use std::fmt;
use std::sync::Arc;

use mm_kernel::errors::{Failure, Kind, Problem};
use mm_kernel::log::{self, Field};
use serde_json::{Map, Value, json};

use crate::attachments;
use crate::composition::{self, Composed};
use crate::sending_ports::{
    self, Accounts, ComposeStore, Copies, Gate, GateWait, Priority, Quote, SendingAccount, on_gate,
};
use crate::spec::Attachment;

const TAG: &str = "compose";

/// The modes a reopened draft or a send that answers another goes by (`resume.REPLIES`).
pub const REPLIES: [&str; 2] = ["reply", "replyall"];

/// What a send and a save ask (`schemas.compose.Composition`, and `SendBody`'s and `DraftBody`'s own), as the request
/// gave it once its schema let it through.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Composition {
    pub account: Option<String>,
    /// `new`, `reply`, `replyall` or `forward`; None is a new message.
    pub mode: Option<&'static str>,
    pub to: Vec<String>,
    pub cc: Vec<String>,
    pub bcc: Vec<String>,
    pub subject: Option<String>,
    pub text: Option<String>,
    /// Each file as the composer sent it, for `attachments.decode` to judge.
    pub attachments: Option<Vec<Value>>,
    /// A reopened draft's quote and threading.
    pub resume: Option<Map<String, Value>>,
    /// The copy of the original the request's `original_key` names now, where one is left.
    pub in_reply_to_pk: Option<i64>,
    /// Whether a forward takes the original's files along: None where the request left it out, which takes them,
    /// and false where it said null, as `body.get("forward_attachments", True)` reads it.
    pub forward_attachments: Option<bool>,
    pub draft_id: Option<i64>,
    pub idempotency_key: Option<String>,
    pub send_after: Option<i64>,
    pub delete: Option<bool>,
    pub autosave: Option<bool>,
    pub base_version: Option<i64>,
}

impl Composition {
    pub fn mode(&self) -> &'static str {
        self.mode.unwrap_or("new")
    }
}

pub struct Composing {
    store: Arc<dyn ComposeStore>,
    accounts: Arc<dyn Accounts>,
    copies: Arc<dyn Copies>,
    gate: Arc<dyn Gate>,
}

impl fmt::Debug for Composing {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Composing").finish_non_exhaustive()
    }
}

/// The text of a JSON value as a reopened draft's field reads it (`str(x or "")`).
fn text_of(value: Option<&Value>) -> Option<String> {
    match value {
        None | Some(Value::Null) | Some(Value::Bool(false)) => None,
        Some(Value::String(text)) => Some(text.clone()),
        Some(Value::Bool(true)) => Some("True".to_owned()),
        Some(Value::Number(n)) if n.as_f64() == Some(0.0) => None,
        Some(other) => Some(other.to_string()),
    }
}

impl Composing {
    pub fn new(
        store: Arc<dyn ComposeStore>,
        accounts: Arc<dyn Accounts>,
        copies: Arc<dyn Copies>,
        gate: Arc<dyn Gate>,
    ) -> Composing {
        Composing { store, accounts, copies, gate }
    }

    /// What a person typed as it would go out, for the composer's Preview. It reads and writes nothing.
    pub fn preview(&self, text: &str, pictures: &BTreeSet<String>) -> Value {
        let (html, formatted) = composition::preview(text, pictures);
        json!({"ok": true, "html": html, "formatted": formatted})
    }

    /// The sending account a request names: From is only ever a configured account.
    pub fn account(&self, asked: &Composition) -> Result<SendingAccount, Failure> {
        let email = mm_kernel::text::strip(asked.account.as_deref().unwrap_or_default());
        self.accounts.sending(email)?.ok_or_else(|| Problem::refused("unknown sending account").into())
    }

    /// Shared by a send and a save (`Composing.spec`): the account resolved, the files decoded, a reply's or a
    /// forward's body and threading made. (the message to build, the original's pk where it answers one, the account).
    pub fn spec(&self, asked: &Composition) -> Result<(Composed, Option<i64>, SendingAccount), Failure> {
        let mode = asked.mode();
        let account = self.account(asked)?;
        let text = asked.text.as_deref().unwrap_or_default();
        let mut in_reply_to_pk = None;
        let mut forwarded: Vec<Attachment> = Vec::new();
        let mut files = attachments::decode(asked.attachments.as_deref()).map_err(Problem::refused)?;
        let pictures = attachments::pictures_of(&files);
        let made = if let Some(resume) = &asked.resume {
            let tail = composition::tail_of(text_of(resume.get("tail_text")).as_deref());
            let recorded = self.recorded_quote(asked.draft_id, &tail)?;
            let made = composition::as_drafted(
                text,
                &tail,
                recorded.as_ref().map(|(t, h)| (t.as_deref(), h.as_deref())),
                text_of(resume.get("in_reply_to")).as_deref(),
                text_of(resume.get("references")).as_deref(),
                &pictures,
            );
            if REPLIES.contains(&mode)
                && let Some(irt) = made.in_reply_to.as_deref().filter(|m| !m.is_empty())
            {
                in_reply_to_pk = self.reopened_original(asked, &account.email, irt)?;
            }
            made
        } else if REPLIES.contains(&mode) || mode == "forward" {
            let Some(pk) = asked.in_reply_to_pk else {
                return Err(Problem::refused("missing original message").into());
            };
            let Some(row) = sending_ports::read(&*self.store, |book| book.message(pk))? else {
                return Err(Problem::not_found("original message not found").into());
            };
            let field = |name: &str| row.get(name).and_then(Value::as_str).map(str::to_owned);
            let owner = field("account").unwrap_or_default();
            let fetched =
                on_gate(&*self.gate, &owner, Priority::Interactive, GateWait::Ever, || self.copies.fetch_body(pk));
            let raw = match fetched {
                Ok(Ok(raw)) => raw.unwrap_or_default(),
                Ok(Err(failed)) => {
                    log::warning(TAG, "original fetch failed", &[(Field::Id, &pk.to_string())], Some(failed.class));
                    return Err(Problem::new(Kind::Unreachable, "could not load the original message").into());
                }
                Err(_) => {
                    log::warning(TAG, "original fetch failed", &[(Field::Id, &pk.to_string())], Some("Busy"));
                    return Err(Problem::new(Kind::Unreachable, "could not load the original message").into());
                }
            };
            let parsed = (!raw.is_empty()).then(|| mm_content::mime::parse(&raw));
            let original =
                composition::original(field("from_name"), field("from_addr"), field("subject"), parsed.as_ref());
            if mode == "forward" {
                let made = composition::as_forward(text, &original, &pictures);
                if asked.forward_attachments.unwrap_or(true) {
                    forwarded = attachments::take_along(&files, attachments::extract(Some(&raw)));
                    files.extend(forwarded.iter().cloned());
                }
                made
            } else {
                in_reply_to_pk = Some(pk);
                composition::as_reply(
                    text,
                    &original,
                    field("message_id").as_deref(),
                    field("refs").as_deref(),
                    field("in_reply_to").as_deref(),
                    &pictures,
                )
            }
        } else {
            composition::as_typed(text, &pictures)
        };
        let composed = composition::composed(
            &account.email,
            &account.name,
            asked.to.clone(),
            asked.cc.clone(),
            asked.bcc.clone(),
            asked.subject.as_deref().unwrap_or_default(),
            made,
            files,
            forwarded,
        );
        Ok((composed, in_reply_to_pk, account))
    }

    /// The quote the draft's newest save holds, where there is a quote and a draft to ask.
    fn recorded_quote(&self, draft_id: Option<i64>, tail: &str) -> Result<Option<Quote>, Failure> {
        let Some(id) = draft_id.filter(|id| *id != 0) else { return Ok(None) };
        if tail.is_empty() {
            return Ok(None);
        }
        Ok(sending_ports::read(&*self.store, |book| book.draft_quote(id))?)
    }

    /// A reopened reply's original by its Message-ID, a pk may have been renumbered; past that From changed, and only
    /// the request's own pk can name it, if it is the message the draft's In-Reply-To says (`_reopened_original`).
    fn reopened_original(&self, asked: &Composition, account: &str, in_reply_to: &str) -> Result<Option<i64>, Failure> {
        let found = sending_ports::read(&*self.store, |book| {
            if let Some(row) = book.message_by_msgid(account, Some(in_reply_to))? {
                return Ok(row.get("pk").and_then(Value::as_i64));
            }
            let Some(pk) = asked.in_reply_to_pk else { return Ok(None) };
            Ok(book
                .message(pk)?
                .filter(|row| row.get("message_id").and_then(Value::as_str) == Some(in_reply_to))
                .map(|_| pk))
        })?;
        Ok(found)
    }
}

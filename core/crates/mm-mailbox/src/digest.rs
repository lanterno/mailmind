//! What `/api/digest` answers (`mailmind.mailbox.application.digest`): the items the brief is built from, and with
//! them what the page needs of the rest of the app as it opens: the identities to send from, the sync's state and
//! the AI's.

use std::sync::Arc;

use mm_kernel::ago::rel_time;
use mm_kernel::errors::Failure;
use mm_kernel::seams::Clock;
use mm_kernel::when::iso;
use serde_json::{Map, Value, json};

use crate::listing::{attention, col, or, text};
use crate::ports::{AccountBook, AiStatus, DigestReads, Row, SyncStatus};

/// How many items the digest holds at most.
const LIMIT: i64 = 400;

#[derive(Debug)]
pub struct Digest {
    accounts: Arc<dyn AccountBook>,
    stored: Arc<dyn DigestReads>,
    syncing: Arc<dyn SyncStatus>,
    ai: Arc<dyn AiStatus>,
    /// How this process runs: `full` or `read-only`.
    profile: &'static str,
    clock: Arc<dyn Clock>,
}

impl Digest {
    pub fn new(
        accounts: Arc<dyn AccountBook>,
        stored: Arc<dyn DigestReads>,
        syncing: Arc<dyn SyncStatus>,
        ai: Arc<dyn AiStatus>,
        profile: &'static str,
        clock: Arc<dyn Clock>,
    ) -> Digest {
        Digest { accounts, stored, syncing, ai, profile, clock }
    }

    pub fn answer(&self) -> Result<Value, Failure> {
        let stored = self.stored.digest(LIMIT)?;
        let faces = self.accounts.faces()?;
        let now = self.clock.now();
        let named = |list: &[(String, Option<String>)], account: &Value| -> Value {
            let account = account.as_str().unwrap_or_default();
            list.iter().find(|(e, _)| e == account).and_then(|(_, v)| v.clone()).map_or(Value::Null, Value::String)
        };
        let items: Vec<Value> = stored
            .rows
            .iter()
            .map(|r| {
                let mut it = Map::new();
                for name in ["message_key", "uid", "account", "kind"] {
                    let key = if name == "message_key" { "key" } else { name };
                    it.insert(key.into(), col(r, name).clone());
                }
                it.insert("alias".into(), named(&faces.aliases, col(r, "account")));
                it.insert("from_name".into(), or(col(r, "from_name"), col(r, "from_addr").clone()));
                it.insert("from_addr".into(), col(r, "from_addr").clone());
                it.insert("subject".into(), or(col(r, "subject"), json!("(no subject)")));
                it.insert("when".into(), Value::String(rel_time(text(r, "date_iso"), now)));
                it.insert("summary".into(), col(r, "summary").clone());
                it.insert("importance".into(), col(r, "importance").clone());
                for (name, value) in attention(col(r, "importance"), col(r, "category"), col(r, "action")) {
                    it.insert(name.into(), Value::Bool(value));
                }
                it.insert("category".into(), col(r, "category").clone());
                it.insert("action".into(), or(col(r, "action"), json!("")));
                let webmail = named(&faces.webmail, col(r, "account"));
                it.insert("link".into(), Value::String(deep_link(r, webmail.as_str())));
                it.insert("bucket".into(), col(r, "bucket").clone());
                for name in ["seen", "flagged", "has_attach"] {
                    it.insert(name.into(), Value::Bool(crate::listing::truthy(col(r, name))));
                }
                for name in ["thread_id", "folder", "ts"] {
                    it.insert(name.into(), col(r, name).clone());
                }
                Value::Object(it)
            })
            .collect();
        let stats: Map<String, Value> = stored.stats.iter().map(|(name, n)| ((*name).to_owned(), json!(n))).collect();
        let aliases: Map<String, Value> =
            faces.aliases.iter().map(|(e, a)| (e.clone(), a.clone().map_or(Value::Null, Value::String))).collect();
        let signatures: Map<String, Value> =
            faces.signatures.iter().map(|(e, s)| (e.clone(), Value::String(s.clone()))).collect();
        Ok(json!({
            "items": items, "stats": stats, "accounts": stored.accounts, "send_accounts": self.accounts.emails()?,
            "aliases": aliases, "signatures": signatures, "signature_default": faces.signature_default,
            "status": self.syncing.status(), "sync": self.syncing.sync(), "ai": self.ai.report()?,
            "profile": self.profile, "generated": iso(now),
        }))
    }
}

/// A link that opens the message in its provider's own web client, else the account's webmail, else ""
/// (`ProviderProfile.deep_link`). Proton's can only search by subject.
pub fn deep_link(r: &Row, webmail: Option<&str>) -> String {
    let account = text(r, "account").unwrap_or_default();
    let id = mm_kernel::text::strip(text(r, "message_id").unwrap_or_default()).trim_matches(['<', '>']);
    match text(r, "kind") {
        Some("gmail") if !id.is_empty() => {
            format!("https://mail.google.com/mail/u/?authuser={}#search/rfc822msgid:{}", quote(account), quote(id))
        }
        Some("proton") => {
            let subject: String = text(r, "subject").unwrap_or_default().chars().take(64).collect();
            format!("https://mail.proton.me/u/0/almost-all-mail#keyword={}", quote(&subject))
        }
        _ => webmail.unwrap_or_default().to_owned(),
    }
}

/// `urllib.parse.quote(text)`: every byte of its UTF-8 but letters, digits, `_.-~` and `/` as `%XX`.
pub fn quote(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for b in text.bytes() {
        if b.is_ascii_alphanumeric() || matches!(b, b'_' | b'.' | b'-' | b'~' | b'/') {
            out.push(char::from(b));
        } else {
            out.push_str(&format!("%{b:02X}"));
        }
    }
    out
}

/// The sync as it stands before any has run, and on the Rust server until the sync is ported: no account's
/// state, and the whole one idle.
#[derive(Debug, Default)]
pub struct NotSyncing;

impl SyncStatus for NotSyncing {
    fn status(&self) -> Value {
        json!({})
    }

    fn sync(&self) -> Value {
        json!({"running": false, "last": null, "result": "idle"})
    }
}

//! What fetching a message's body needs of the store beyond reading one (`StoredBodies`): keeping a body fetched with
//! what opening it records (`Ingest._keep_body`). The store (`mm-store`) implements it.

use std::fmt;

use mm_content::mime::{self, Attachment};
use mm_kernel::errors::Fault;

/// Of the To and Cc text kept for search: a long list, not a forged one's whole length (`RECIPIENTS_CHARS`).
const RECIPIENTS_CHARS: usize = 20_000;

/// A body fetched, and what opening it records: its parts, who it went to, and its text for search.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct KeptBody {
    /// Its raw RFC822 bytes.
    pub raw: Vec<u8>,
    /// When it was fetched.
    pub when: String,
    /// Its parts, as the reader lists them.
    pub parts: Vec<Attachment>,
    /// To and Cc as the text search reads (`recipients_of`).
    pub recipients: String,
    /// To and Cc, bare and lower-case (`recipient_addrs`).
    pub recipient_addrs: Vec<String>,
    /// Its text for search (`body_text_of`).
    pub body_text: String,
    /// The snippet a list shows, where its sync kept none (`snippet_of`).
    pub snippet: String,
}

impl KeptBody {
    /// What opening the message `raw` records, as of `when`.
    pub fn of(raw: Vec<u8>, when: String) -> KeptBody {
        let parsed = mime::parse(&raw);
        let recipients = format!("{} {}", parsed.to, parsed.cc);
        let recipients = mm_kernel::text::strip(&recipients).chars().take(RECIPIENTS_CHARS).collect();
        let body_text = mime::body_text_of(&parsed);
        let snippet = mime::snippet_of(&body_text);
        let parts = parsed.attachments;
        let recipient_addrs = mime::fields(&raw).recipient_addrs;
        KeptBody { raw, when, parts, recipients, recipient_addrs, body_text, snippet }
    }
}

/// Where a body fetched is kept.
pub trait KeptBodies: Send + Sync + fmt::Debug {
    /// Keep the message's body, in one transaction: its bytes, its parts in place of any kept before, whether it has
    /// any, who it went to, and its text, which search indexes and a list without a snippet shows.
    fn keep_body(&self, pk: i64, body: &KeptBody) -> Result<(), Fault>;
}

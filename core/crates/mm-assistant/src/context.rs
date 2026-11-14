//! What the model reads of a conversation (`application.context`): the first message and the last 19, each trimmed of
//! its quotes and within the caps. A message whose body hasn't come down yet is asked of the Mail port, which speaks to
//! the server; nothing here does. Thread summaries, replies and extraction all read through this.

use std::sync::Arc;

use mm_kernel::errors::{Fault, NOT_YET};

use crate::ports::{AccountList, ConversationBy, Mail, Said};
use crate::text::{cut, length, lines, strip};

pub const CAP_MSGS: usize = 20;
pub const CAP_PER_MSG: usize = 1500;
pub const CAP_TOTAL: usize = 14000;

/// What a message with no thread goes by here: its Message-ID, or if it has none, its pk.
pub fn lone_key(message_id: Option<&str>, pk: i64) -> String {
    match message_id.filter(|m| !m.is_empty()) {
        Some(id) => format!("m:{id}"),
        None => format!("pk:{pk}"),
    }
}

/// `^\s*>`: a quoted line.
pub fn quoted(line: &str) -> bool {
    line.trim_start_matches(mm_kernel::text::is_space).starts_with('>')
}

fn starts_ci(s: &str, prefix: &str) -> bool {
    s.len() >= prefix.len() && s.as_bytes()[..prefix.len()].eq_ignore_ascii_case(prefix.as_bytes())
}

/// `^(On .+ wrote:|From:.*|-{3,} ?Forwarded)`, any case: where a reply's quoted history begins.
fn attribution(line: &str) -> bool {
    // "On ", at least one character, then " wrote:".
    if starts_ci(line, "On ") && line.to_ascii_lowercase()[3..].match_indices(" wrote:").any(|(i, _)| i >= 1) {
        return true;
    }
    if starts_ci(line, "From:") {
        return true;
    }
    let dashes = line.bytes().take_while(|b| *b == b'-').count();
    if dashes >= 3 {
        let rest = &line[dashes..];
        let rest = rest.strip_prefix(' ').unwrap_or(rest);
        return starts_ci(rest, "Forwarded");
    }
    false
}

/// A message's own words: quoted lines left out, and nothing from where its quoted history begins, within the cap.
pub fn trim(text: &str) -> String {
    let mut out: Vec<&str> = Vec::new();
    for line in lines(text) {
        if quoted(line) {
            continue;
        }
        if attribution(strip(line)) {
            break;
        }
        out.push(line);
    }
    cut(strip(&out.join("\n")), CAP_PER_MSG).to_owned()
}

/// Reads conversations for the model.
#[derive(Debug, Clone)]
pub struct Context {
    mail: Arc<dyn Mail>,
    accounts: Arc<dyn AccountList>,
}

impl Context {
    pub fn new(mail: Arc<dyn Mail>, accounts: Arc<dyn AccountList>) -> Context {
        Context { mail, accounts }
    }

    pub fn mail(&self) -> &Arc<dyn Mail> {
        &self.mail
    }

    /// The accounts the app may use.
    pub fn accounts(&self) -> Result<Vec<String>, Fault> {
        self.accounts.emails()
    }

    /// A conversation's messages, oldest first, each Message-ID once. A thread id `m:<Message-ID>` or `pk:<pk>` names
    /// a message with no thread.
    pub fn rows(&self, account: &str, thread_id: &str) -> Result<Vec<Said>, Fault> {
        let (by, value) = match thread_id.split_once(':') {
            Some(("m", value)) => (ConversationBy::MessageId, value),
            Some(("pk", value)) => (ConversationBy::Pk, value),
            _ => (ConversationBy::Thread, thread_id),
        };
        let mut seen = std::collections::HashSet::new();
        let mut out = Vec::new();
        for r in self.mail.conversation(account, by, value)? {
            let key = match r.message_id.as_deref().map(strip).filter(|m| !m.is_empty()) {
                Some(id) => id.to_owned(),
                None => format!("pk:{}", r.pk),
            };
            if seen.insert(key) {
                out.push(r);
            }
        }
        Ok(out)
    }

    /// A message's text, asked of its server only if it has none stored and none has come down yet. A fetch that
    /// fails is no text; one the Rust server can't make yet is `NOT_YET`.
    pub fn body_of(&self, r: &Said) -> Result<String, Fault> {
        let stored =
            [&r.body_text, &r.snippet].into_iter().flatten().find(|t| !t.is_empty()).cloned().unwrap_or_default();
        if !stored.is_empty() || r.body_fetched {
            return Ok(stored);
        }
        match self.mail.fetch_text(r.pk) {
            Ok(text) => Ok(text.unwrap_or_default()),
            Err(fault) if fault == NOT_YET => Err(fault),
            Err(_) => Ok(String::new()),
        }
    }

    /// The blocks the model is shown, and how many older messages were left out.
    pub fn gather(&self, rows: &[Said]) -> Result<(Vec<String>, usize), Fault> {
        let mut omitted = 0;
        let kept: Vec<&Said> = if rows.len() > CAP_MSGS {
            omitted = rows.len() - CAP_MSGS;
            std::iter::once(&rows[0]).chain(&rows[rows.len() - (CAP_MSGS - 1)..]).collect()
        } else {
            rows.iter().collect()
        };
        let mut blocks = Vec::new();
        let mut total = 0;
        for (i, r) in kept.into_iter().enumerate() {
            let mut body = trim(&self.body_of(r)?);
            if total + length(&body) > CAP_TOTAL {
                body = cut(&body, CAP_TOTAL.saturating_sub(total)).to_owned();
            }
            total += length(&body);
            let addr = r.from_addr.as_deref().unwrap_or_default();
            let who = [&r.from_name, &r.from_addr]
                .into_iter()
                .flatten()
                .find(|t| !t.is_empty())
                .map_or("someone", String::as_str);
            let date = r.date_iso.as_deref().unwrap_or_default();
            blocks.push(format!("--- [{}] {who} <{addr}> \u{2014} {date}\n{body}", i + 1));
            if total >= CAP_TOTAL {
                break;
            }
        }
        Ok((blocks, omitted))
    }
}

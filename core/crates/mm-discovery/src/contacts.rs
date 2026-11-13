//! A person as your mail knows them (`mailmind.discovery.application.contacts` and `domain.contacts`): autocomplete
//! and the contact card. Local SQL over mail already stored: nothing leaves this machine.

use std::sync::Arc;

use mm_kernel::errors::{Failure, Problem};
use mm_kernel::relative::{ago, rel_time};
use mm_kernel::seams::Clock;
use mm_kernel::text::strip;
use serde_json::{Value, json};

use crate::ports::ContactMail;
use crate::search::{NO_SUBJECT, cut};
use crate::text::lower;

/// What autocomplete looks for: the start of what was typed is plenty.
pub fn needle(q: Option<&str>) -> String {
    cut(&lower(strip(q.unwrap_or_default())), 64)
}

/// The address a card is for, or None for what couldn't be one.
pub fn address(addr: Option<&str>) -> Option<String> {
    let a = lower(strip(addr.unwrap_or_default()));
    a.contains('@').then_some(a)
}

#[derive(Debug)]
pub struct Contacts {
    mail: Arc<dyn ContactMail>,
    clock: Arc<dyn Clock>,
}

impl Contacts {
    pub fn new(mail: Arc<dyn ContactMail>, clock: Arc<dyn Clock>) -> Contacts {
        Contacts { mail, clock }
    }

    /// Everyone who has written to you, the most often first and then the latest.
    pub fn autocomplete(&self, q: Option<&str>) -> Result<Value, Failure> {
        let items: Vec<Value> = self
            .mail
            .senders_like(&needle(q))?
            .into_iter()
            .map(|s| json!({"addr": s.addr, "name": s.name.unwrap_or_default(), "n": s.n}))
            .collect();
        Ok(json!({"ok": true, "items": items}))
    }

    /// One sender's card: how often they wrote and you replied, which accounts they write to and their latest.
    pub fn card(&self, addr: Option<&str>) -> Result<Value, Failure> {
        let a = address(addr).ok_or_else(|| Problem::refused("bad address"))?;
        let (tally, accounts, recent) = self.mail.card(&a)?;
        let now = self.clock.now();
        let said = |ts: Option<i64>| ts.filter(|t| *t != 0).map(|t| ago(Some(t), now));
        let recent: Vec<Value> = recent
            .iter()
            .map(|r| {
                json!({
                    "key": r.key, "folder": r.folder, "uid": r.uid, "account": r.account,
                    "subject": r.subject.as_deref().filter(|s| !s.is_empty()).unwrap_or(NO_SUBJECT),
                    "thread_id": r.thread_id, "when": rel_time(r.date_iso.as_deref(), now), "seen": r.seen,
                })
            })
            .collect();
        Ok(json!({
            "ok": true,
            "addr": a,
            "name": tally.name.unwrap_or_default(),
            "count": tally.n,
            "replied": tally.replied,
            "first_ago": said(tally.first),
            "known": tally.n > 0,
            "last_ago": said(tally.last),
            "accounts": accounts,
            "recent": recent,
        }))
    }
}

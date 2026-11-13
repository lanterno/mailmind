//! The Screener and a person's verdict on a sender (`mailmind.curation.application.screener`), the stored mail a rule
//! covers classified again (`mailbox.application.sync.reclassify`, which reads only the classifier and the rules),
//! and the audit trail as the page reads it.

use std::sync::Arc;

use mm_kernel::errors::{Failure, Problem};
use mm_kernel::relative::rel_time;
use mm_kernel::seams::Clock;
use serde_json::{Value, json};

use crate::classifier::{by_rule, classify};
use crate::ports::{AuditTrail, Filing, Mailboxes, Reclassified, RuleBook, SenderMail};
use crate::rulebook::{VERDICTS, decided, domain_of, is_token, token_of};
use crate::text::lower;

/// The most the queue lists, of however many are left to decide on.
pub const SHOWN: usize = 100;

#[derive(Debug)]
pub struct Screener {
    rules: Arc<dyn RuleBook>,
    mail: Arc<dyn SenderMail>,
    boxes: Arc<dyn Mailboxes>,
    filing: Arc<dyn Filing>,
    clock: Arc<dyn Clock>,
}

impl Screener {
    pub fn new(
        rules: Arc<dyn RuleBook>,
        mail: Arc<dyn SenderMail>,
        boxes: Arc<dyn Mailboxes>,
        filing: Arc<dyn Filing>,
        clock: Arc<dyn Clock>,
    ) -> Screener {
        Screener { rules, mail, boxes, filing, clock }
    }

    /// `GET /api/rules`.
    pub fn listed(&self) -> Result<Value, Failure> {
        Ok(json!({"rules": self.rules.load()?.to_json()}))
    }

    /// The first-contact gate: each Inbox sender no keep or block rule covers yet, the latest first.
    pub fn queue(&self, account: Option<&str>) -> Result<Value, Failure> {
        let Some(account) = account.filter(|a| !a.is_empty()) else {
            return Ok(json!({"ok": true, "account": account, "items": [], "total": 0}));
        };
        if !self.boxes.known()?.contains(account) {
            return Ok(json!({"ok": true, "account": account, "items": [], "total": 0}));
        }
        let sets = self.rules.load()?.sets();
        let now = self.clock.now();
        let items: Vec<Value> = self
            .mail
            .inbox_senders(account)?
            .into_iter()
            .filter(|row| !decided(&row.addr, &sets))
            .map(|row| {
                json!({
                    "from_addr": row.addr,
                    "from_name": row.name.as_deref().filter(|n| !n.is_empty()).unwrap_or(&row.addr),
                    "domain": domain_of(&row.addr),
                    "count": row.n,
                    "when": rel_time(row.iso.as_deref(), now),
                })
            })
            .collect();
        let total = items.len();
        Ok(
            json!({"ok": true, "account": account, "items": items.into_iter().take(SHOWN).collect::<Vec<_>>(), "total": total}),
        )
    }

    /// A keep, block or remove from the person, on a sender or a domain; a block with `pk` trashes that copy too.
    /// `verdict` is what the request said, if anything.
    pub fn rule(&self, token: Option<&str>, verdict: Option<&str>, pk: Option<i64>) -> Result<Value, Failure> {
        let token = token_of(token.unwrap_or_default());
        if !is_token(&token) {
            return Err(Problem::refused("invalid token (need a domain or an address)").into());
        }
        let placed = match verdict {
            Some("remove") => {
                self.rules.change(&mut |rules| {
                    rules.remove(&token);
                    Ok(())
                })?;
                None
            }
            Some(v) if VERDICTS.contains(&v) => {
                let mut placed = "";
                self.rules.change(&mut |rules| {
                    placed = rules.add(&token, v);
                    Ok(())
                })?;
                Some(placed)
            }
            _ => return Err(Problem::refused("verdict must be keep|block|remove").into()),
        };
        let trashed = match (verdict, pk) {
            // Block closes the loop: it trashes the message too, and isn't undone. However that goes, the rule stands.
            (Some("block"), Some(pk)) => self.filing.trash_now(pk).unwrap_or(false),
            _ => false,
        };
        let reclassified = self.reclassify(&token)?;
        self.filing.stats_changed();
        Ok(json!({
            "ok": true,
            "token": token,
            "placed_in": placed,
            "reclassified": reclassified,
            "trashed": trashed,
            "rules": self.rules.load()?.to_json(),
        }))
    }

    /// Undo of a block (`Cleanup.reverse_block`, an `unblock` entry): the rule on `token` goes, and the mail it
    /// decided is classified again. Whether that went.
    pub fn reverse_block(&self, token: &str) -> bool {
        let token = token_of(token);
        let removed = self.rules.change(&mut |rules| {
            rules.remove(&token);
            Ok(())
        });
        removed.is_ok() && self.reclassify(&token).is_ok()
    }

    /// A rule on `token`, a sender or a domain, changed: classify again the stored mail it covers (`reclassify`).
    pub fn reclassify(&self, token: &str) -> Result<usize, Failure> {
        reclassify(&*self.rules, &*self.mail, token)
    }
}

/// A rule on `token`, a sender or a domain, changed: classify again the stored mail it covers, as mail arriving now
/// would be. Only a row a rule decides now or decided before: what else the classifier read as it arrived wasn't all
/// kept, so one no rule had a say in stays as it was judged. How many rows changed.
pub fn reclassify(rules: &dyn RuleBook, mail: &dyn SenderMail, token: &str) -> Result<usize, Failure> {
    let token = token_of(token);
    let sets = rules.load()?.sets();
    let mut changes = Vec::new();
    for r in mail.classified_from(&token)? {
        let addr = lower(r.from_addr.as_deref().unwrap_or_default()).into_owned();
        let has_unsub = r.list_unsub_method.as_deref().unwrap_or("none") != "none";
        let said = classify(
            &addr,
            r.subject.as_deref().unwrap_or_default(),
            r.from_name.as_deref().unwrap_or_default(),
            has_unsub,
            &sets,
        );
        if !(by_rule(Some(&said.reason)) || by_rule(r.reason.as_deref())) {
            continue;
        }
        if (Some(said.bucket.name()), Some(said.reason.as_ref())) != (r.bucket.as_deref(), r.reason.as_deref()) {
            changes.push(Reclassified {
                pk: r.pk,
                bucket: said.bucket.name(),
                confidence: said.confidence.name(),
                reason: said.reason.into_owned(),
                is_junk: said.bucket.is_junk(),
            });
        }
    }
    if !changes.is_empty() {
        mail.set_classifications(&changes)?;
    }
    Ok(changes.len())
}

/// The audit trails as the page reads them (`GET /api/audit`).
#[derive(Debug)]
pub struct Audit {
    trail: Arc<dyn AuditTrail>,
}

impl Audit {
    pub fn new(trail: Arc<dyn AuditTrail>) -> Audit {
        Audit { trail }
    }

    /// The newest `limit` records, newest first, and whether there are older ones.
    pub fn read(&self, limit: i64) -> Result<Value, Failure> {
        let (events, has_more) = self.trail.newest(limit.max(0))?;
        Ok(json!({"events": events, "has_more": has_more}))
    }
}

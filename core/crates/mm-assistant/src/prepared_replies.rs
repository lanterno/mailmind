//! Drafting ahead (`application.prepared_replies`): an editable reply made ready for mail that wants one, while the
//! person reads it (the kick), in the background for the best few, and banked from replies asked for. Never sent: every
//! path ends in the composer.
//!
//! `draft_ahead` is the only cache, keyed on (account, `t:<thread>` or `m:<Message-ID>`), never a pk, and every serve
//! checks the fingerprint, the age, whether the mail still wants a reply and the thread's anchor again, so a stale
//! draft is never shown. One generation at a time for each key; the cost governor says when one may run.

use std::collections::{HashMap, HashSet};
use std::sync::Arc;
use std::time::Duration;

use mm_kernel::errors::{Fault, Problem};
use mm_kernel::events::{Events, Topic};
use mm_kernel::seams::Clock;
use serde_json::{Value, json};

use crate::background_ports::DraftCandidates;
use crate::context::Context;
use crate::digest;
use crate::flights::SingleFlight;
use crate::ports::{Answers, Mail, Message, NewDraft};
use crate::prepared_reply::{self as rules, Breaker};
use crate::prompts;
use crate::refusals::AiFailure;
use crate::reply_draft::{Draft, ReplyDrafts};
use crate::text::cut;

/// How long a second generation for a key waits on the first.
const WAIT: Duration = Duration::from_secs(45);
/// Seconds between the background rounds, and before the first, which lets the first sync and triage settle.
pub const DRAFT_TICK_INTERVAL: i64 = 180;
pub const DRAFT_TICK_STARTUP_DELAY: i64 = 120;
/// Warm drafts kept for at most this many of the best candidates, one drafted a round...
pub const DRAFT_TOPK: usize = 6;
/// ...and at most this many for one account.
pub const DRAFT_TOPK_PER_ACCOUNT: usize = 2;
/// Only mail from the last three days: older mail drafts on demand.
pub const DRAFT_RECENCY_WINDOW: i64 = 3 * 86_400;

/// What a reply request asks: what to reply to, by whom, and how.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct ReplyAsked {
    pub account: Option<String>,
    pub thread_id: Option<String>,
    pub pk: Option<i64>,
    pub tone: Option<String>,
    pub instruction: Option<String>,
}

/// What `get` answers, and the generation it asks the caller to start, by the message's account and pk.
pub type Served = (Value, Option<(String, i64)>);

pub struct PreparedReplies {
    answers: Arc<dyn Answers>,
    mail: Arc<dyn Mail>,
    context: Context,
    replies: Arc<ReplyDrafts>,
    looks_human: Box<dyn Fn(&str) -> bool + Send + Sync>,
    clock: Arc<dyn Clock>,
    flights: SingleFlight<(String, String)>,
}

impl std::fmt::Debug for PreparedReplies {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("PreparedReplies").finish_non_exhaustive()
    }
}

fn status(said: &str) -> Value {
    json!({"ok": true, "status": said})
}

impl PreparedReplies {
    pub fn new(
        answers: Arc<dyn Answers>,
        context: Context,
        replies: Arc<ReplyDrafts>,
        looks_human: Box<dyn Fn(&str) -> bool + Send + Sync>,
        clock: Arc<dyn Clock>,
    ) -> PreparedReplies {
        PreparedReplies {
            answers,
            mail: context.mail().clone(),
            context,
            replies,
            looks_human,
            clock,
            flights: SingleFlight::default(),
        }
    }

    fn now(&self) -> i64 {
        self.clock.now().whole_seconds()
    }

    fn eligible(&self, row: &Message) -> bool {
        rules::eligible(row, &*self.looks_human)
    }

    /// The thread's state, from Message-IDs and times and never pks, so a renumbered folder leaves it alone; a lone
    /// message's from its Message-ID and whether its body is here. Taken before a generation, so a thread that changes
    /// meanwhile can only cost another one, never serve a stale draft.
    fn fingerprint(&self, account: &str, thread_id: Option<&str>, pk: i64) -> Result<String, Fault> {
        if let Some(thread_id) = thread_id {
            let stamps = self.mail.thread_stamps(account, thread_id)?;
            if stamps.is_empty() {
                return Ok("t2:gone".to_owned());
            }
            let newest = stamps.iter().map(|(_, ts)| ts.unwrap_or(0)).max().unwrap_or(0);
            return Ok(digest::fingerprint(stamps.iter().map(|(id, _)| id.as_deref()), newest));
        }
        Ok(match self.mail.message_stamp(pk)? {
            Some((id, fetched)) => digest::message_fingerprint(id.as_deref(), fetched),
            None => digest::message_fingerprint(None, Some(0)),
        })
    }

    /// `POST /api/ai/draft-reply`: an editable reply. A draft drafted ahead for the thread as it is now answers a
    /// request with no instruction in the default tone for nothing; such a reply made now is banked for the next.
    pub fn reply(&self, asked: &ReplyAsked) -> Result<Value, AiFailure> {
        let tone = cut(asked.tone.as_deref().filter(|t| !t.is_empty()).unwrap_or(prompts::DEFAULT_TONE), 16).to_owned();
        let instruction = cut(asked.instruction.as_deref().unwrap_or_default(), prompts::INSTRUCTION_MAX).to_owned();
        let default_shape = rules::default_shaped(&instruction, &tone);
        let mut account = asked.account.clone().filter(|a| !a.is_empty());
        let mut thread_id = asked.thread_id.clone().filter(|t| !t.is_empty());
        let mut pk = asked.pk;
        let mut banked = None;
        let mut whose = None;
        if let Some(asked_pk) = asked.pk {
            let row = self.mail.message(asked_pk)?.ok_or_else(|| Problem::not_found("not found"))?;
            let key = rules::key_of(&row);
            let tid = row.thread_id.as_deref().filter(|t| !t.is_empty());
            let fp = self.fingerprint(&row.account, tid, row.pk)?;
            if default_shape && let Some((acct, ref_key)) = &key {
                let stored = self.answers.draft_ahead(acct, ref_key)?;
                let now = self.now();
                if let Some(da) = stored.filter(|da| rules::servable(Some(da), &fp, now)) {
                    self.answers.draft_ahead_touched(acct, ref_key, now)?;
                    return Ok(json!({"ok": true, "body": da.body,
                                     "tone": da.tone.filter(|t| !t.is_empty()).unwrap_or(tone),
                                     "model": da.model.filter(|m| !m.is_empty()).unwrap_or_else(|| "cache".into())}));
                }
            }
            account = account.or_else(|| Some(row.account.clone()));
            thread_id = thread_id.or_else(|| tid.map(str::to_owned));
            pk = if tid.is_none() { Some(row.pk) } else { None };
            banked = key.map(|key| (key, fp, row.message_id.clone()));
            whose = Some(row.account);
        }
        let accounts = self.context.accounts()?;
        let Some(account) = account.filter(|a| accounts.contains(a)) else {
            return Err(Problem::bad_request().into());
        };
        let made = self.replies.draft(&Draft {
            account: whose.as_deref().unwrap_or(&account),
            thread_id: thread_id.as_deref(),
            pk,
            instruction: &instruction,
            tone: &tone,
            me: &account,
            feature: "draft",
        })?;
        // A reply in the default shape is banked: reopening the composer is then free. Banking is best effort.
        if default_shape && let Some(((acct, ref_key), fp, mid)) = banked {
            let draft = NewDraft {
                fp: &fp,
                body: made["body"].as_str().unwrap_or_default(),
                tone: made["tone"].as_str(),
                model: made["model"].as_str(),
                src_message_id: mid.as_deref(),
                source: "demand",
            };
            let _ = self.answers.put_draft_ahead(&acct, &ref_key, &draft, self.now());
        }
        Ok(made)
    }

    /// `GET /api/ai/draft-ahead`: the draft for the message `pk` if one is ready, else whether one is being made. It
    /// never drafts while asked: where a kick may start one (`kick` and not `peek`, and the mail wants a reply), the
    /// answer is "generating" and the generation to start comes back beside it.
    pub fn get(&self, pk: i64, peek: bool, kick: bool) -> Result<Served, AiFailure> {
        let peek = peek || !kick;
        let none = || Ok((status("none"), None));
        if pk == 0 {
            return none();
        }
        let Some(row) = self.mail.message(pk)? else { return none() };
        let tid = row.thread_id.as_deref().filter(|t| !t.is_empty());
        let anchor = match tid {
            Some(tid) => self.mail.thread_anchor(&row.account, tid)?,
            None => None,
        };
        if !rules::for_the_anchor(pk, tid, anchor) || !self.eligible(&row) {
            return none();
        }
        let Some(key) = rules::key_of(&row) else { return none() };
        let fp = self.fingerprint(&row.account, tid, pk)?;
        let stored = self.answers.draft_ahead(&key.0, &key.1)?;
        let now = self.now();
        if let Some(da) = stored.as_ref().filter(|da| rules::servable(Some(da), &fp, now)) {
            // Only a draft served is touched: asking takes no lock.
            self.answers.draft_ahead_touched(&key.0, &key.1, now)?;
            return Ok((json!({"ok": true, "status": "ready", "body": da.body, "tone": da.tone}), None));
        }
        if self.flights.busy(&key) {
            return Ok((status("generating"), None));
        }
        // A failure lately backs off: the page falls back to asking for one.
        if peek || rules::failed_lately(stored.as_ref(), now) || !self.answers.reply_worthy(pk)? {
            return none();
        }
        Ok((status("generating"), Some((row.account, pk))))
    }

    /// One generation for the message `pk`, one at a time for each key. Writes the cache, never sends. Some(true) when
    /// this one led and worked, Some(false) only when it led and failed, None when it joined another's, found the
    /// draft warm, found nothing worth drafting, or wasn't let go: a breaker counts only what it led. A draft made is
    /// told to the open tabs on `events`.
    pub fn generate(&self, account: Option<&str>, pk: i64, source: &str, events: &dyn Events) -> Option<bool> {
        let row = self.mail.message(pk).ok().flatten()?;
        let key = rules::key_of(&row)?;
        if !self.eligible(&row) {
            return None;
        }
        let acct = account.map_or_else(|| row.account.clone(), str::to_owned);
        let tid = row.thread_id.clone().filter(|t| !t.is_empty());
        let fp = self.fingerprint(&acct, tid.as_deref(), pk).ok()?;
        let warm = || -> Option<Option<bool>> {
            let stored = self.answers.draft_ahead(&key.0, &key.1).ok()?;
            rules::servable(stored.as_ref(), &fp, self.now()).then_some(None)
        };
        let make = || -> Option<bool> {
            let made = self.replies.draft(&Draft {
                account: &acct,
                thread_id: tid.as_deref(),
                pk: if tid.is_some() { None } else { Some(pk) },
                instruction: "",
                tone: prompts::DEFAULT_TONE,
                me: &acct,
                feature: "draft-ahead",
            });
            let now = self.now();
            match made {
                Ok(made) => {
                    let draft = NewDraft {
                        fp: &fp,
                        body: made["body"].as_str().unwrap_or_default(),
                        tone: made["tone"].as_str(),
                        model: made["model"].as_str(),
                        src_message_id: row.message_id.as_deref(),
                        source,
                    };
                    let kept = self
                        .answers
                        .put_draft_ahead(&key.0, &key.1, &draft, now)
                        .and_then(|()| self.answers.draft_ahead_gc(now, false));
                    if kept.is_err() {
                        mm_kernel::log::warning("ai", "draft-ahead failed", &[], Some("Fault"));
                        let _ = self.answers.draft_ahead_failed(&key.0, &key.1, now);
                        return Some(false);
                    }
                    events.publish(Topic::DraftReady, json!({"account": acct, "key": row.message_key}));
                    Some(true)
                }
                Err(AiFailure::NotSent(_)) => None,
                Err(AiFailure::Failure(_)) => {
                    mm_kernel::log::warning("ai", "draft-ahead failed", &[], Some("Unreachable"));
                    let _ = self.answers.draft_ahead_failed(&key.0, &key.1, now);
                    Some(false)
                }
            }
        };
        self.flights.run(key.clone(), make, warm, WAIT, true).flatten()
    }

    /// One round's worth of work from `rows`, the candidates as the store lists them, best first: the best that wants
    /// a reply and isn't warm, being made or backing off, by its account and pk. None when the warm set is full or
    /// nothing is left. Reading it spends nothing.
    pub fn pick(&self, rows: &[Message]) -> Result<Option<(String, i64)>, Fault> {
        let now = self.now();
        let mut seen = HashSet::new();
        let mut warm_total = 0;
        let mut warm_of: HashMap<&str, usize> = HashMap::new();
        for r in rows {
            if warm_total >= DRAFT_TOPK {
                break;
            }
            if !self.eligible(r) {
                continue;
            }
            let Some(key) = rules::key_of(r) else { continue };
            if !seen.insert(key.clone()) {
                continue;
            }
            let stored = self.answers.draft_ahead(&key.0, &key.1)?;
            let tid = r.thread_id.as_deref().filter(|t| !t.is_empty());
            let fp = self.fingerprint(&r.account, tid, r.pk)?;
            if rules::servable(stored.as_ref(), &fp, now) {
                // Already warm: what keeps a restart from drafting them all again.
                warm_total += 1;
                *warm_of.entry(&r.account).or_default() += 1;
                continue;
            }
            if warm_of.get(r.account.as_str()).copied().unwrap_or_default() >= DRAFT_TOPK_PER_ACCOUNT
                || rules::backing_off(stored.as_ref(), now)
                || self.flights.busy(&key)
            {
                continue;
            }
            return Ok(Some((r.account.clone(), r.pk)));
        }
        Ok(None)
    }

    /// A pre-drafter's round, with its own breaker: at most one reply drafted ahead, picked from `candidates`, only when
    /// the cost governor would let it go (`held_back` false), then the expired drafts collected.
    pub fn round(
        &self,
        breaker: &mut Breaker,
        held_back: bool,
        candidates: &dyn DraftCandidates,
        events: &dyn Events,
    ) -> Result<(), Fault> {
        let now = self.clock.now().seconds();
        if breaker.is_open(now) || held_back {
            return Ok(());
        }
        let rows = candidates.draft_candidates(self.now() - DRAFT_RECENCY_WINDOW)?;
        if let Some((account, pk)) = self.pick(&rows)? {
            let led = self.generate(Some(&account), pk, "topk", events);
            if breaker.heard(led, self.clock.now().seconds()) {
                mm_kernel::log::warning("draft", "tick suspended 30m after repeated failures", &[], None);
            }
        }
        self.answers.draft_ahead_gc(self.now(), true)
    }
}

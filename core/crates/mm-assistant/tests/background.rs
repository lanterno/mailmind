//! The Assistant's background work (`tests/unit/assistant/test_assessment_record.py`, `test_ai_draft_ahead.py` and
//! `bootstrap.background`'s rounds): triage of what is new and what it keeps, a reply drafted ahead for the best mail
//! that wants one, and the morning's brief and the week's review written before anyone opens them. Every call goes
//! through the gateway, so the ledger names the feature it went under.

use std::collections::HashMap;
use std::sync::atomic::{AtomicI64, Ordering};
use std::sync::{Arc, Mutex};

use mm_assistant::ahead::{Ahead, is_ahead_morning, is_morning};
use mm_assistant::asking::Asking;
use mm_assistant::assessments::{Assessments, assessment_of, importance_of, triage_prompt, triage_schema};
use mm_assistant::background_ports::{Assessment, DraftCandidates, Outcome, Pending, Triage};
use mm_assistant::briefs::Briefs;
use mm_assistant::calls::Calls;
use mm_assistant::context::Context;
use mm_assistant::digest::fingerprint;
use mm_assistant::gateway::{Budget, Gateway};
use mm_assistant::governor::CostGovernor;
use mm_assistant::ports::{
    AccountList, AiSettings, Answers, Around, Cards, ConversationBy, CopyNow, Engine, Glance, InForce, LanguageModel,
    Mail, Message, ModelError, NewDraft, Overview, Prompt, Reply, Said, Spend, Stamp, StoredCard, StoredDraft,
    StoredReview, StoredSummary, Usage, UsageLedger, WeekRows,
};
use mm_assistant::prepared_replies::{DRAFT_RECENCY_WINDOW, PreparedReplies};
use mm_assistant::prepared_reply::{Breaker, SUSPEND};
use mm_assistant::reply_draft::ReplyDrafts;
use mm_assistant::reviews::Reviews;
use mm_assistant::schema::structured;
use mm_kernel::errors::Fault;
use mm_kernel::events::{Events, Topic};
use mm_kernel::seams::{Clock, Moment};
use mm_kernel::zone::Utc;
use serde_json::{Value, json};

/// Monday 2 November 2026, noon UTC, and that Monday's midnight.
const NOW: i64 = 1_793_620_800;
const MONDAY: i64 = NOW - 12 * 3600;
const ADA: &str = "ada@example.test";
const BEA: &str = "bea@example.test";
/// A model other than the one asked for, as a server-side fallback leaves.
const OTHER: &str = "claude-opus-4-8";

#[derive(Debug)]
struct Frozen(AtomicI64);

impl Clock for Frozen {
    fn now(&self) -> Moment {
        Moment::from_micros(self.0.load(Ordering::SeqCst))
    }
}

impl Frozen {
    fn at(&self, seconds: i64) {
        self.0.store(seconds * 1_000_000, Ordering::SeqCst);
    }
    fn shift(&self, seconds: i64) {
        self.0.fetch_add(seconds * 1_000_000, Ordering::SeqCst);
    }
}

/// Answers by what it is asked: triage by the subject it is shown, anything else with a card and a draft, and drafts
/// fail while `down` is set. It keeps the prompts.
#[derive(Debug, Default)]
struct Model {
    prompts: Mutex<Vec<Prompt>>,
    down: Mutex<bool>,
}

impl LanguageModel for Model {
    fn complete(&self, prompt: &Prompt) -> Result<Reply, ModelError> {
        self.prompts.lock().expect("prompts").push(prompt.clone());
        let reply = |text: &str, stop: &str| Reply {
            text: text.into(),
            model: OTHER.into(),
            usage: Usage::default(),
            stop_reason: stop.into(),
        };
        let text = &prompt.text;
        if text.contains("You are triaging") {
            return if text.contains("Subject: broken") {
                Err(ModelError::new("Claude API 400: bad", Some(400)))
            } else if text.contains("Subject: prose") {
                Ok(reply("Just prose.", "end_turn"))
            } else if text.contains("Subject: refused") {
                Ok(reply("", "refusal"))
            } else {
                Ok(reply(
                    "{\"summary\": \"Said once.\", \"importance\": 9, \"category\": \"info\", \"action\": \"Reply\"}",
                    "end_turn",
                ))
            };
        }
        if *self.down.lock().expect("down") {
            return Err(ModelError::new("Claude API 500: down", Some(500)));
        }
        Ok(reply(
            "{\"narrative\": \"Written ahead.\", \"groups\": [], \"next\": [], \"body\": \"Drafted ahead.\"}",
            "end_turn",
        ))
    }
}

#[derive(Debug)]
struct Keyed(Option<Arc<dyn LanguageModel>>);

impl Engine for Keyed {
    fn in_force(&self) -> InForce {
        match &self.0 {
            Some(model) => InForce::On { model: model.clone(), key_from: "ANTHROPIC_API_KEY" },
            None => InForce::Off { key_from: "ANTHROPIC_API_KEY" },
        }
    }
}

/// The features calls went under, in order.
#[derive(Debug, Default)]
struct Ledger(Mutex<Vec<String>>);

impl UsageLedger for Ledger {
    fn record(&self, spend: &Spend) -> Result<(), Fault> {
        self.0.lock().expect("ledger").push(spend.feature.clone());
        Ok(())
    }
    fn spent(&self, _day: &str) -> Result<i64, Fault> {
        Ok(0)
    }
    fn reserved(&self) -> Result<i64, Fault> {
        Ok(0)
    }
    fn in_batches(&self) -> Result<i64, Fault> {
        Ok(0)
    }
}

#[derive(Debug)]
struct NoLimit;

impl AiSettings for NoLimit {
    fn key_named(&self) -> Result<bool, Fault> {
        Ok(true)
    }
    fn daily_limit(&self) -> Result<Option<Value>, Fault> {
        Ok(None)
    }
}

struct World {
    model: Arc<Model>,
    ledger: Arc<Ledger>,
    clock: Arc<Frozen>,
    calls: Arc<Calls>,
}

fn world(on: bool) -> World {
    let model = Arc::new(Model::default());
    let ledger = Arc::new(Ledger::default());
    let clock = Arc::new(Frozen(AtomicI64::new(NOW * 1_000_000)));
    let budget = Arc::new(Budget {
        ledger: ledger.clone(),
        settings: Arc::new(NoLimit),
        clock: clock.clone(),
        zone: Arc::new(Utc),
    });
    let governor = Arc::new(CostGovernor::new(budget.clone(), clock.clone()));
    let lm: Arc<dyn LanguageModel> = model.clone();
    let gateway = Arc::new(Gateway::new(Arc::new(Keyed(on.then_some(lm))), budget, governor));
    let status = Arc::new(mm_assistant::status::Status::new(gateway.clone(), clock.clone(), "claude-opus-5-5".into()));
    let calls = Arc::new(Calls::new(gateway, status, None, false));
    World { model, ledger, clock, calls }
}

impl World {
    fn sent(&self) -> Vec<Prompt> {
        std::mem::take(&mut *self.model.prompts.lock().expect("prompts"))
    }
    fn features(&self) -> Vec<String> {
        std::mem::take(&mut *self.ledger.0.lock().expect("ledger"))
    }
}

// ── triage ──────────────────────────────────────────────────────────────────────────────────────────

/// What triage hasn't summarized, and what it kept of each, as of when.
#[derive(Debug, Default)]
struct Queue {
    rows: Mutex<Vec<Pending>>,
    cutoff: Option<i64>,
    asked: Mutex<Vec<(i64, Option<i64>)>>,
    said: Mutex<HashMap<i64, (Assessment, String)>>,
    unkeepable: bool,
}

impl Queue {
    fn of(rows: Vec<Pending>) -> Queue {
        Queue { rows: Mutex::new(rows), ..Queue::default() }
    }
    fn said(&self) -> HashMap<i64, (Assessment, String)> {
        self.said.lock().expect("said").clone()
    }
}

impl Triage for Queue {
    fn batch_cutoff(&self) -> Result<Option<i64>, Fault> {
        Ok(self.cutoff)
    }
    fn needs_summary(&self, limit: i64, newer_than: Option<i64>) -> Result<Vec<Pending>, Fault> {
        self.asked.lock().expect("asked").push((limit, newer_than));
        let rows = self.rows.lock().expect("rows");
        let newer = rows.iter().filter(|r| r.ts.unwrap_or(0) > newer_than.unwrap_or(-1));
        Ok(newer.take(usize::try_from(limit).expect("a limit")).cloned().collect())
    }
    fn save_summary(&self, pk: i64, said: &Assessment, when: &str) -> Result<(), Fault> {
        if self.unkeepable {
            return Err(Fault::new("Locked"));
        }
        self.said.lock().expect("said").insert(pk, (said.clone(), when.to_owned()));
        Ok(())
    }
}

fn pending(pk: i64, subject: &str, ts: i64) -> Pending {
    Pending {
        pk,
        message_key: Some(pk * 10),
        from_name: Some("Lin Mori".into()),
        from_addr: Some("lin@example.test".into()),
        subject: Some(subject.into()),
        date_iso: Some("2026-11-02T08:00:00+00:00".into()),
        snippet: Some("Are you free on Thursday?".into()),
        ts: Some(ts),
    }
}

fn assessments(w: &World, queue: &Arc<Queue>) -> Assessments {
    Assessments::new(w.calls.clone(), queue.clone(), w.clock.clone())
}

fn reply(text: &str, stop: &str) -> Reply {
    Reply { text: text.into(), model: OTHER.into(), usage: Usage::default(), stop_reason: stop.into() }
}

#[test]
fn a_message_is_shown_to_triage_as_the_python_app_shows_it() {
    let prompt = triage_prompt(&pending(1, "Lunch", NOW));
    assert!(prompt.starts_with("You are triaging ONE email for a personal dashboard."));
    assert!(prompt.ends_with(
        "Email:\nFrom: Lin Mori <lin@example.test>\nSubject: Lunch\nDate: 2026-11-02T08:00:00+00:00\nBody:\nAre you free on Thursday?\n"
    ));
    let bare = triage_prompt(&Pending { pk: 2, snippet: Some("x".repeat(2000)), ..Pending::default() });
    assert!(bare.ends_with(&format!("From: None\nSubject: (no subject)\nDate: \nBody:\n{}\n", "x".repeat(1800))));
    let unnamed = Pending { from_name: Some(String::new()), subject: Some(String::new()), ..pending(3, "", NOW) };
    assert!(triage_prompt(&unnamed).contains("From: lin@example.test\nSubject: (no subject)\n"));
}

#[test]
fn an_importance_is_one_to_five_and_two_when_it_wont_read() {
    let table = [
        (json!(1), 1),
        (json!(5), 5),
        (json!(9), 5),
        (json!(0), 1),
        (json!(-3), 1),
        (json!("4"), 4),
        (json!(3.7), 3),
        (json!(null), 2),
        (json!("soon"), 2),
        (json!([]), 2),
        (json!({}), 2),
        (json!("99999999999999999999"), 5),
        (json!("-99999999999999999999"), 1),
    ];
    for (told, reads) in table {
        assert_eq!(importance_of(Some(&told)), reads, "{told}");
    }
    assert_eq!(importance_of(None), 2);
}

#[test]
fn what_triage_keeps_names_the_model_that_served_it_and_what_stands_in_says_why() {
    let row = pending(1, "Lunch", NOW);
    let good = "{\"summary\": \"Said once.\", \"importance\": 9, \"category\": \"info\", \"action\": \"Reply\"}";
    assert_eq!(
        assessment_of(&row, &reply(good, "end_turn"), "claude-opus-5-5", 2),
        Assessment {
            summary: "Said once.".into(),
            importance: 5,
            category: "info".into(),
            action: "Reply".into(),
            model: "claude-opus-5-5".into(),
            served_by: Some(OTHER.into()),
            prompt_version: Some(2),
            outcome: Some(Outcome::Assessed),
        }
    );
    let loose = assessment_of(&row, &reply("Sure: {\"summary\": 7} done", "end_turn"), "claude-opus-5-5", 2);
    assert_eq!(
        (loose.summary.as_str(), loose.importance, loose.category.as_str(), loose.action.as_str()),
        ("7", 2, "other", "")
    );
    for (text, stop, outcome) in [
        ("not json at all", "end_turn", Outcome::Unusable),
        ("{\"importance\": 3}", "end_turn", Outcome::Unusable),
        ("{\"summary\": \"\"}", "end_turn", Outcome::Unusable),
        ("", "refusal", Outcome::Refused),
    ] {
        let said = assessment_of(&row, &reply(text, stop), "claude-opus-5-5", 2);
        assert_eq!(
            said,
            Assessment {
                summary: "Lunch".into(),
                importance: 2,
                category: "other".into(),
                action: String::new(),
                model: "parse-error".into(),
                served_by: Some(OTHER.into()),
                prompt_version: Some(2),
                outcome: Some(outcome),
            },
            "{text}"
        );
    }
    let untitled = assessment_of(&Pending::default(), &reply("", "refusal"), "claude-opus-5-5", 2);
    assert_eq!(untitled.summary, "(no subject)");
}

#[test]
fn a_round_triages_whats_new_at_low_effort_and_keeps_what_each_reply_came_to() {
    let w = world(true);
    let queue = Arc::new(Queue::of(vec![
        pending(1, "Lunch", NOW - 1),
        pending(2, "prose", NOW - 2),
        pending(3, "refused", NOW - 3),
        pending(4, "broken", NOW - 4),
    ]));
    assert_eq!(assessments(&w, &queue).round(), Ok(None));
    assert_eq!(*queue.asked.lock().expect("asked"), [(300, None)]);
    let said = queue.said();
    let outcomes: HashMap<i64, (Option<Outcome>, &str, &str)> =
        said.iter().map(|(pk, (a, when))| (*pk, (a.outcome, a.model.as_str(), when.as_str()))).collect();
    assert_eq!(
        outcomes,
        HashMap::from([
            (1, (Some(Outcome::Assessed), "claude-opus-5-5", "2026-11-02T12:00:00+00:00")),
            (2, (Some(Outcome::Unusable), "parse-error", "2026-11-02T12:00:00+00:00")),
            (3, (Some(Outcome::Refused), "parse-error", "2026-11-02T12:00:00+00:00")),
        ])
    );
    assert_eq!((said[&1].0.importance, said[&1].0.served_by.as_deref()), (5, Some(OTHER)));
    let sent = w.sent();
    assert_eq!(sent.len(), 4);
    for prompt in &sent {
        assert_eq!(
            (prompt.effort.as_deref(), prompt.schema.as_ref()),
            (Some("low"), Some(&structured(&triage_schema())))
        );
    }
    assert!(w.features().iter().all(|f| f == "triage"));
}

#[test]
fn a_full_round_says_the_next_follows_at_once_and_one_that_cant_keep_what_it_said_fails() {
    let w = world(true);
    let queue = Arc::new(Queue::of((1..=300).map(|pk| pending(pk, "Lunch", NOW - pk)).collect()));
    assert_eq!(assessments(&w, &queue).round(), Ok(Some(0)));
    assert_eq!(queue.said().len(), 300);
    let stuck = Arc::new(Queue { unkeepable: true, ..Queue::of(vec![pending(1, "Lunch", NOW - 1)]) });
    assert_eq!(assessments(&w, &stuck).round(), Err(Fault::new("Locked")));
}

#[test]
fn triage_waits_while_ai_is_off_or_someone_is_using_it_and_leaves_alone_what_a_batch_still_holds() {
    let off = world(false);
    let queue = Arc::new(Queue::of(vec![pending(1, "Lunch", NOW - 1)]));
    assert_eq!(assessments(&off, &queue).round(), Ok(None));
    assert!(queue.asked.lock().expect("asked").is_empty() && off.sent().is_empty());

    let w = world(true);
    w.calls.call_claude("Asked for.", "thread").expect("an interactive call");
    w.sent();
    let queue = Arc::new(Queue {
        cutoff: Some(NOW - 600),
        ..Queue::of(vec![pending(1, "Lunch", NOW - 1), pending(2, "Older", NOW - 3600)])
    });
    assert_eq!(assessments(&w, &queue).round(), Ok(Some(45)));
    assert!(queue.asked.lock().expect("asked").is_empty() && w.sent().is_empty());
    w.clock.shift(46);
    assert_eq!(assessments(&w, &queue).round(), Ok(None));
    assert_eq!(*queue.asked.lock().expect("asked"), [(300, Some(NOW - 600))]);
    assert_eq!(queue.said().into_keys().collect::<Vec<_>>(), [1]);
}

// ── drafting ahead ──────────────────────────────────────────────────────────────────────────────────

/// Each thread's messages, each thread one message from a person to its account.
#[derive(Debug)]
struct Threads(Vec<Message>);

impl Mail for Threads {
    fn message(&self, pk: i64) -> Result<Option<Message>, Fault> {
        Ok(self.0.iter().find(|m| m.pk == pk).cloned())
    }
    fn conversation(&self, account: &str, by: ConversationBy, value: &str) -> Result<Vec<Said>, Fault> {
        let of = |m: &&Message| {
            by == ConversationBy::Thread && m.account == account && m.thread_id.as_deref() == Some(value)
        };
        Ok(self
            .0
            .iter()
            .filter(of)
            .map(|m| Said {
                pk: m.pk,
                account: m.account.clone(),
                from_addr: m.from_addr.clone(),
                subject: Some("Lunch on Thursday?".into()),
                body_text: Some("Are you free?".into()),
                message_id: m.message_id.clone(),
                body_fetched: true,
                ts: Some(100),
                ..Said::default()
            })
            .collect())
    }
    fn to_reply_to(&self, _pk: i64) -> Result<Vec<Said>, Fault> {
        Ok(vec![])
    }
    fn fetch_text(&self, _pk: i64) -> Result<Option<String>, Fault> {
        Ok(None)
    }
    fn thread_stamps(&self, _account: &str, thread_id: &str) -> Result<Vec<Stamp>, Fault> {
        Ok(vec![(Some(format!("<{thread_id}@example.test>")), Some(100))])
    }
    fn message_stamp(&self, _pk: i64) -> Result<Option<Stamp>, Fault> {
        Ok(None)
    }
    fn thread_anchor(&self, account: &str, thread_id: &str) -> Result<Option<i64>, Fault> {
        Ok(self.0.iter().find(|m| m.account == account && m.thread_id.as_deref() == Some(thread_id)).map(|m| m.pk))
    }
}

#[derive(Debug)]
struct Both;

impl AccountList for Both {
    fn emails(&self) -> Result<Vec<String>, Fault> {
        Ok(vec![ADA.into(), BEA.into()])
    }
}

/// The drafts kept ahead, and each collection of the expired ones (whether it took the orphans too).
#[derive(Debug, Default)]
struct Bank {
    drafts: Mutex<HashMap<(String, String), StoredDraft>>,
    collected: Mutex<Vec<bool>>,
}

impl Bank {
    fn keep(&self, account: &str, thread: &str, draft: StoredDraft) {
        self.drafts.lock().expect("drafts").insert((account.into(), format!("t:{thread}")), draft);
    }
    fn body(&self, account: &str, thread: &str) -> Option<String> {
        let drafts = self.drafts.lock().expect("drafts");
        drafts.get(&(account.into(), format!("t:{thread}"))).and_then(|d| d.body.clone())
    }
}

impl Answers for Bank {
    fn thread_summary(&self, _account: &str, _thread_id: &str, _scope: &str) -> Result<Option<StoredSummary>, Fault> {
        Ok(None)
    }
    fn save_thread_summary(&self, _summary: &StoredSummary) -> Result<(), Fault> {
        Ok(())
    }
    fn draft_ahead(&self, account: &str, ref_key: &str) -> Result<Option<StoredDraft>, Fault> {
        Ok(self.drafts.lock().expect("drafts").get(&(account.into(), ref_key.into())).cloned())
    }
    fn put_draft_ahead(&self, account: &str, ref_key: &str, d: &NewDraft<'_>, now: i64) -> Result<(), Fault> {
        let stored = StoredDraft {
            fp: Some(d.fp.into()),
            body: Some(d.body.into()),
            tone: d.tone.map(str::to_owned),
            model: d.model.map(str::to_owned),
            created_ts: now,
            ..StoredDraft::default()
        };
        self.drafts.lock().expect("drafts").insert((account.into(), ref_key.into()), stored);
        Ok(())
    }
    fn draft_ahead_failed(&self, account: &str, ref_key: &str, now: i64) -> Result<(), Fault> {
        let mut drafts = self.drafts.lock().expect("drafts");
        let draft = drafts.entry((account.into(), ref_key.into())).or_default();
        draft.fail_ts = now;
        draft.fail_count += 1;
        Ok(())
    }
    fn draft_ahead_touched(&self, _account: &str, _ref_key: &str, _now: i64) -> Result<(), Fault> {
        Ok(())
    }
    fn draft_ahead_gc(&self, _now: i64, orphans: bool) -> Result<(), Fault> {
        self.collected.lock().expect("collected").push(orphans);
        Ok(())
    }
    fn reply_worthy(&self, _pk: i64) -> Result<bool, Fault> {
        Ok(true)
    }
}

/// The candidates the store lists, and each moment they were asked for since.
#[derive(Debug, Default)]
struct Candidates {
    rows: Vec<Message>,
    since: Mutex<Vec<i64>>,
}

impl DraftCandidates for Candidates {
    fn draft_candidates(&self, since_ts: i64) -> Result<Vec<Message>, Fault> {
        self.since.lock().expect("since").push(since_ts);
        Ok(self.rows.clone())
    }
}

/// The open tabs, as what each was told.
#[derive(Debug, Default)]
struct Tabs(Mutex<Vec<(&'static str, Value)>>);

impl Events for Tabs {
    fn publish(&self, topic: Topic, data: Value) {
        self.0.lock().expect("tabs").push((topic.name(), data));
    }
}

fn mail(pk: i64, account: &str, thread: &str, from: &str) -> Message {
    Message {
        pk,
        account: account.into(),
        thread_id: Some(thread.into()),
        message_id: Some(format!("<m{pk}@example.test>")),
        message_key: Some(pk * 10),
        from_addr: Some(from.into()),
        folder: Some("INBOX".into()),
        ..Message::default()
    }
}

/// A draft kept for the thread as it is now, a minute old.
fn warm(thread: &str) -> StoredDraft {
    let id = format!("<{thread}@example.test>");
    StoredDraft {
        fp: Some(fingerprint([Some(id.as_str())], 100)),
        body: Some("Warm.".into()),
        created_ts: NOW - 60,
        ..StoredDraft::default()
    }
}

fn drafting(w: &World, rows: &[Message]) -> (PreparedReplies, Arc<Bank>) {
    let bank = Arc::new(Bank::default());
    let context = Context::new(Arc::new(Threads(rows.to_vec())), Arc::new(Both));
    let replies = Arc::new(ReplyDrafts::new(w.calls.clone(), context.clone()));
    let looks_human = Box::new(|local: &str| local != "noreply");
    (PreparedReplies::new(bank.clone(), context, replies, looks_human, w.clock.clone()), bank)
}

#[test]
fn drafting_ahead_picks_the_best_that_isnt_warm_with_two_warm_at_most_for_an_account() {
    let w = world(true);
    let rows = [
        mail(1, ADA, "t-news", "noreply@example.test"),
        mail(2, ADA, "t-lunch", "lin@example.test"),
        mail(3, ADA, "t-lunch", "lin@example.test"),
        mail(4, ADA, "t-venue", "ivo@example.test"),
        mail(5, ADA, "t-design", "mae@example.test"),
        mail(6, BEA, "t-rent", "kai@example.test"),
        mail(7, BEA, "t-tea", "lin@example.test"),
    ];
    let (drafting, bank) = drafting(&w, &rows);
    assert_eq!(drafting.pick(&rows), Ok(Some((ADA.into(), 2))));
    // Ada's lunch and venue are warm, so her design waits; Bea's rent failed a minute ago and backs off.
    bank.keep(ADA, "t-lunch", warm("t-lunch"));
    bank.keep(ADA, "t-venue", warm("t-venue"));
    bank.keep(BEA, "t-rent", StoredDraft { fail_ts: NOW - 60, fail_count: 1, ..StoredDraft::default() });
    assert_eq!(drafting.pick(&rows), Ok(Some((BEA.into(), 7))));
    bank.keep(BEA, "t-tea", warm("t-tea"));
    assert_eq!(drafting.pick(&rows), Ok(None));
    // Five minutes on, the backoff is over; a draft for a thread that has changed since is not warm.
    w.clock.shift(300);
    assert_eq!(drafting.pick(&rows), Ok(Some((BEA.into(), 6))));
    bank.keep(ADA, "t-venue", StoredDraft { fp: Some("t2:old".into()), ..warm("t-venue") });
    assert_eq!(drafting.pick(&rows), Ok(Some((ADA.into(), 4))));
    assert!(w.sent().is_empty());
}

#[test]
fn with_six_warm_drafting_ahead_has_nothing_to_do() {
    let w = world(true);
    let accounts = ["a@example.test", "b@example.test", "c@example.test", "d@example.test"];
    let mut rows: Vec<Message> = (0..6)
        .map(|n| {
            mail(n + 1, accounts[usize::try_from(n / 2).expect("an index")], &format!("t-{n}"), "lin@example.test")
        })
        .collect();
    rows.push(mail(7, accounts[3], "t-cold", "lin@example.test"));
    let (drafting, bank) = drafting(&w, &rows);
    for row in &rows[..5] {
        let thread = row.thread_id.clone().expect("a thread");
        bank.keep(&row.account, &thread, warm(&thread));
    }
    assert_eq!(drafting.pick(&rows), Ok(Some((accounts[2].into(), 6))));
    bank.keep(accounts[2], "t-5", warm("t-5"));
    assert_eq!(drafting.pick(&rows), Ok(None));
}

#[test]
fn a_round_drafts_one_reply_ahead_from_the_last_three_days_then_collects_what_expired() {
    let w = world(true);
    let rows = vec![mail(1, ADA, "t-lunch", "lin@example.test")];
    let (drafting, bank) = drafting(&w, &rows);
    let candidates = Candidates { rows, ..Candidates::default() };
    let tabs = Tabs::default();
    let mut breaker = Breaker::default();
    drafting.round(&mut breaker, true, &candidates, &tabs).expect("a round held back");
    assert!(candidates.since.lock().expect("since").is_empty() && w.sent().is_empty());
    drafting.round(&mut breaker, false, &candidates, &tabs).expect("a round");
    assert_eq!(*candidates.since.lock().expect("since"), [NOW - DRAFT_RECENCY_WINDOW]);
    assert_eq!(bank.body(ADA, "t-lunch").as_deref(), Some("Drafted ahead."));
    assert_eq!(*tabs.0.lock().expect("tabs"), [("draft-ready", json!({"account": ADA, "key": 10}))]);
    assert_eq!((w.sent().len(), w.features()), (1, vec!["draft-ahead".to_owned()]));
    assert_eq!(*bank.collected.lock().expect("collected"), [false, true]);
    // The next round finds it warm.
    drafting.round(&mut breaker, false, &candidates, &tabs).expect("a round");
    assert!(w.sent().is_empty());
    assert_eq!(*bank.collected.lock().expect("collected"), [false, true, true]);
}

#[test]
fn three_failed_rounds_in_a_row_stop_drafting_ahead_for_half_an_hour() {
    let w = world(true);
    let rows: Vec<Message> =
        ["t-a", "t-b", "t-c"].iter().zip(1..).map(|(t, pk)| mail(pk, ADA, t, "lin@example.test")).collect();
    let (drafting, bank) = drafting(&w, &rows);
    let candidates = Candidates { rows, ..Candidates::default() };
    let tabs = Tabs::default();
    let mut breaker = Breaker::default();
    *w.model.down.lock().expect("down") = true;
    for _ in 0..4 {
        drafting.round(&mut breaker, false, &candidates, &tabs).expect("a round");
    }
    // Each failed once, and the fourth round didn't look.
    assert_eq!(candidates.since.lock().expect("since").len(), 3);
    assert_eq!(w.sent().len(), 3);
    let failed = bank.drafts.lock().expect("drafts").values().filter(|d| d.fail_count == 1).count();
    assert_eq!(failed, 3);
    *w.model.down.lock().expect("down") = false;
    w.clock.shift(SUSPEND);
    drafting.round(&mut breaker, false, &candidates, &tabs).expect("a round");
    assert_eq!(bank.body(ADA, "t-a").as_deref(), Some("Drafted ahead."));
}

// ── written ahead ───────────────────────────────────────────────────────────────────────────────────

/// One message worth a glance, and a week with mail in it.
#[derive(Debug)]
struct Glances;

impl Overview for Glances {
    fn brief_rows(&self, _accounts: &[String], _n: i64) -> Result<Vec<Glance>, Fault> {
        Ok(vec![Glance {
            pk: 1,
            message_key: Some(10),
            folder: Some("INBOX".into()),
            from_name: Some("Lin".into()),
            subject: Some("Lunch".into()),
            message_id: Some("<m1@example.test>".into()),
            summary: Some("Lunch on Thursday.".into()),
            importance: Some(4),
            category: Some("personal".into()),
            ..Glance::default()
        }])
    }
    fn delta_rows(&self, _accounts: &[String], _since_ts: i64, _n: i64) -> Result<Vec<Glance>, Fault> {
        Ok(vec![])
    }
    fn copies_now(&self, copies: &[(i64, Option<String>)]) -> Result<Vec<CopyNow>, Fault> {
        Ok(copies.iter().map(|(key, _)| Some((Some("INBOX".to_owned()), Some(key + 100)))).collect())
    }
    fn week(&self, _accounts: &[String], _since_ts: f64, _since_iso: &str, _top: i64) -> Result<WeekRows, Fault> {
        Ok(WeekRows { sent: 2, received: 5, ..WeekRows::default() })
    }
}

/// No calendar, and a review read all week.
#[derive(Debug)]
struct NoCalendar;

impl Around for NoCalendar {
    fn events_from(&self, _at: i64, _limit: usize) -> Result<Vec<Value>, Fault> {
        Ok(vec![])
    }
    fn weekly_review(&self) -> Result<bool, Fault> {
        Ok(true)
    }
}

/// The briefs and the reviews kept, by day.
#[derive(Debug, Default)]
struct Shelf {
    briefs: Mutex<Vec<String>>,
    reviews: Mutex<Vec<String>>,
}

impl Cards for Shelf {
    fn brief(&self, _accounts: &str, day: &str) -> Result<Option<StoredCard>, Fault> {
        let kept = self.briefs.lock().expect("briefs").iter().any(|d| d == day);
        Ok(kept.then(|| StoredCard { body: "[]".into(), ..StoredCard::default() }))
    }
    fn save_brief(&self, _accounts: &str, day: &str, _brief: &StoredCard, _forget: &str) -> Result<(), Fault> {
        self.briefs.lock().expect("briefs").push(day.into());
        Ok(())
    }
    fn delta(&self, _accounts: &str, _since_ts: i64, _fingerprint: &str) -> Result<Option<StoredCard>, Fault> {
        Ok(None)
    }
    fn save_delta(&self, _accounts: &str, _since_ts: i64, _delta: &StoredCard, _forget: &str) -> Result<(), Fault> {
        Ok(())
    }
    fn review(&self, _accounts: &str, _day: &str) -> Result<Option<StoredReview>, Fault> {
        Ok(None)
    }
    fn save_review(&self, _accounts: &str, day: &str, _review: &StoredReview, _forget: &str) -> Result<(), Fault> {
        self.reviews.lock().expect("reviews").push(day.into());
        Ok(())
    }
}

fn ahead(w: &World, queue: &Arc<Queue>) -> (Ahead, Arc<Shelf>) {
    let shelf = Arc::new(Shelf::default());
    let clock: Arc<dyn Clock> = w.clock.clone();
    let briefs = Arc::new(Briefs::new(w.calls.clone(), shelf.clone(), Arc::new(Glances), clock.clone(), Arc::new(Utc)));
    let reviews = Arc::new(Reviews::new(
        w.calls.clone(),
        shelf.clone(),
        Arc::new(Glances),
        Arc::new(NoCalendar),
        clock.clone(),
        Arc::new(Utc),
    ));
    let asking = Arc::new(Asking::new(briefs, reviews, Arc::new(Both), Arc::new(NoCalendar), clock.clone()));
    (Ahead::new(asking, Arc::new(Both), queue.clone(), clock, Arc::new(Utc)), shelf)
}

#[test]
fn the_morning_is_five_until_noon_and_the_reviews_is_mondays() {
    assert_eq!([4, 5, 11, 12].map(is_morning), [false, true, true, false]);
    let monday = MONDAY / 86_400;
    assert_eq!(
        [(monday, 8), (monday, 12), (monday + 1, 8), (monday + 7, 5)].map(|(d, h)| is_ahead_morning(d, h)),
        [true, false, false, true]
    );
}

#[test]
fn the_brief_is_written_ahead_in_the_morning_once_triage_has_read_the_last_day() {
    let w = world(true);
    let queue = Arc::new(Queue::default());
    let (ahead, shelf) = ahead(&w, &queue);
    w.clock.at(MONDAY + 4 * 3600);
    ahead.write_brief_ahead().expect("too early");
    w.clock.at(MONDAY + 7 * 3600);
    *queue.rows.lock().expect("rows") = vec![pending(1, "Lunch", MONDAY + 6 * 3600)];
    ahead.write_brief_ahead().expect("triage behind");
    assert!(w.sent().is_empty() && shelf.briefs.lock().expect("briefs").is_empty());
    assert_eq!(queue.asked.lock().expect("asked").last(), Some(&(1, Some(MONDAY + 7 * 3600 - 86_400))));
    // Mail older than a day still waiting doesn't hold it back.
    *queue.rows.lock().expect("rows") = vec![pending(1, "Lunch", MONDAY - 2 * 86_400)];
    ahead.write_brief_ahead().expect("written");
    assert_eq!(w.sent().len(), 1);
    assert_eq!(
        (w.features(), shelf.briefs.lock().expect("briefs").clone()),
        (vec!["brief-ahead".into()], vec!["2026-11-02".into()])
    );
    ahead.write_brief_ahead().expect("already written");
    w.clock.at(MONDAY + 12 * 3600);
    *shelf.briefs.lock().expect("briefs") = vec![];
    ahead.write_brief_ahead().expect("too late");
    assert!(w.sent().is_empty() && shelf.briefs.lock().expect("briefs").is_empty());
}

#[test]
fn the_review_is_written_ahead_on_monday_morning_only() {
    let w = world(true);
    let (ahead, shelf) = ahead(&w, &Arc::new(Queue::default()));
    for at in [MONDAY + 86_400 + 8 * 3600, MONDAY + 13 * 3600, MONDAY + 3 * 3600] {
        w.clock.at(at);
        ahead.write_review_ahead().expect("not now");
    }
    assert!(w.sent().is_empty());
    w.clock.at(MONDAY + 8 * 3600);
    ahead.write_review_ahead().expect("written");
    assert_eq!(w.sent().len(), 1);
    assert_eq!(
        (w.features(), shelf.reviews.lock().expect("reviews").clone()),
        (vec!["review-ahead".into()], vec!["2026-11-02".into()])
    );
}

#[test]
fn with_ai_off_nothing_is_written_ahead_and_the_round_goes_on() {
    let w = world(false);
    let (ahead, shelf) = ahead(&w, &Arc::new(Queue::default()));
    w.clock.at(MONDAY + 8 * 3600);
    ahead.write_brief_ahead().expect("AI off");
    ahead.write_review_ahead().expect("AI off");
    assert!(w.sent().is_empty());
    assert!(shelf.briefs.lock().expect("briefs").is_empty() && shelf.reviews.lock().expect("reviews").is_empty());
}

//! A backlog of triage through the Message Batches API (`tests/unit/assistant/test_ai_batches.py`): what a batch costs
//! and how that counts against the daily limit, and the job that sends one and brings its answers in, on mail and a
//! model of its own.

use std::collections::{BTreeMap, HashMap, HashSet};
use std::sync::atomic::{AtomicI64, Ordering};
use std::sync::{Arc, Mutex, MutexGuard};

use mm_assistant::assessments::{Assessments, triage_prompt};
use mm_assistant::background_ports::{Assessment, Batch, BatchBook, Begun, Judge, Outcome, Pending, Triage};
use mm_assistant::batches::{self, Batching};
use mm_assistant::calls::Calls;
use mm_assistant::gateway::{Budget, Gateway};
use mm_assistant::governor::CostGovernor;
use mm_assistant::models::DEFAULT;
use mm_assistant::ports::{
    AiSettings, BatchResult, Engine, InForce, LanguageModel, ModelError, Prompt, Reply, Spend, Usage, UsageLedger,
};
use mm_assistant::pricing::{batch_estimate, batch_micros, cost_micros};
use mm_assistant::prompts::SYSTEM;
use mm_assistant::status::Status;
use mm_kernel::errors::Fault;
use mm_kernel::seams::{Clock, Moment};
use mm_kernel::zone::Utc;
use serde_json::{Value, json};

/// Monday 2 November 2026, noon UTC.
const NOW: i64 = 1_793_620_800;

fn lock<T>(m: &Mutex<T>) -> MutexGuard<'_, T> {
    m.lock().unwrap_or_else(std::sync::PoisonError::into_inner)
}

#[derive(Debug)]
struct Frozen(AtomicI64);

impl Clock for Frozen {
    fn now(&self) -> Moment {
        Moment::from_micros(self.0.load(Ordering::SeqCst) * 1_000_000)
    }
}

impl Frozen {
    fn shift(&self, seconds: i64) {
        self.0.fetch_add(seconds, Ordering::SeqCst);
    }
}

fn usage(input_tokens: i64, output_tokens: i64) -> Usage {
    Usage { input_tokens, output_tokens, ..Usage::default() }
}

fn reply(text: &str, stop: &str, usage: Usage) -> Reply {
    Reply { text: text.into(), model: DEFAULT.into(), usage, stop_reason: stop.into() }
}

/// A batch the model holds: its requests, and whether it has ended.
#[derive(Debug, Default)]
struct Held {
    requests: Vec<(String, Prompt)>,
    ended: bool,
}

/// The Claude API as triage meets it: each message answered at once, and batches held until a test ends them, each
/// request then answered as `results` says, or with a summary. It keeps what it was asked.
#[derive(Debug, Default)]
struct Model {
    asked: Mutex<Vec<&'static str>>,
    sent: Mutex<Vec<Prompt>>,
    batches: Mutex<BTreeMap<String, Held>>,
    results: Mutex<HashMap<String, BatchResult>>,
    discarded: Mutex<Vec<String>>,
    failing: Mutex<HashMap<&'static str, ModelError>>,
}

impl Model {
    fn ask(&self, what: &'static str) -> Result<(), ModelError> {
        lock(&self.asked).push(what);
        lock(&self.failing).get(what).map_or(Ok(()), |error| Err(error.clone()))
    }

    fn end_batch(&self) {
        for held in lock(&self.batches).values_mut() {
            held.ended = true;
        }
    }

    fn asked(&self) -> Vec<&'static str> {
        lock(&self.asked).clone()
    }

    /// The custom ids of every request batched, as keys.
    fn batched(&self) -> Vec<i64> {
        lock(&self.batches).values().flat_map(|h| h.requests.iter().map(|(id, _)| id.parse().expect("a key"))).collect()
    }
}

fn gone() -> ModelError {
    ModelError::new("Claude API 404: no such batch", Some(404))
}

impl LanguageModel for Model {
    fn complete(&self, prompt: &Prompt) -> Result<Reply, ModelError> {
        lock(&self.sent).push(prompt.clone());
        Ok(reply(
            "{\"summary\": \"Said at once.\", \"importance\": 3, \"category\": \"info\"}",
            "end_turn",
            usage(0, 0),
        ))
    }

    fn submit_batch(&self, requests: &[(String, Prompt)]) -> Result<String, ModelError> {
        self.ask("submit_batch")?;
        let mut batches = lock(&self.batches);
        let id = format!("msgbatch_{}", batches.len() + lock(&self.discarded).len() + 1);
        batches.insert(id.clone(), Held { requests: requests.to_vec(), ended: false });
        Ok(id)
    }

    fn batch_ended(&self, batch_id: &str) -> Result<bool, ModelError> {
        self.ask("batch_ended")?;
        lock(&self.batches).get(batch_id).map(|h| h.ended).ok_or_else(gone)
    }

    fn batch_results(&self, batch_id: &str, _asked: &str) -> Result<Vec<BatchResult>, ModelError> {
        self.ask("batch_results")?;
        let batches = lock(&self.batches);
        let held = batches.get(batch_id).ok_or_else(gone)?;
        let results = lock(&self.results);
        Ok(held
            .requests
            .iter()
            .map(|(id, _)| {
                results.get(id).cloned().unwrap_or_else(|| BatchResult {
                    custom_id: id.clone(),
                    reply: Some(reply("{\"summary\": \"Batched.\", \"importance\": 9}", "end_turn", usage(1000, 100))),
                    permanent: false,
                })
            })
            .collect())
    }

    fn discard_batch(&self, batch_id: &str) -> Result<(), ModelError> {
        self.ask("discard_batch")?;
        lock(&self.batches).remove(batch_id).ok_or_else(gone)?;
        lock(&self.discarded).push(batch_id.to_owned());
        Ok(())
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

/// The mail waiting for triage, what it said of each, and the batches, as the database keeps them.
#[derive(Debug, Default)]
struct Book {
    rows: Mutex<Vec<Pending>>,
    said: Mutex<HashMap<i64, Assessment>>,
    batches: Mutex<BTreeMap<i64, Batch>>,
    items: Mutex<HashMap<i64, i64>>,
}

const OUT: [&str; 2] = ["submitting", "running"];

impl Book {
    fn batch_mut(&self, id: i64, change: impl FnOnce(&mut Batch)) {
        if let Some(batch) = lock(&self.batches).get_mut(&id) {
            change(batch);
        }
    }

    fn states(&self) -> Vec<String> {
        lock(&self.batches).values().map(|b| b.state.clone()).collect()
    }
}

impl Triage for Book {
    fn batch_cutoff(&self) -> Result<Option<i64>, Fault> {
        Ok(self.open()?.map(|b| b.cutoff_ts))
    }

    fn needs_summary(&self, limit: i64, newer_than: Option<i64>) -> Result<Vec<Pending>, Fault> {
        let (said, items) = (lock(&self.said), lock(&self.items));
        let mut rows: Vec<Pending> = lock(&self.rows)
            .iter()
            .filter(|r| r.message_key.is_some_and(|k| !said.contains_key(&k) && !items.contains_key(&k)))
            .filter(|r| r.ts.unwrap_or(0) > newer_than.unwrap_or(-1))
            .cloned()
            .collect();
        rows.sort_by_key(|r| std::cmp::Reverse(r.ts));
        rows.truncate(usize::try_from(limit).unwrap_or(usize::MAX));
        Ok(rows)
    }

    fn save_summary(&self, pk: i64, said: &Assessment, _when: &str) -> Result<(), Fault> {
        let key = lock(&self.rows).iter().find(|r| r.pk == pk).and_then(|r| r.message_key);
        if let Some(key) = key {
            lock(&self.said).insert(key, said.clone());
        }
        Ok(())
    }
}

impl BatchBook for Book {
    fn open(&self) -> Result<Option<Batch>, Fault> {
        Ok(lock(&self.batches).values().rev().find(|b| OUT.contains(&b.state.as_str())).cloned())
    }

    fn in_state(&self, state: &str) -> Result<Vec<Batch>, Fault> {
        Ok(lock(&self.batches).values().filter(|b| b.state == state).cloned().collect())
    }

    fn begin(&self, begun: &Begun<'_>) -> Result<Option<i64>, Fault> {
        if self.open()?.is_some() {
            return Ok(None);
        }
        let mut batches = lock(&self.batches);
        let id = batches.keys().last().copied().unwrap_or(0) + 1;
        batches.insert(
            id,
            Batch {
                id,
                remote_id: None,
                feature: begun.feature.into(),
                model: begun.model.into(),
                state: "submitting".into(),
                requests: i64::try_from(begun.keys.len()).unwrap_or(0),
                estimate_micros: begun.estimate_micros,
                cutoff_ts: begun.cutoff_ts,
                created_at: begun.now,
                polled_at: 0,
                ended_at: None,
                prompt_version: Some(i64::from(begun.prompt_version)),
            },
        );
        lock(&self.items).extend(begun.keys.iter().map(|k| (*k, id)));
        Ok(Some(id))
    }

    fn sent(&self, id: i64, remote_id: &str, now: i64) -> Result<(), Fault> {
        self.batch_mut(id, |b| (b.state, b.remote_id, b.polled_at) = ("running".into(), Some(remote_id.into()), now));
        Ok(())
    }

    fn drop_batch(&self, id: i64) -> Result<(), Fault> {
        lock(&self.batches).remove(&id);
        lock(&self.items).retain(|_, batch| *batch != id);
        Ok(())
    }

    fn polled(&self, id: i64, now: i64) -> Result<(), Fault> {
        self.batch_mut(id, |b| b.polled_at = now);
        Ok(())
    }

    fn collected(
        &self,
        id: i64,
        now: i64,
        _when: &str,
        answers: &[(i64, Reply)],
        judge: Judge<'_>,
    ) -> Result<usize, Fault> {
        let held: HashSet<i64> = lock(&self.items).iter().filter(|(_, b)| **b == id).map(|(k, _)| *k).collect();
        let mut stored = 0;
        for (key, reply) in answers.iter().filter(|(key, _)| held.contains(key)) {
            let copy = lock(&self.rows).iter().find(|r| r.message_key == Some(*key)).cloned();
            let said = judge(copy.as_ref(), reply);
            if copy.is_some() || said.outcome == Some(Outcome::Assessed) {
                lock(&self.said).insert(*key, said);
            }
            stored += 1;
        }
        lock(&self.items).retain(|_, batch| *batch != id);
        self.batch_mut(id, |b| (b.state, b.ended_at) = ("collected".into(), Some(now)));
        Ok(stored)
    }

    fn discarded(&self, id: i64) -> Result<(), Fault> {
        self.batch_mut(id, |b| b.state = "done".into());
        Ok(())
    }

    fn waiting(&self) -> Result<i64, Fault> {
        Ok(i64::try_from(lock(&self.items).len()).unwrap_or(0))
    }
}

/// What calls cost, and what batches out book, as the book says.
#[derive(Debug)]
struct Ledger {
    spends: Mutex<Vec<Spend>>,
    spent: AtomicI64,
    book: Arc<Book>,
}

impl UsageLedger for Ledger {
    fn record(&self, spend: &Spend) -> Result<(), Fault> {
        lock(&self.spends).push(spend.clone());
        Ok(())
    }
    fn spent(&self, _day: &str) -> Result<i64, Fault> {
        Ok(self.spent.load(Ordering::SeqCst) + lock(&self.spends).iter().map(|s| s.cost_micros).sum::<i64>())
    }
    fn reserved(&self) -> Result<i64, Fault> {
        Ok(self.book.open()?.map_or(0, |b| b.estimate_micros))
    }
    fn in_batches(&self) -> Result<i64, Fault> {
        self.book.waiting()
    }
}

#[derive(Debug)]
struct Limit(Option<Value>);

impl AiSettings for Limit {
    fn key_named(&self) -> Result<bool, Fault> {
        Ok(true)
    }
    fn daily_limit(&self) -> Result<Option<Value>, Fault> {
        Ok(self.0.clone())
    }
}

/// The n-th message waiting: newer the higher `n`.
fn message(n: i64) -> Pending {
    Pending {
        pk: n,
        message_key: Some(n),
        from_name: Some("Pat Lee".into()),
        from_addr: Some("pat@example.test".into()),
        subject: Some(format!("Note {n}")),
        date_iso: None,
        snippet: Some("On the quarterly figures.".into()),
        ts: Some(n * 100),
    }
}

struct World {
    model: Arc<Model>,
    book: Arc<Book>,
    ledger: Arc<Ledger>,
    clock: Arc<Frozen>,
    governor: Arc<CostGovernor>,
    calls: Arc<Calls>,
    batching: Batching,
    status: Arc<Status>,
}

/// `messages` waiting, with AI `on` or off and a daily limit of `limit_micros` (the default without one).
fn world(messages: i64, on: bool, limit_micros: Option<i64>) -> World {
    let model = Arc::new(Model::default());
    let book = Arc::new(Book::default());
    lock(&book.rows).extend((1..=messages).map(message));
    let ledger = Arc::new(Ledger { spends: Mutex::default(), spent: AtomicI64::new(0), book: book.clone() });
    let clock = Arc::new(Frozen(AtomicI64::new(NOW)));
    let limit = limit_micros.map(|micros| json!(micros as f64 / 1_000_000.0));
    let budget = Arc::new(Budget {
        ledger: ledger.clone(),
        settings: Arc::new(Limit(limit)),
        clock: clock.clone(),
        zone: Arc::new(Utc),
    });
    let governor = Arc::new(CostGovernor::new(budget.clone(), clock.clone()));
    let lm: Arc<dyn LanguageModel> = model.clone();
    let gateway = Arc::new(Gateway::new(Arc::new(Keyed(on.then_some(lm))), budget, governor.clone()));
    let status = Arc::new(Status::new(gateway.clone(), clock.clone(), DEFAULT.into()));
    let calls = Arc::new(Calls::new(gateway, status.clone(), None, false));
    let batching = Batching::new(calls.clone(), book.clone(), book.clone(), clock.clone());
    World { model, book, ledger, clock, governor, calls, batching, status }
}

impl World {
    fn waiting(&self) -> Vec<i64> {
        self.book.needs_summary(10_000, None).expect("waiting").into_iter().filter_map(|r| r.message_key).collect()
    }

    fn submit(&self) -> usize {
        self.batching.submit(batches::MIN, batches::KEEP_NEWEST).expect("a round")
    }

    fn collect(&self) -> usize {
        self.batching.collect().expect("a round")
    }

    fn assessments(&self, send: bool) -> Assessments {
        let batching =
            Arc::new(Batching::new(self.calls.clone(), self.book.clone(), self.book.clone(), self.clock.clone()));
        Assessments::new(self.calls.clone(), self.book.clone(), self.clock.clone()).with_batches(batching, send)
    }

    /// The ledger's rows for `feature`: (served, outcome, calls, cost).
    fn counted(&self, feature: &str) -> Vec<(String, &'static str, i64, i64)> {
        lock(&self.ledger.spends)
            .iter()
            .filter(|s| s.feature == feature)
            .map(|s| (s.served.clone(), s.outcome, s.calls, s.cost_micros))
            .collect()
    }

    /// What each of `rows` books through a batch.
    fn estimates(&self, rows: &[Pending]) -> Vec<i64> {
        rows.iter()
            .map(|r| {
                let chars = SYSTEM.chars().count() + triage_prompt(r).chars().count();
                batch_estimate(DEFAULT, i64::try_from(chars).expect("a count"))
            })
            .collect()
    }
}

// -- what it costs ---------------------------------------------------------------

#[test]
fn half_a_micro_dollar_rounds_up_and_an_estimate_errs_high() {
    assert_eq!((batch_micros(3), batch_micros(4), batch_micros(0)), (2, 2, 0));
    // About 750 tokens of mail and an answer of about 100.
    assert!(batch_estimate(DEFAULT, 3000) > batch_micros(cost_micros(DEFAULT, DEFAULT, 750, 100, 0, 0)));
}

#[test]
fn a_batch_carries_each_request_without_the_fallback_and_costs_half_what_the_same_calls_do() {
    let w = world(0, true, None);
    let gateway = w.calls.gateway();
    let id = gateway
        .submit_batch(&[("1".into(), "x".into()), ("2".into(), "y".into())], None, "triage-batch", None, Some("low"))
        .expect("sent");
    {
        let held = lock(&w.model.batches);
        let requests = &held[&id].requests;
        assert_eq!(requests.iter().map(|(id, _)| id.as_str()).collect::<Vec<_>>(), ["1", "2"]);
        let first = &requests[0].1;
        assert_eq!((first.fallback, first.effort.as_deref(), first.system), (false, Some("low"), Some(SYSTEM)));
    }
    w.model.end_batch();
    gateway.batch_results(&id, "triage-batch", None).expect("its answers");
    let full = cost_micros(DEFAULT, DEFAULT, 1000, 100, 0, 0);
    assert_eq!(w.counted("triage-batch"), [(DEFAULT.to_owned(), "ok", 2, 2 * batch_micros(full))]);
    let spend = lock(&w.ledger.spends)[0].clone();
    assert_eq!((spend.day.as_str(), spend.ts, spend.usage), ("2026-11-02", NOW, usage(2000, 200)));
}

#[test]
fn a_request_with_no_answer_costs_nothing_but_is_counted_and_a_failed_send_is_a_failed_call() {
    let w = world(0, true, None);
    let gateway = w.calls.gateway();
    let id =
        gateway.submit_batch(&[("1".into(), "x".into()), ("2".into(), "y".into())], None, "triage-batch", None, None);
    let id = id.expect("sent");
    lock(&w.model.results).extend([
        ("1".to_owned(), BatchResult { custom_id: "1".into(), reply: None, permanent: false }),
        ("2".to_owned(), BatchResult { custom_id: "2".into(), reply: None, permanent: true }),
    ]);
    w.model.end_batch();
    gateway.batch_results(&id, "triage-batch", None).expect("its answers");
    assert_eq!(w.counted("triage-batch"), [(String::new(), "failed", 1, 0), (String::new(), "rejected", 1, 0)]);

    lock(&w.model.failing).insert("submit_batch", ModelError::new("Claude API 500: down", Some(500)));
    assert!(gateway.submit_batch(&[("3".into(), "z".into())], None, "triage-batch", None, None).is_err());
    assert_eq!(w.counted("triage-batch").last(), Some(&(String::new(), "failed", 1, 0)));
}

#[test]
fn requests_a_batch_keeps_rejecting_trip_the_breaker_for_batches_alone() {
    let w = world(0, true, None);
    let gateway = w.calls.gateway();
    let requests: Vec<(String, String)> = (0..3).map(|i| (i.to_string(), "x".to_owned())).collect();
    let id = gateway.submit_batch(&requests, None, "triage-batch", None, None).expect("sent");
    lock(&w.model.results).extend(
        (0..3).map(|i| (i.to_string(), BatchResult { custom_id: i.to_string(), reply: None, permanent: true })),
    );
    w.model.end_batch();
    gateway.batch_results(&id, "triage-batch", None).expect("its answers");
    assert_eq!(gateway.held_back("triage-batch", None), Some("breaker"));
    assert_eq!(gateway.held_back("triage", None), None, "the ordinary path goes on");
}

// -- the job ---------------------------------------------------------------------

#[test]
fn a_backlog_goes_as_a_batch_and_the_newest_stay() {
    let w = world(120, true, None);
    let before = w.waiting();
    assert_eq!(w.submit(), 100);
    assert_eq!(w.model.batched(), before[20..]);
    assert_eq!(w.waiting(), before[..20]);
    let [out] = w.book.in_state("running").expect("out").try_into().expect("one");
    assert_eq!((out.remote_id.as_deref(), out.cutoff_ts, out.polled_at), (Some("msgbatch_1"), 100 * 100, NOW));
    assert_eq!((out.feature.as_str(), out.model.as_str(), out.prompt_version), ("triage-batch", DEFAULT, Some(2)));
    let [prompt] = lock(&w.model.batches).values().next().map(|h| [h.requests[0].1.clone()]).expect("a request");
    assert_eq!((prompt.text, prompt.effort.as_deref()), (triage_prompt(&message(100)), Some("low")));
    assert!(prompt.schema.is_some() && !prompt.fallback);
}

#[test]
fn a_few_new_messages_are_no_backlog_and_one_batch_goes_at_a_time() {
    let w = world(99, true, None);
    assert_eq!(w.submit(), 0);
    assert!(w.model.asked().is_empty());
    lock(&w.book.rows).extend((100..=130).map(message));
    assert!(w.submit() > 0);
    assert_eq!(w.submit(), 0);
    assert_eq!(w.model.asked(), ["submit_batch"]);
}

#[test]
fn a_batch_is_no_bigger_than_its_maximum() {
    // A limit whose share holds more than the most a batch takes.
    let w = world(2100, true, Some(1_000_000_000));
    assert_eq!(w.submit(), batches::MAX);
}

#[test]
fn what_a_batch_books_counts_the_system_prompt_each_request_carries() {
    let w = world(30, true, None);
    let rows = w.book.needs_summary(1000, None).expect("rows");
    assert_eq!(w.batching.submit(1, 2).expect("a round"), 28);
    let status = w.batching.status().expect("its status");
    let booked: i64 = w.estimates(&rows[2..]).iter().sum();
    assert_eq!(status, batches::BatchStatus { waiting: 28, state: Some("running".into()), estimate_micros: booked });
    assert_eq!(w.ledger.reserved(), Ok(booked));
    assert_eq!(w.status.report().expect("a report")["batch_waiting"], json!(28));
}

#[test]
fn a_batch_takes_only_its_share_of_what_the_limit_leaves() {
    let probe = world(120, true, None);
    let rows = probe.book.needs_summary(1000, None).expect("rows");
    let each = probe.estimates(&rows[20..]);
    let w = world(120, true, Some((each[..3].iter().sum::<i64>() + each[3] / 2) * batches::SHARE));
    assert_eq!(w.submit(), 3);
}

#[test]
fn at_the_limit_or_with_no_room_for_one_request_no_batch_goes() {
    let w = world(120, true, Some(15_000_000));
    w.ledger.spent.store(15_000_000, Ordering::SeqCst);
    assert_eq!(w.submit(), 0);
    let w = world(120, true, Some(1));
    assert_eq!(w.submit(), 0);
    assert!(w.model.asked().is_empty());
}

#[test]
fn an_answer_for_nothing_of_ours_is_ignored_and_nothing_comes_in_before_the_batch_ends() {
    let w = world(120, true, None);
    let ours = w.submit();
    w.clock.shift(batches::POLL);
    assert_eq!(w.collect(), 0);
    assert_eq!((w.batching.status().expect("status").waiting, lock(&w.model.discarded).len()), (100, 0));
    let prompt = lock(&w.model.batches).values().next().expect("a batch").requests[0].1.clone();
    for stray in ["not-a-key", "999999", "-5", ""] {
        lock(&w.model.batches).values_mut().for_each(|h| h.requests.push((stray.into(), prompt.clone())));
    }
    w.model.end_batch();
    w.clock.shift(batches::POLL);
    assert_eq!(w.collect(), ours);
    let said = lock(&w.book.said).get(&100).cloned().expect("an answer");
    assert_eq!((said.summary.as_str(), said.importance, said.outcome), ("Batched.", 5, Some(Outcome::Assessed)));
    assert_eq!((said.model.as_str(), said.prompt_version), (DEFAULT, Some(2)));
}

#[test]
fn it_asks_only_so_often_and_the_batch_is_removed_once_its_answers_are_stored() {
    let w = world(120, true, None);
    assert!(w.submit() > 0);
    lock(&w.model.asked).clear();
    w.clock.shift(batches::POLL - 1);
    w.collect();
    assert!(w.model.asked().is_empty());
    w.clock.shift(1);
    w.collect();
    assert_eq!(w.model.asked(), ["batch_ended"]);
    w.collect();
    assert_eq!(w.model.asked(), ["batch_ended"]);
    w.model.end_batch();
    w.clock.shift(batches::POLL);
    assert_eq!(w.collect(), 100);
    assert_eq!(w.model.asked(), ["batch_ended", "batch_ended", "batch_results", "discard_batch"]);
    assert_eq!(
        (lock(&w.model.discarded).clone(), w.book.states()),
        (vec!["msgbatch_1".to_owned()], vec!["done".into()])
    );
    assert_eq!(w.book.in_state("done").expect("done")[0].ended_at, Some(NOW + 2 * batches::POLL));
}

#[test]
fn removing_it_is_tried_again_when_it_fails() {
    let w = world(120, true, None);
    assert!(w.submit() > 0);
    w.model.end_batch();
    lock(&w.model.failing).insert("discard_batch", ModelError::new("Claude API 400: no", Some(400)));
    w.clock.shift(batches::POLL);
    assert_eq!(w.collect(), 100);
    assert_eq!(w.book.states(), ["collected"]);
    lock(&w.model.failing).clear();
    assert_eq!(w.collect(), 0, "nothing is stored twice");
    assert_eq!((lock(&w.model.discarded).len(), w.book.states()), (1, vec!["done".to_owned()]));
}

#[test]
fn a_batch_the_api_no_longer_has_gives_its_messages_back_and_an_outage_leaves_it_out() {
    let w = world(120, true, None);
    assert!(w.submit() > 0);
    lock(&w.model.failing).insert("batch_ended", ModelError::new("Claude API 503: busy", Some(503)));
    w.clock.shift(batches::POLL);
    assert_eq!(w.collect(), 0);
    assert_eq!(w.batching.status().expect("status").state.as_deref(), Some("running"));
    assert_eq!(w.book.open().expect("out").map(|b| b.polled_at), Some(NOW + batches::POLL));
    lock(&w.model.failing).clear();
    lock(&w.model.batches).clear();
    w.clock.shift(batches::POLL);
    w.collect();
    assert_eq!((w.book.states(), w.batching.status().expect("status").waiting), (vec![], 0));
    assert_eq!(w.waiting().len(), 120);
}

#[test]
fn a_crash_while_sending_gives_its_messages_back_in_time() {
    let w = world(120, true, None);
    let lately = NOW - batches::SEND_GIVE_UP + 60;
    let keys = [1, 2];
    w.book.begin(&Begun { keys: &keys, now: lately, ..begun() }).expect("begun");
    w.collect();
    assert_eq!(w.batching.status().expect("status").waiting, 2, "it may still be going");
    assert_eq!(w.submit(), 0);
    w.clock.shift(61);
    w.collect();
    assert_eq!(w.batching.status().expect("status").waiting, 0);
    assert!(w.book.states().is_empty());
}

fn begun() -> Begun<'static> {
    Begun {
        feature: "triage-batch",
        model: DEFAULT,
        keys: &[],
        estimate_micros: 10,
        cutoff_ts: 0,
        now: NOW,
        prompt_version: 2,
    }
}

#[test]
fn a_batch_that_wasnt_sent_gives_its_messages_back_and_the_next_round_tries_again() {
    let w = world(120, true, None);
    lock(&w.model.failing).insert("submit_batch", ModelError::new("Claude API 500: down", Some(500)));
    assert_eq!(w.submit(), 0);
    assert!(w.book.states().is_empty());
    assert_eq!(w.waiting().len(), 120);
    lock(&w.model.failing).clear();
    assert_eq!(w.submit(), 100);
}

// -- within the daily limit, and the round ----------------------------------------

#[test]
fn answers_are_brought_in_at_the_limit_and_while_someone_uses_ai() {
    for held in ["yielding", "limit"] {
        let w = world(120, true, Some(15_000_000));
        let round = w.assessments(true);
        assert!(w.submit() > 0);
        w.model.end_batch();
        w.clock.shift(batches::POLL);
        if held == "yielding" {
            w.calls.call_claude("Asked for.", "thread").expect("someone asked just now");
        } else {
            w.ledger.spent.store(15_000_000, Ordering::SeqCst);
        }
        let sent_before = lock(&w.model.sent).len();
        let next = round.round().expect("a round");
        assert_eq!(next, (held == "yielding").then_some(45), "{held}");
        assert_eq!(w.batching.status().expect("status").waiting, 0, "{held}");
        assert_eq!((lock(&w.model.discarded).len(), lock(&w.model.sent).len()), (1, sent_before), "{held}");
    }
}

#[test]
fn the_round_sends_the_backlog_and_triages_the_newest_at_once() {
    let w = world(120, true, None);
    let before = w.waiting();
    w.assessments(true).round().expect("a round");
    assert_eq!(w.model.asked(), ["submit_batch"]);
    let triaged: HashSet<String> = lock(&w.model.sent).iter().map(|p| p.text.clone()).collect();
    let newest: HashSet<String> = before[..20].iter().map(|k| triage_prompt(&message(*k))).collect();
    assert_eq!(triaged, newest);
    assert!(w.waiting().is_empty());
    assert_eq!(w.model.batched().len(), 100);
}

#[test]
fn the_switch_keeps_the_round_from_sending_one_and_still_brings_one_in() {
    let w = world(120, true, None);
    assert!(w.submit() > 0);
    w.model.end_batch();
    lock(&w.book.rows).extend((200..=330).map(message));
    w.clock.shift(batches::POLL);
    w.assessments(false).round().expect("a round");
    assert_eq!(lock(&w.model.discarded).len(), 1, "the one that was out came in");
    assert_eq!(w.model.asked().iter().filter(|a| **a == "submit_batch").count(), 1, "and no other went");
}

#[test]
fn with_ai_off_nothing_goes_and_nothing_is_touched() {
    let w = world(120, false, None);
    assert_eq!(w.batching.submit(1, 0), Ok(0));
    assert!(w.book.states().is_empty());
    let id = w.book.begin(&Begun { keys: &[1, 2], ..begun() }).expect("begun").expect("an id");
    w.book.sent(id, "msgbatch_x", 0).expect("sent");
    w.clock.shift(batches::POLL);
    assert_eq!(w.collect(), 0);
    assert!(w.model.asked().is_empty() && lock(&w.model.sent).is_empty());
    assert_eq!(w.batching.status().expect("status").waiting, 2);
}

#[test]
fn while_someone_uses_ai_no_batch_goes_and_the_backlog_still_waits() {
    let w = world(120, true, None);
    let before = w.waiting();
    w.calls.call_claude("Asked for.", "thread").expect("someone asked just now");
    lock(&w.model.sent).clear();
    assert_eq!(w.submit(), 0);
    assert!(w.model.asked().is_empty());
    assert_eq!((w.waiting(), w.batching.status().expect("status").waiting), (before, 0));
    w.governor.forget();
    assert_eq!(w.submit(), 100);
}

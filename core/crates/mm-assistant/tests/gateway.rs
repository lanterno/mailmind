//! The gateway and the cost governor (`tests/unit/assistant/test_governor.py` and `test_ai_ledger.py`'s): what a call
//! sends, what the ledger counts, the daily limit, yielding, presence and the breaker, and what the status says.

use std::sync::atomic::{AtomicI64, Ordering};
use std::sync::{Arc, Mutex};

use mm_assistant::calls::Calls;
use mm_assistant::gateway::{Budget, Gateway};
use mm_assistant::governor::{CostGovernor, PRESENCE_SECONDS, allowing_over};
use mm_assistant::ports::{
    AiSettings, Engine, InForce, LanguageModel, ModelError, Prompt, Reply, Spend, Usage, UsageLedger,
};
use mm_assistant::refusals::{CallError, NotSent};
use mm_assistant::status::Status;
use mm_kernel::errors::Fault;
use mm_kernel::seams::{Clock, Moment};
use mm_kernel::zone::Utc;
use serde_json::{Value, json};

#[derive(Debug)]
struct Frozen(AtomicI64);

impl Clock for Frozen {
    fn now(&self) -> Moment {
        Moment::from_micros(self.0.load(Ordering::SeqCst))
    }
}

impl Frozen {
    fn shift(&self, seconds: i64) {
        self.0.fetch_add(seconds * 1_000_000, Ordering::SeqCst);
    }
}

/// Answers each prompt with the next of its replies, or "ok", and keeps the prompts.
#[derive(Debug, Default)]
struct Model {
    replies: Mutex<Vec<Result<Reply, ModelError>>>,
    prompts: Mutex<Vec<Prompt>>,
}

impl LanguageModel for Model {
    fn complete(&self, prompt: &Prompt) -> Result<Reply, ModelError> {
        self.prompts.lock().expect("prompts").push(prompt.clone());
        let mut replies = self.replies.lock().expect("replies");
        if replies.is_empty() {
            return Ok(Reply {
                text: "{\"text\": \"ok\"}".into(),
                model: prompt.model.clone(),
                usage: Usage { input_tokens: 1000, output_tokens: 100, ..Usage::default() },
                stop_reason: "end_turn".into(),
            });
        }
        replies.remove(0)
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

#[derive(Debug, Default)]
struct Ledger(Mutex<Vec<Spend>>, AtomicI64);

impl UsageLedger for Ledger {
    fn record(&self, spend: &Spend) -> Result<(), Fault> {
        self.0.lock().expect("ledger").push(spend.clone());
        Ok(())
    }
    fn spent(&self, day: &str) -> Result<i64, Fault> {
        let recorded: i64 = self.0.lock().expect("ledger").iter().filter(|s| s.day == day).map(|s| s.cost_micros).sum();
        Ok(recorded + self.1.load(Ordering::SeqCst))
    }
    fn reserved(&self) -> Result<i64, Fault> {
        Ok(0)
    }
    fn in_batches(&self) -> Result<i64, Fault> {
        Ok(0)
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

struct World {
    model: Arc<Model>,
    ledger: Arc<Ledger>,
    clock: Arc<Frozen>,
    governor: Arc<CostGovernor>,
    calls: Calls,
    status: Arc<Status>,
}

/// Monday 2 November 2026, noon UTC.
const NOW: i64 = 1_793_620_800;

fn world(on: bool, limit: Option<Value>) -> World {
    let model = Arc::new(Model::default());
    let ledger = Arc::new(Ledger::default());
    let clock = Arc::new(Frozen(AtomicI64::new(NOW * 1_000_000)));
    let budget = Arc::new(Budget {
        ledger: ledger.clone(),
        settings: Arc::new(Limit(limit)),
        clock: clock.clone(),
        zone: Arc::new(Utc),
    });
    let governor = Arc::new(CostGovernor::new(budget.clone(), clock.clone()));
    let lm: Arc<dyn LanguageModel> = model.clone();
    let gateway = Arc::new(Gateway::new(Arc::new(Keyed(on.then_some(lm))), budget, governor.clone()));
    let status = Arc::new(Status::new(gateway.clone(), clock.clone(), "claude-opus-5-5".into()));
    let calls = Calls::new(gateway, status.clone(), None, false);
    World { model, ledger, clock, governor, calls, status }
}

fn schema() -> Value {
    json!({"type": "object", "properties": {"text": {"type": "string", "maxLength": 10}}, "required": ["text"]})
}

#[test]
fn a_call_goes_under_the_system_prompt_at_medium_effort_with_its_schema_closed_and_is_counted() {
    let w = world(true, None);
    let answer = w.calls.call_claude_json("Rewrite this.", &schema(), None, "rewrite").expect("an answer");
    assert_eq!(answer.get("text"), Some(&json!("ok")));
    let prompts = w.model.prompts.lock().expect("prompts").clone();
    let [sent] = prompts.try_into().expect("one call");
    assert_eq!(sent.system, Some(mm_assistant::prompts::SYSTEM));
    assert_eq!((sent.model.as_str(), sent.effort.as_deref(), sent.fallback), ("claude-opus-5-5", Some("medium"), true));
    assert_eq!(
        sent.schema,
        Some(json!({"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"],
                    "additionalProperties": false}))
    );
    let spends = w.ledger.0.lock().expect("ledger").clone();
    let [spend] = spends.try_into().expect("one row");
    // 1000 input tokens at $4 and 100 output at $20 a million: 4000 + 2000 micro-dollars.
    assert_eq!(
        (spend.day.as_str(), spend.feature.as_str(), spend.outcome, spend.cost_micros),
        ("2026-11-02", "rewrite", "ok", 6000)
    );
}

#[test]
fn with_ai_off_nothing_is_sent_counted_or_noted() {
    let w = world(false, None);
    assert_eq!(w.calls.call_claude("Say hello.", "thread"), Err(CallError::NotSent(NotSent::AiOff)));
    assert!(w.model.prompts.lock().expect("prompts").is_empty() && w.ledger.0.lock().expect("ledger").is_empty());
    let report = w.status.report().expect("a report");
    assert_eq!(
        (report["engine"].clone(), report["last_ok"].clone(), report["model"].clone()),
        (json!("none"), Value::Null, json!(""))
    );
}

#[test]
fn at_the_limit_an_interactive_call_is_held_back_until_the_person_allows_it() {
    let w = world(true, Some(json!(1)));
    w.ledger.1.store(1_000_000, Ordering::SeqCst);
    assert_eq!(
        w.calls.call_claude("Say hello.", "thread"),
        Err(CallError::NotSent(NotSent::OverLimit { spent: 1_000_000, limit: 1_000_000 }))
    );
    assert!(w.model.prompts.lock().expect("prompts").is_empty());
    assert!(allowing_over(true, || w.calls.call_claude("Say hello.", "thread")).is_ok());
    assert_eq!(w.model.prompts.lock().expect("prompts").len(), 1);
    // Background work stops for the day.
    w.clock.shift(46);
    assert_eq!(w.calls.call_claude("Triage.", "triage"), Err(CallError::NotSent(NotSent::Deferred("limit"))));
}

#[test]
fn background_work_yields_to_a_person_and_speculative_work_waits_for_one() {
    let w = world(true, None);
    w.calls.call_claude("Asked for.", "thread").expect("an interactive call");
    assert_eq!(w.calls.call_claude("Triage.", "triage"), Err(CallError::NotSent(NotSent::Deferred("yielding"))));
    w.clock.shift(46);
    assert!(w.calls.call_claude("Triage.", "triage").is_ok());
    w.clock.shift(PRESENCE_SECONDS as i64);
    assert_eq!(w.calls.call_claude("Ahead.", "draft-ahead"), Err(CallError::NotSent(NotSent::Deferred("away"))));
    w.governor.saw_someone();
    assert!(w.calls.call_claude("Ahead.", "draft-ahead").is_ok());
}

#[test]
fn a_rejected_key_stops_background_work_and_is_noted_for_the_status() {
    let w = world(true, None);
    w.model.replies.lock().expect("replies").push(Err(ModelError::new("Claude API 401: invalid x-api-key", Some(401))));
    w.clock.shift(100);
    assert!(matches!(w.calls.call_claude("Triage.", "triage"), Err(CallError::Model(_))));
    assert_eq!(w.calls.call_claude("Triage.", "triage"), Err(CallError::NotSent(NotSent::Deferred("breaker"))));
    let report = w.status.report().expect("a report");
    assert_eq!(
        (report["last_ok"].clone(), report["last_error"].clone()),
        (json!(false), json!("the API key was rejected"))
    );
    assert_eq!(report["last_ts"], json!(NOW + 100));
    let rejected = w.ledger.0.lock().expect("ledger").iter().filter(|s| s.outcome == "rejected").count();
    assert_eq!(rejected, 1);
    // Someone asking still goes, so they see the error.
    assert!(w.calls.call_claude("Asked for.", "thread").is_ok());
}

#[test]
fn three_rejections_in_a_row_stop_that_feature_on_that_model() {
    let w = world(true, None);
    w.clock.shift(100);
    for _ in 0..3 {
        w.model.replies.lock().expect("replies").push(Err(ModelError::new("Claude API 400: bad", Some(400))));
        assert!(matches!(w.calls.call_claude("Triage.", "triage"), Err(CallError::Model(_))));
    }
    assert_eq!(w.calls.call_claude("Triage.", "triage"), Err(CallError::NotSent(NotSent::Deferred("breaker"))));
    assert!(w.calls.call_claude("Ahead.", "draft-ahead").is_ok());
}

#[test]
fn prices_are_counted_in_micro_dollars_rounded_up_at_the_dearer_model() {
    use mm_assistant::pricing::{batch_micros, cost_micros};
    assert_eq!(cost_micros("claude-opus-5-5", "", 1_000_000, 0, 0, 0), 4_000_000);
    assert_eq!(cost_micros("claude-haiku-5-5", "", 1, 0, 0, 0), 1);
    assert_eq!(cost_micros("claude-haiku-5-5-20261101", "claude-opus-5-5", 0, 1000, 0, 0), 20_000);
    assert_eq!(cost_micros("claude-unknown", "", 0, 1000, 0, 0), 50_000);
    assert_eq!(cost_micros("claude-haiku-5-5", "", 100_001, 0, 0, 0), 50_001);
    assert_eq!(cost_micros("claude-opus-5-5", "", 0, 0, 1000, 1000), 5_000 + 200);
    assert_eq!(batch_micros(15), 8);
}

#[test]
fn a_schema_goes_out_closed_and_without_bounds_and_a_loose_reply_is_read() {
    use mm_assistant::schema::{object_in, structured};
    let nested = json!({"type": "object", "properties": {
        "maximum": {"type": "string", "maxLength": 10},
        "groups": {"type": "array", "items": {"type": "object", "properties": {"n": {"type": "integer", "minimum": 0}}}}}});
    let out = structured(&nested);
    assert_eq!(out["properties"]["maximum"], json!({"type": "string"}));
    assert_eq!(out["properties"]["groups"]["items"]["additionalProperties"], json!(false));
    assert_eq!(out["properties"]["groups"]["items"]["properties"]["n"], json!({"type": "integer"}));
    assert_eq!(object_in("Here you go: {\"summary\": \"loose\"} done").get("summary"), Some(&json!("loose")));
    assert!(object_in("no JSON here").is_empty() && object_in("[1, 2]").is_empty());
}

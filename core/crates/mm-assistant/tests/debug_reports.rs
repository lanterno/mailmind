//! What the debug routes report of AI (`tests/integration/assistant/test_ai_ledger.py`'s and `test_governor.py`'s): each
//! local day's calls and cost from the usage ledger (`Gateway.usage`), and the cost governor's snapshot.

use std::sync::atomic::{AtomicI64, Ordering};
use std::sync::{Arc, Mutex};

use mm_assistant::features::Kind;
use mm_assistant::governor::{CostGovernor, Spending};
use mm_assistant::ports::{FeatureDay, LanguageModel, ModelError, Prompt, Reply, UsageDays};
use mm_assistant::usage::UsageReport;
use mm_kernel::errors::Fault;
use mm_kernel::seams::{Clock, Moment};
use mm_kernel::zone::{Utc, Zone};
use serde_json::{Value, json};

#[derive(Debug)]
struct Frozen(AtomicI64);

impl Clock for Frozen {
    fn now(&self) -> Moment {
        Moment::from_micros(self.0.load(Ordering::SeqCst))
    }
}

impl Frozen {
    fn shift_micros(&self, micros: i64) {
        self.0.fetch_add(micros, Ordering::SeqCst);
    }
}

/// Monday 2 November 2026, noon UTC.
const NOW: i64 = 1_793_620_800;

fn frozen() -> Arc<Frozen> {
    Arc::new(Frozen(AtomicI64::new(NOW * 1_000_000)))
}

/// The ledger's rows as its SQL adds them up, and the day each report asked from.
#[derive(Debug, Default)]
struct Days {
    rows: Vec<FeatureDay>,
    asked: Mutex<Vec<String>>,
}

impl UsageDays for Days {
    fn days(&self, since: &str) -> Result<Vec<FeatureDay>, Fault> {
        self.asked.lock().expect("asked").push(since.to_owned());
        Ok(self.rows.iter().filter(|r| r.day.as_str() >= since).cloned().collect())
    }
}

fn row(day: &str, feature: &str, calls: i64, cost_micros: i64) -> FeatureDay {
    FeatureDay { day: day.into(), feature: feature.into(), calls, cost_micros }
}

/// A zone ten hours ahead of UTC, where noon on the 2nd is ten at night.
#[derive(Debug)]
struct Ahead;

impl Zone for Ahead {
    fn offset_at(&self, _utc: i64) -> i64 {
        10 * 3600
    }
}

fn reporting(rows: Vec<FeatureDay>, zone: Arc<dyn Zone>) -> (UsageReport, Arc<Days>, Arc<Frozen>) {
    let days = Arc::new(Days { rows, asked: Mutex::default() });
    let clock = frozen();
    (UsageReport::new(days.clone(), clock.clone(), zone), days, clock)
}

#[test]
fn each_day_lists_its_calls_by_feature_newest_first_and_a_day_without_any_with_zeros() {
    let (report, days, _) = reporting(
        vec![
            row("2026-11-03", "triage", 7, 70_000),
            row("2026-11-02", "ask", 1, 4_000),
            row("2026-11-02", "triage", 4, 9_000),
            row("2026-10-31", "brief", 1, 20_000),
            row("2026-10-29", "thread", 5, 15_000),
        ],
        Arc::new(Utc),
    );
    assert_eq!(
        report.days(4).expect("a report"),
        vec![
            json!({"day": "2026-11-02", "calls": 5, "cost_micros": 13_000,
                   "features": {"ask": {"calls": 1, "cost_micros": 4_000}, "triage": {"calls": 4, "cost_micros": 9_000}}}),
            json!({"day": "2026-11-01", "calls": 0, "cost_micros": 0, "features": {}}),
            json!({"day": "2026-10-31", "calls": 1, "cost_micros": 20_000,
                   "features": {"brief": {"calls": 1, "cost_micros": 20_000}}}),
            json!({"day": "2026-10-30", "calls": 0, "cost_micros": 0, "features": {}}),
        ]
    );
    // The ledger is asked from the oldest day listed; a day after today, which a clock set back leaves, isn't listed.
    assert_eq!(*days.asked.lock().expect("asked"), vec!["2026-10-30".to_owned()]);
}

#[test]
fn at_least_today_is_listed_and_the_days_run_back_across_months_and_years() {
    let (report, _, clock) = reporting(vec![], Arc::new(Utc));
    let listed = |n| report.days(n).expect("a report").iter().map(|d| d["day"].clone()).collect::<Vec<Value>>();
    assert_eq!(listed(0), vec![json!("2026-11-02")]);
    assert_eq!(listed(-3), vec![json!("2026-11-02")]);
    assert_eq!(listed(3), vec![json!("2026-11-02"), json!("2026-11-01"), json!("2026-10-31")]);
    let year = listed(366);
    assert_eq!((year.len(), year.last()), (366, Some(&json!("2025-11-02"))));
    // 1 March 2028, after a leap day.
    clock.0.store(1_835_481_600 * 1_000_000, Ordering::SeqCst);
    assert_eq!(listed(2), vec![json!("2028-03-01"), json!("2028-02-29")]);
}

#[test]
fn a_day_is_the_persons_local_one() {
    let (report, _, _) = reporting(vec![row("2026-11-02", "ask", 1, 4_000)], Arc::new(Ahead));
    let [today] = report.days(1).expect("a report").try_into().expect("one day");
    assert_eq!((today["day"].clone(), today["calls"].clone()), (json!("2026-11-02"), json!(1)));
    let (later, _, clock) = reporting(vec![], Arc::new(Ahead));
    clock.shift_micros(3 * 3600 * 1_000_000);
    assert_eq!(later.days(1).expect("a report")[0]["day"], json!("2026-11-03"));
}

/// What the governor reads of the day, or a ledger that can't be read.
#[derive(Debug)]
struct Day {
    spent: Result<i64, Fault>,
    reserved: Result<i64, Fault>,
}

impl Spending for Day {
    fn spent_today(&self) -> Result<i64, Fault> {
        self.spent
    }
    fn limit_micros(&self) -> i64 {
        15_000_000
    }
    fn reserved(&self) -> Result<i64, Fault> {
        self.reserved
    }
}

#[derive(Debug)]
struct Silent;

impl LanguageModel for Silent {
    fn complete(&self, _prompt: &Prompt) -> Result<Reply, ModelError> {
        Err(ModelError::new("unused", None))
    }
}

fn governor(day: Day) -> (CostGovernor, Arc<Frozen>) {
    let clock = frozen();
    (CostGovernor::new(Arc::new(day), clock.clone()), clock)
}

#[test]
fn the_snapshot_shows_the_day_and_nothing_going_on() {
    let (governor, _) = governor(Day { spent: Ok(19_000), reserved: Ok(12_000) });
    assert_eq!(
        governor.snapshot(),
        json!({"spent_today_micros": 19_000, "limit_micros": 15_000_000, "reserved_micros": 12_000,
               "interactive_running": 0, "since_interactive": null, "since_seen": 0.0, "breaker": []})
    );
}

#[test]
fn a_ledger_that_cant_be_read_shows_as_no_spending_known() {
    let (governor, _) = governor(Day { spent: Err(Fault::new("Storage")), reserved: Err(Fault::new("Storage")) });
    let snapshot = governor.snapshot();
    assert_eq!((&snapshot["spent_today_micros"], &snapshot["reserved_micros"]), (&Value::Null, &Value::Null));
    assert_eq!(snapshot["limit_micros"], json!(15_000_000));
}

#[test]
fn the_seconds_since_a_click_and_since_someone_was_here_are_to_a_tenth_as_python_rounds_them() {
    let (governor, clock) = governor(Day { spent: Ok(0), reserved: Ok(0) });
    let model: Arc<dyn LanguageModel> = Arc::new(Silent);
    let during = governor
        .call(Kind::Interactive, "rewrite", "claude-opus-5-5", &model, || governor.snapshot())
        .expect("it goes");
    assert_eq!((during["interactive_running"].clone(), during["since_interactive"].clone()), (json!(1), Value::Null));
    // 2.25 seconds is a tie, which goes to the even tenth; 123.45 is a little over, in binary.
    clock.shift_micros(2_250_000);
    let at = governor.snapshot();
    assert_eq!((at["interactive_running"].clone(), at["since_interactive"].clone()), (json!(0), json!(2.2)));
    assert_eq!(at["since_seen"], json!(2.2));
    clock.shift_micros(121_200_000);
    assert_eq!(governor.snapshot()["since_interactive"], json!(123.5));
    // A twentieth of a second, at this moment's precision, is a hair under: none.
    governor.saw_someone();
    clock.shift_micros(50_000);
    assert_eq!(governor.snapshot()["since_seen"], json!(0.0));
    clock.shift_micros(250_000);
    let now = governor.snapshot();
    assert_eq!((now["since_interactive"].clone(), now["since_seen"].clone()), (json!(123.8), json!(0.3)));
}

#[test]
fn the_breaker_shows_where_it_is_open_by_name_sorted() {
    let (governor, _) = governor(Day { spent: Ok(0), reserved: Ok(0) });
    let model: Arc<dyn LanguageModel> = Arc::new(Silent);
    let refused = ModelError::new("bad request", Some(400));
    for _ in 0..3 {
        governor.heard("triage", "claude-sonnet-5-5", &model, Some(&refused));
    }
    assert_eq!(governor.snapshot()["breaker"], json!(["triage on claude-sonnet-5-5"]));
    governor.heard("rewrite", "claude-opus-5-5", &model, Some(&ModelError::new("invalid x-api-key", Some(401))));
    assert_eq!(governor.snapshot()["breaker"], json!(["every feature", "triage on claude-sonnet-5-5"]));
    governor.forget();
    assert_eq!(governor.snapshot()["breaker"], json!([]));
}

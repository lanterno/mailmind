//! What the log says as the cost governor's breaker opens (`governor.heard`): what stops, every background call or a
//! feature on a model, and the status that stopped it, once each time it opens. A test binary of its own, as the log's
//! sink is the process's.

use std::sync::atomic::AtomicI64;
use std::sync::{Arc, Mutex};

use mm_assistant::governor::{CostGovernor, Spending};
use mm_assistant::ports::{LanguageModel, ModelError, Prompt, Reply};
use mm_kernel::errors::Fault;
use mm_kernel::log::{self, Line, Sink};
use mm_kernel::seams::{Clock, Moment};

#[derive(Debug)]
struct Frozen(AtomicI64);

impl Clock for Frozen {
    fn now(&self) -> Moment {
        Moment::from_micros(self.0.load(std::sync::atomic::Ordering::SeqCst))
    }
}

#[derive(Debug)]
struct Nothing;

impl Spending for Nothing {
    fn spent_today(&self) -> Result<i64, Fault> {
        Ok(0)
    }
    fn limit_micros(&self) -> i64 {
        15_000_000
    }
    fn reserved(&self) -> Result<i64, Fault> {
        Ok(0)
    }
}

#[derive(Debug)]
struct Silent;

impl LanguageModel for Silent {
    fn complete(&self, _prompt: &Prompt) -> Result<Reply, ModelError> {
        Err(ModelError::new("unused", None))
    }
}

/// Each line as the log's file would say it: its level, tag, message and named fields.
#[derive(Debug, Default)]
struct Kept(Mutex<Vec<String>>);

#[derive(Debug)]
struct Keeping(Arc<Kept>);

impl Sink for Keeping {
    fn write(&self, line: &Line<'_>) {
        let fields: Vec<String> = line.fields.iter().map(|(f, v)| format!("{}={v}", f.name())).collect();
        let said = format!("{} [{}] {} {fields:?}", line.level.name(), line.tag, line.message);
        self.0.0.lock().unwrap_or_else(std::sync::PoisonError::into_inner).push(said);
    }
}

#[test]
fn the_breaker_opening_says_what_stops_and_the_status_once_each_time() {
    let kept = Arc::new(Kept::default());
    log::install(Box::new(Keeping(kept.clone())));
    let governor = CostGovernor::new(Arc::new(Nothing), Arc::new(Frozen(AtomicI64::new(1_793_620_800_000_000))));
    let model: Arc<dyn LanguageModel> = Arc::new(Silent);
    let refused = ModelError::new("bad request", Some(400));
    for _ in 0..3 {
        governor.heard("triage", "claude-sonnet-5-5", &model, Some(&refused));
    }
    let key = ModelError::new("invalid x-api-key", Some(401));
    governor.heard("rewrite", "claude-opus-5-5", &model, Some(&key));
    // Open already, under the same model: nothing more is said.
    governor.heard("ask", "claude-opus-5-5", &model, Some(&ModelError::new("forbidden", Some(403))));
    let message = "the API keeps rejecting requests: %(what)s stops until the key or the model changes (%(why)s)";
    assert_eq!(
        *kept.0.lock().unwrap_or_else(std::sync::PoisonError::into_inner),
        [
            format!(r#"WARNING [ai] {message} ["what=triage on claude-sonnet-5-5", "why=400"]"#),
            format!(r#"WARNING [ai] {message} ["what=every background call", "why=401"]"#),
        ]
    );
}

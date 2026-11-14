//! What the app says of its own running (`mailmind.http.routers.debug`): the connection pool, the background jobs, what
//! AI cost, and all of that at a glance.
//!
//! The page asks none of these: they are for the owner, and say only ids and counts, and of a job's error its class's
//! name, never an address, a subject or a key. Asking `/api/debug/health` never counts as someone being here, as
//! `assistant_wiring`'s `UI_QUIET` says; asking any of the others does, as any request to the API does.

use std::sync::Arc;

use axum::Router;
use axum::body::Body;
use axum::extract::State;
use axum::http::{Method, Response, Uri};
use axum::routing::get;
use mm_assistant::batches::BatchStatus;
use mm_kernel::errors::Failure;
use mm_kernel::when;
use serde_json::{Value, json};

use crate::answer;
use crate::app::App;
use crate::query::Query;

pub(super) fn routes() -> Router<Arc<App>> {
    Router::new()
        .route("/api/debug/health", get(health))
        .route("/api/debug/pool", get(pool))
        .route("/api/debug/jobs", get(jobs))
        .route("/api/debug/ai", get(ai))
}

/// The days `/api/debug/ai` lists: 30 unless asked, and held to 1 to 366.
const DAYS: (i64, i64, i64) = (30, 1, 366);

/// What is out in a batch (`Batch`).
fn batch(status: BatchStatus) -> Value {
    json!({"waiting": status.waiting, "state": status.state, "estimate_micros": status.estimate_micros})
}

/// How the app is doing at a glance (`AtAGlance`): the jobs, IDLE's workers counted, the rows in each state of the
/// outbox and the change queue and the saves still to reach their server, where the AI breaker is open, and what AI
/// cost today against the limit, with the batch out.
async fn health(State(app): State<Arc<App>>, method: Method, uri: Uri) -> Response<Body> {
    super::run(&app, &method, &uri, |app| {
        let idle = app.realtime.watching();
        let governor = app.assistant.governor.snapshot();
        let today = app.assistant.usage.days(1)?.into_iter().next().unwrap_or_default();
        Ok(json!({
            "ok": true,
            "profile": app.profile,
            "generated": when::iso(app.clock.now()),
            "jobs": app.jobs.snapshot(),
            "idle": {"accounts": idle.len(), "running": idle.iter().filter(|(_, running)| *running).count()},
            "queues": {
                "outbox": app.reading.outbox.counts()?,
                "commands": app.commands.counts()?,
                "drafts_waiting": app.reading.outbox.drafts_waiting()?,
            },
            "breakers": governor["breaker"],
            "ai": {
                "today": today,
                "limit_micros": governor["limit_micros"],
                "batch": batch(app.background.assessments.batch_status()?),
            },
        }))
    })
    .await
}

/// How the IMAP connection pool stands (`mailbox.pool.stats`). The Rust server keeps no connection between requests
/// yet, whatever `MAILMIND_POOL` or an account's `"pool"` asks: each read opens one and logs out. So the pool is off,
/// with nothing counted and no connection kept.
async fn pool(State(app): State<Arc<App>>, method: Method, uri: Uri) -> Response<Body> {
    super::run(&app, &method, &uri, |_| {
        Ok(json!({
            "ok": true,
            "enabled": false,
            "target": "",
            "counters": {"created": 0, "reused": 0, "validate_fail": 0, "discard_dirty": 0},
            "slots": [],
        }))
    })
    .await
}

/// The background jobs' timing and how their last rounds went (`Jobs`): none in the served suites, which start no
/// workers.
async fn jobs(State(app): State<Arc<App>>, method: Method, uri: Uri) -> Response<Body> {
    super::run(&app, &method, &uri, |app| {
        Ok(json!({
            "ok": true,
            "profile": app.profile,
            "jobs": app.jobs.snapshot(),
            "generated": when::iso(app.clock.now()),
        }))
    })
    .await
}

/// What AI cost each local day, by feature, for the last `days` (`AiCosts`), with the cost governor and the batch out.
async fn ai(State(app): State<Arc<App>>, method: Method, uri: Uri) -> Response<Body> {
    let (default, fewest, most) = DAYS;
    let days = match Query::parse(uri.query().unwrap_or_default().as_bytes()).number("days", default) {
        Ok(days) => days.clamp(fewest, most),
        Err(problem) => return answer::failure(&Failure::Problem(problem), method.as_str(), uri.path()),
    };
    super::run(&app, &method, &uri, move |app| {
        Ok(json!({
            "ok": true,
            "days": app.assistant.usage.days(days)?,
            "governor": app.assistant.governor.snapshot(),
            "batch": batch(app.background.assessments.batch_status()?),
            "generated": when::iso(app.clock.now()),
        }))
    })
    .await
}

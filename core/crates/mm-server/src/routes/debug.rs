//! What the app says of its own running (`mailmind.http.routers.debug`), as far as the Rust server runs it.
//!
//! The page asks none of these. `/api/debug/pool` is here because the served suites read it; `/api/debug/health`,
//! `/api/debug/jobs` and `/api/debug/ai` wait for the jobs, the queues and the AI calls they report on.

use std::sync::Arc;

use axum::Router;
use axum::body::Body;
use axum::extract::State;
use axum::http::{Method, Response, Uri};
use axum::routing::get;
use serde_json::json;

use crate::app::App;

pub(super) fn routes() -> Router<Arc<App>> {
    Router::new().route("/api/debug/pool", get(pool))
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

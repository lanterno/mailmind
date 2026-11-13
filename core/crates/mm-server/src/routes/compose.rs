//! The outbox tray (`mailmind.http.routers.compose`): the recent sends, and the saves still to reach their server.

use std::sync::Arc;

use axum::Router;
use axum::body::Body;
use axum::extract::State;
use axum::http::{Method, Response, Uri};
use axum::routing::get;
use mm_kernel::errors::Failure;

use super::reading::{query, run};
use crate::answer;
use crate::app::App;

pub(super) fn routes() -> Router<Arc<App>> {
    Router::new().route("/api/outbox", get(outbox))
}

/// The newest sends, `limit` of them (50 unless asked), and the drafts whose saves are overdue.
async fn outbox(State(app): State<Arc<App>>, method: Method, uri: Uri) -> Response<Body> {
    let limit = match query(&uri).number("limit", 50) {
        Ok(limit) => limit,
        Err(problem) => return answer::failure(&Failure::Problem(problem), method.as_str(), uri.path()),
    };
    run(&app, &method, &uri, move |app| Ok(app.reading.outbox.tray(limit)?)).await
}

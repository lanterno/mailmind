//! The accounts' routes (`mailmind.http.routers.accounts`).

use std::sync::Arc;

use axum::Router;
use axum::body::Body;
use axum::extract::State;
use axum::http::{Method, Response, Uri};
use axum::routing::get;

use crate::app::App;

pub(super) fn routes() -> Router<Arc<App>> {
    Router::new().route("/api/accounts", get(listed)).route("/api/account-health", get(account_health))
}

/// Every account as the API may show it, the default signature, and when this was said.
async fn listed(State(app): State<Arc<App>>, method: Method, uri: Uri) -> Response<Body> {
    super::run(&app, &method, &uri, |app| app.directory.listed()).await
}

/// How each account stands: its last sync that worked and its last that failed, and how its sync stands now.
async fn account_health(State(app): State<Arc<App>>, method: Method, uri: Uri) -> Response<Body> {
    super::run(&app, &method, &uri, |app| app.health.answer()).await
}

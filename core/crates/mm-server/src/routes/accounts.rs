//! The accounts' routes (`mailmind.http.routers.accounts`).

use std::sync::Arc;

use axum::Router;
use axum::body::Body;
use axum::extract::State;
use axum::http::{Method, Response, Uri};
use axum::routing::get;

use crate::app::App;

pub(super) fn routes() -> Router<Arc<App>> {
    Router::new().route("/api/accounts", get(listed))
}

/// Every account as the API may show it, the default signature, and when this was said.
async fn listed(State(app): State<Arc<App>>, method: Method, uri: Uri) -> Response<Body> {
    super::run(&app, &method, &uri, |app| app.directory.listed()).await
}

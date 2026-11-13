//! Tidy's route that reads alone (`mailmind.http.routers.mailbox`'s `GET /api/tidy`): the newest run a person can
//! still take back. Taking it back is the Mailbox's Undo.

use std::sync::Arc;

use axum::Router;
use axum::body::Body;
use axum::extract::State;
use axum::http::{Method, Response, Uri};
use axum::routing::get;

use crate::app::App;

pub(super) fn routes() -> Router<Arc<App>> {
    Router::new().route("/api/tidy", get(tidy))
}

/// The newest run of Tidy that can still be taken back, or no group at all.
async fn tidy(State(app): State<Arc<App>>, method: Method, uri: Uri) -> Response<Body> {
    super::run(&app, &method, &uri, |app| Ok(app.tidying.latest()?)).await
}

//! The routes: each a plain async fn on a literal path, so grepping the path finds its handler, in a module of the
//! context it serves (`mailbox`, `accounts`) or `page`.
//!
//! A handler decodes the request (`Query` for a GET's query string), calls one use case through `run`, and encodes
//! what it answers; the use case's `Failure` is a problem document or the fixed 500 (`answer::failure`). A use case
//! may wait on a mail server or the disk, so `run` calls it on a thread of the blocking pool, never on the one
//! answering requests.
//!
//! What no route matches, by path or by method, is the Python app's `_no_route`: a 404, or a 501 for a method it
//! doesn't implement.

mod accounts;
mod mailbox;
mod page;
// The Mailbox's reads a person waits on, the outbox, the documents and the exports (area B).
mod compose;
mod documents;
mod export;
mod reading;

use std::sync::Arc;

use axum::Router;
use axum::body::Body;
use axum::http::{Method, Response, Uri};
use mm_kernel::errors::{Failure, Fault};
use serde_json::Value;

use crate::answer;
use crate::app::App;

pub fn router(app: Arc<App>) -> Router {
    Router::new()
        .merge(page::routes())
        .merge(accounts::routes())
        .merge(mailbox::routes())
        .merge(reading::routes())
        .merge(export::routes())
        .merge(compose::routes())
        .merge(documents::routes())
        .fallback(no_route)
        .method_not_allowed_fallback(no_route)
        .with_state(app)
}

async fn no_route(method: Method, uri: Uri) -> Response<Body> {
    answer::no_route(method.as_str(), uri.path())
}

/// `work`'s answer, run on the blocking pool: its value as JSON, or its failure.
pub(crate) async fn run(
    app: &Arc<App>,
    method: &Method,
    uri: &Uri,
    work: impl FnOnce(&App) -> Result<Value, Failure> + Send + 'static,
) -> Response<Body> {
    let app = app.clone();
    let done = tokio::task::spawn_blocking(move || work(&app)).await;
    match done.unwrap_or(Err(Failure::Fault(Fault::new("Panicked")))) {
        Ok(value) => answer::json(200, &value),
        Err(failure) => answer::failure(&failure, method.as_str(), uri.path()),
    }
}

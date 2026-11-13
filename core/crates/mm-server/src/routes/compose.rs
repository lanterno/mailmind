//! Compose's routes (`mailmind.http.routers.compose`), as far as the Rust server has come: the outbox tray (the recent
//! sends, and the saves still to reach their server) and the snippets.

use std::sync::Arc;

use axum::Router;
use axum::body::Body;
use axum::extract::State;
use axum::http::{HeaderMap, Method, Response, Uri};
use axum::routing::get;
use mm_kernel::errors::{Failure, Problem};
use serde_json::{Map, Value};

use super::reading::query;
use crate::answer;
use crate::app::App;
use crate::schema;

pub(super) fn routes() -> Router<Arc<App>> {
    Router::new().route("/api/outbox", get(outbox)).route("/api/snippets", get(snippets).post(snippets_post))
}

/// The newest sends, `limit` of them (50 unless asked), and the drafts whose saves are overdue.
async fn outbox(State(app): State<Arc<App>>, method: Method, uri: Uri) -> Response<Body> {
    let limit = match query(&uri).number("limit", 50) {
        Ok(limit) => limit,
        Err(problem) => return answer::failure(&Failure::Problem(problem), method.as_str(), uri.path()),
    };
    super::reading::run(&app, &method, &uri, move |app| Ok(app.reading.outbox.tray(limit)?)).await
}

/// Every snippet kept.
async fn snippets(State(app): State<Arc<App>>, method: Method, uri: Uri) -> Response<Body> {
    super::run(&app, &method, &uri, |app| app.snippets.listed()).await
}

/// A snippet saved or deleted, and the snippets after it.
async fn snippets_post(
    State(app): State<Arc<App>>,
    method: Method,
    uri: Uri,
    headers: HeaderMap,
    body: Body,
) -> Response<Body> {
    let asked = match crate::body::object(uri.path(), &headers, body).await {
        Ok(body) => snippet_change(&body),
        Err(refused) => return *refused,
    };
    super::run(&app, &method, &uri, move |app| app.snippets.post(&asked?)).await
}

/// `SnippetChange`: a verb, save or delete (left out is the use case's to word), a trigger and a text.
fn snippet_change(body: &Map<String, Value>) -> Result<Map<String, Value>, Problem> {
    let mut dumped = Map::new();
    schema::optional(body, "verb", |v| schema::choice(v, &["save", "delete"]), &mut dumped)?;
    schema::optional(body, "trigger", schema::text, &mut dumped)?;
    schema::optional(body, "text", schema::text, &mut dumped)?;
    Ok(dumped)
}

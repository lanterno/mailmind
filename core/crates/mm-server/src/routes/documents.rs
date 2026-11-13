//! The Document Hub (`mailmind.http.routers.discovery`'s `/api/documents`): every real attachment across the
//! accounts, by kind, by words and in sections.

use std::sync::Arc;

use axum::Router;
use axum::body::Body;
use axum::extract::State;
use axum::http::{Method, Response, Uri};
use axum::routing::get;
use mm_discovery::documents::{GROUPINGS, asked};
use mm_kernel::errors::{Failure, Problem};

use super::reading::{query, run};
use crate::answer;
use crate::app::App;

pub(super) fn routes() -> Router<Arc<App>> {
    Router::new().route("/api/documents", get(documents))
}

/// A page of the documents (`DocumentsArgs`): a limit and an offset that won't read are their defaults, and a
/// grouping it doesn't know is the `bad request` refusal.
async fn documents(State(app): State<Arc<App>>, method: Method, uri: Uri) -> Response<Body> {
    let given = query(&uri);
    let group = given.text("group");
    if group.is_some_and(|g| !GROUPINGS.contains(&g)) {
        return answer::failure(&Failure::Problem(Problem::bad_request()), method.as_str(), uri.path());
    }
    let want = asked(given.text("limit"), given.text("offset"), given.text("kind"), given.text("q"), group);
    run(&app, &method, &uri, move |app| Ok(app.reading.documents.page(&want)?)).await
}

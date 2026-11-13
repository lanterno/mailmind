//! The live event stream (`mailmind.http.routers.events`): each tab's `Listener` as server-sent events, for as long as
//! the tab stays.

use std::sync::Arc;

use axum::Router;
use axum::body::Body;
use axum::extract::State;
use axum::http::{HeaderMap, HeaderValue, Method, Response, StatusCode, Uri, header};
use axum::routing::get;
use mm_kernel::errors::{Failure, Fault};

use crate::app::App;
use crate::events::TabStream;
use crate::{answer, problem};

pub(super) fn routes() -> Router<Arc<App>> {
    Router::new().route("/api/events", get(events))
}

/// A tab that reconnects resumes after the Last-Event-ID its EventSource sends (read as Latin-1, as a header is), or
/// gets one resync.
async fn events(State(app): State<Arc<App>>, method: Method, uri: Uri, headers: HeaderMap) -> Response<Body> {
    let last: Option<String> =
        headers.get("last-event-id").map(|id| id.as_bytes().iter().map(|&b| char::from(b)).collect());
    let build = {
        let app = app.clone();
        tokio::task::spawn_blocking(move || app.page.edition(&app.csrf)).await.unwrap_or(Err(Fault::new("Panicked")))
    };
    let build = match build {
        Ok(build) => build,
        Err(fault) => return answer::failure(&Failure::Fault(fault), method.as_str(), uri.path()),
    };
    let Some(listener) = app.events.listen(last.as_deref()) else {
        return answer::problem_document(503, &problem::blank(503, "too many listeners"));
    };
    let mut answer = Response::new(Body::from_stream(TabStream::new(listener, &build)));
    *answer.status_mut() = StatusCode::OK;
    let headers = answer.headers_mut();
    headers.insert(header::CONTENT_TYPE, HeaderValue::from_static("text/event-stream"));
    headers.insert(header::CACHE_CONTROL, HeaderValue::from_static("no-cache"));
    answer
}

//! The Mailbox's lists and a conversation (`mailmind.http.routers.mailbox`): what the page
//! reads as it opens a folder, the Inboxes, a view that isn't a folder, or a conversation.
//!
//! A part of a route the Rust server can't do yet comes back from its use case as the `NOT_YET` fault, and answers
//! as `answer::not_yet`.

use std::sync::Arc;

use axum::Router;
use axum::body::Body;
use axum::extract::State;
use axum::http::{Method, Response, Uri};
use axum::routing::get;
use mm_kernel::errors::{Failure, Fault, Problem};
use mm_mailbox::lists::{INBOX, Paging};
use mm_mailbox::ports::NOT_YET;
use serde_json::Value;

use crate::answer;
use crate::app::App;
use crate::query::Query;

pub(super) fn routes() -> Router<Arc<App>> {
    Router::new()
        .route("/api/messages", get(messages))
        .route("/api/inboxes", get(inboxes))
        .route("/api/thread", get(thread))
        .route("/api/folders/across", get(folders_across))
        .route("/api/digest", get(digest))
}

pub(super) fn query(uri: &Uri) -> Query {
    Query::parse(uri.query().unwrap_or_default().as_bytes())
}

/// `routes::run`, but a part of the route the Rust server can't do yet answers as such.
pub(super) async fn run(
    app: &Arc<App>,
    method: &Method,
    uri: &Uri,
    work: impl FnOnce(&App) -> Result<Value, Failure> + Send + 'static,
) -> Response<Body> {
    let app = app.clone();
    let done = tokio::task::spawn_blocking(move || work(&app)).await;
    match done.unwrap_or(Err(Failure::Fault(Fault::new("Panicked")))) {
        Ok(value) => answer::json(200, &value),
        Err(Failure::Fault(fault)) if fault == NOT_YET => answer::not_yet(uri.path()),
        Err(failure) => answer::failure(&failure, method.as_str(), uri.path()),
    }
}

fn refused(problem: Problem, method: &Method, uri: &Uri) -> Response<Body> {
    answer::failure(&Failure::Problem(problem), method.as_str(), uri.path())
}

/// The page asked for (`ListArgs`): an offset and a limit, each a number, and `group=thread` for one row a
/// conversation.
fn paging(given: &Query) -> Result<Paging, Problem> {
    Ok(Paging {
        offset: given.number("offset", 0)?,
        limit: given.number("limit", 80)?,
        grouped: given.text("group") == Some("thread"),
    })
}

/// A folder's messages, or a view that isn't a folder's (`__snoozed__`, `__muted__`, `__waiting__`,
/// `__replylater__`), which with no account named reads every account.
async fn messages(State(app): State<Arc<App>>, method: Method, uri: Uri) -> Response<Body> {
    let given = query(&uri);
    let page = match paging(&given) {
        Ok(page) => page,
        Err(problem) => return refused(problem, &method, &uri),
    };
    let account = given.text("account").map(str::to_owned);
    let folder = given.text("folder").unwrap_or(INBOX).to_owned();
    run(&app, &method, &uri, move |app| app.reading.lists.messages_in(account.as_deref(), &folder, page)).await
}

/// Every account's Inbox as one list.
async fn inboxes(State(app): State<Arc<App>>, method: Method, uri: Uri) -> Response<Body> {
    let page = match paging(&query(&uri)) {
        Ok(page) => page,
        Err(problem) => return refused(problem, &method, &uri),
    };
    run(&app, &method, &uri, move |app| app.reading.lists.all_inboxes(page)).await
}

/// A conversation's messages, oldest first.
async fn thread(State(app): State<Arc<App>>, method: Method, uri: Uri) -> Response<Body> {
    let given = query(&uri);
    let account = given.text("account").map(str::to_owned);
    let thread_id = given.text("thread_id").map(str::to_owned);
    run(&app, &method, &uri, move |app| app.reading.lists.thread(account.as_deref(), thread_id.as_deref())).await
}

/// Snoozed, Muted, Waiting and Reply Later counted over every account, for the rail on All inboxes.
async fn folders_across(State(app): State<Arc<App>>, method: Method, uri: Uri) -> Response<Body> {
    run(&app, &method, &uri, |app| app.reading.lists.across_accounts()).await
}

/// The items the brief is built from, and what the page needs as it opens: the identities to send from, the sync's
/// state and the AI's.
async fn digest(State(app): State<Arc<App>>, method: Method, uri: Uri) -> Response<Body> {
    run(&app, &method, &uri, |app| app.reading.digest.answer()).await
}

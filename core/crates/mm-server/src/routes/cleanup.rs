//! A sender's cleanup and a sweep (`mailmind.http.routers.curation`): the preview a cleanup must come from, the
//! cleanup of one sender or of many, and a sweep of an inbox's junk.
//!
//! Each reads its body as its Python schema does, so a field the schema refuses is the `bad request` 400 before
//! anything is looked up. An unsubscribe this server can't send yet answers as not yet served, having changed nothing.

use std::sync::Arc;

use axum::Router;
use axum::body::Body;
use axum::extract::State;
use axum::http::{Method, Request, Response, Uri};
use axum::routing::{get, post};
use mm_curation::cleaning::Asked;
use mm_curation::sweeping::READ_ONLY;
use mm_kernel::errors::{Failure, Fault, NOT_YET, Problem};
use mm_kernel::log::{self, Field};
use serde_json::{Value, json};

use crate::answer;
use crate::app::App;
use crate::body::{self, Fields};
use crate::problem;
use crate::query::Query;

const TAG: &str = "cleanup";

pub(super) fn routes() -> Router<Arc<App>> {
    Router::new()
        .route("/api/cleanup/sender", get(cleanup_sender))
        .route("/api/cleanup/execute", post(cleanup_execute))
        .route("/api/cleanup/batch/preview", post(cleanup_batch_preview))
        .route("/api/cleanup/batch", post(cleanup_batch))
        .route("/api/sweep", post(sweep))
}

/// What a fault this route words itself answers: the cleanup's own 500, or the server's.
#[derive(Clone, Copy)]
enum Worded {
    CleanupFailed,
    RequestFailed,
}

/// `work`'s answer on the blocking pool. What this server can't send yet is a route not yet served, and a write a
/// read-only process may not make is the read-only 409.
async fn answered(
    app: Arc<App>,
    method: Method,
    uri: Uri,
    worded: Worded,
    work: impl FnOnce(&App) -> Result<Value, Failure> + Send + 'static,
) -> Response<Body> {
    let done = tokio::task::spawn_blocking(move || work(&app)).await;
    match done.unwrap_or(Err(Failure::Fault(Fault::new("Panicked")))) {
        Ok(value) => answer::json(200, &value),
        Err(Failure::Fault(NOT_YET)) => answer::not_yet(uri.path()),
        Err(Failure::Fault(READ_ONLY)) if method == Method::POST => {
            log::info(TAG, "POST %(path)s refused, read-only", &[(Field::Path, uri.path())]);
            let mut document = problem::blank(409, "MailMind is running read-only");
            document["type"] = json!("/problems/read-only");
            document["title"] = json!("Read-only");
            document["read_only"] = json!(true);
            answer::problem_document(409, &document)
        }
        Err(Failure::Fault(fault)) if matches!(worded, Worded::CleanupFailed) => {
            log::error(TAG, "error", &[], Some(fault.class));
            answer::problem_document(500, &problem::blank(500, "cleanup failed"))
        }
        Err(failure) => answer::failure(&failure, method.as_str(), uri.path()),
    }
}

/// A POST's answer: its body read and decoded, then `work` on the blocking pool.
async fn posted<T: Send + 'static>(
    app: Arc<App>,
    request: Request<Body>,
    worded: Worded,
    decode: impl FnOnce(&Fields) -> Result<T, Problem>,
    work: impl FnOnce(&App, T) -> Result<Value, Failure> + Send + 'static,
) -> Response<Body> {
    let (method, uri) = (request.method().clone(), request.uri().clone());
    let asked = match body::read(request).await {
        Ok(fields) => decode(&fields),
        Err(unread) => return unread.answer(),
    };
    match asked {
        Ok(asked) => answered(app, method, uri, worded, move |app| work(app, asked)).await,
        Err(refused) => answer::problem(&refused),
    }
}

/// A `list[str] | None` field: None where it is left out or null; anything but a list of strings is refused.
fn texts(fields: &Fields, name: &str) -> Result<Option<Vec<String>>, Problem> {
    match fields.given(name) {
        None | Some(Value::Null) => Ok(None),
        Some(Value::Array(items)) => items
            .iter()
            .map(|item| item.as_str().map(str::to_owned).ok_or_else(Problem::bad_request))
            .collect::<Result<_, _>>()
            .map(Some),
        Some(_) => Err(Problem::bad_request()),
    }
}

/// The preview a cleanup must come from, for one sender.
async fn cleanup_sender(State(app): State<Arc<App>>, method: Method, uri: Uri) -> Response<Body> {
    let addr = Query::parse(uri.query().unwrap_or_default().as_bytes()).text("addr").map(str::to_owned);
    answered(app, method, uri, Worded::RequestFailed, move |app| app.cleanups.cleaning.sender(addr.as_deref())).await
}

/// `CleanupBody`.
fn cleanup_asked(fields: &Fields) -> Result<Asked, Problem> {
    Ok(Asked {
        from_addr: fields.text("from_addr")?.map(str::to_owned),
        block_scope: fields.one_of("block_scope", &["sender", "domain"])?.map(str::to_owned),
        do_unsubscribe: fields.flag("do_unsubscribe")? == Some(true),
        confirm_token: fields.text("confirm_token")?.map(str::to_owned),
        undo_group: fields.text("undo_group")?.map(str::to_owned),
    })
}

/// The cleanup of one sender: the block and the sweep, then the unsubscribe the preview's token allows.
async fn cleanup_execute(State(app): State<Arc<App>>, request: Request<Body>) -> Response<Body> {
    posted(app, request, Worded::CleanupFailed, cleanup_asked, |app, asked| app.cleanups.cleaning.execute(&asked)).await
}

/// What cleaning up these senders together would do. It reads only: a POST for the list it names.
async fn cleanup_batch_preview(State(app): State<Arc<App>>, request: Request<Body>) -> Response<Body> {
    posted(
        app,
        request,
        Worded::RequestFailed,
        |fields| texts(fields, "senders"),
        |app, senders| app.cleanups.cleaning.batch_preview(senders.as_deref()),
    )
    .await
}

/// Many senders' blocks and sweeps under one undo group.
async fn cleanup_batch(State(app): State<Arc<App>>, request: Request<Body>) -> Response<Body> {
    posted(
        app,
        request,
        Worded::CleanupFailed,
        |fields| Ok((texts(fields, "senders")?, fields.text("undo_group")?.map(str::to_owned))),
        |app, (senders, group)| app.cleanups.cleaning.batch_execute(senders.as_deref(), group.as_deref()),
    )
    .await
}

/// A sweep of the account named, or of every one: a dry run's plan, or its junk moved to Trash.
async fn sweep(State(app): State<Arc<App>>, request: Request<Body>) -> Response<Body> {
    posted(
        app,
        request,
        Worded::RequestFailed,
        |fields| Ok((fields.text("account")?.map(str::to_owned), fields.flag("dry_run")? == Some(true))),
        |app, (account, dry_run)| app.cleanups.sweeps.run(Some(account.as_deref().unwrap_or("all")), dry_run),
    )
    .await
}

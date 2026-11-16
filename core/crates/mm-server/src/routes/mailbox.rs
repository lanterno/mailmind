//! The Mailbox's routes (`mailmind.http.routers.mailbox`).

use std::sync::Arc;

use axum::Router;
use axum::body::Body;
use axum::extract::State;
use axum::http::{Method, Request, Response, Uri};
use axum::routing::{get, post};
use mm_kernel::errors::{Failure, Fault, Problem};
use mm_mailbox::commands::READ_ONLY;
use mm_mailbox::folders::AccountFolders;
use serde_json::{Value, json};

use crate::answer;
use crate::app::App;
use crate::body::{self, Fields};
use crate::problem;
use crate::query::Query;

pub(super) fn routes() -> Router<Arc<App>> {
    Router::new()
        .route("/api/folders", get(folders))
        .route("/api/folders/create", post(folder_create))
        .route("/api/folders/rename", post(folder_rename))
        .route("/api/folders/delete", post(folder_delete))
        .route("/api/stats", get(stats))
        .route("/api/labels", get(labels))
}

fn query(uri: &Uri) -> Query {
    Query::parse(uri.query().unwrap_or_default().as_bytes())
}

/// One account's folders with their roles and counts, and the views that aren't folders counted.
async fn folders(State(app): State<Arc<App>>, method: Method, uri: Uri) -> Response<Body> {
    let Some(account) = query(&uri).text("account").map(str::to_owned) else {
        return answer::problem(&Problem::refused("account required").with("folders", json!([])));
    };
    super::run(&app, &method, &uri, move |app| app.folders.of_account(&account).map(|f| folders_answer(&f))).await
}

fn folders_answer(found: &AccountFolders) -> Value {
    let folders: Vec<Value> = found
        .folders
        .iter()
        .map(|(f, takes_filing)| {
            json!({
                "name": f.name,
                "role": f.role,
                "unseen": f.unseen,
                "total": f.total,
                "takes_filing": takes_filing,
                "own_name": f.own_name,
                "changeable": f.changeable,
            })
        })
        .collect();
    json!({
        "ok": true,
        "account": found.account,
        "folders": folders,
        "snoozed": found.counts.snoozed,
        "muted": found.counts.muted,
        "waiting": found.counts.waiting,
        "replylater": found.counts.replylater,
    })
}

/// Each account's stored inbox counted by bucket, with its top senders, and the totals across accounts.
async fn stats(State(app): State<Arc<App>>, method: Method, uri: Uri) -> Response<Body> {
    super::run(&app, &method, &uri, |app| Ok(app.stats.live()?)).await
}

/// A Gmail account's own labels and how many messages carry each: its lanes in the rail.
async fn labels(State(app): State<Arc<App>>, method: Method, uri: Uri) -> Response<Body> {
    let account = query(&uri).text("account").map(str::to_owned);
    super::run(&app, &method, &uri, move |app| app.labels.of_account(account.as_deref())).await
}

/// A folder's change: its body read, then `work` on the blocking pool. A change a read-only process may not make is
/// the read-only 409, as the Python app answers `profile.ReadOnlyError`.
async fn folder_change(
    app: Arc<App>,
    request: Request<Body>,
    work: impl FnOnce(&App, Fields) -> Result<Value, Failure> + Send + 'static,
) -> Response<Body> {
    let (method, uri) = (request.method().clone(), request.uri().clone());
    let fields = match body::read(request).await {
        Ok(fields) => fields,
        Err(unread) => return unread.answer(),
    };
    let done = tokio::task::spawn_blocking(move || work(&app, fields)).await;
    match done.unwrap_or(Err(Failure::Fault(Fault::new("Panicked")))) {
        Ok(value) => answer::json(200, &value),
        Err(Failure::Fault(READ_ONLY)) => {
            let mut document = problem::blank(409, "MailMind is running read-only");
            document["type"] = json!("/problems/read-only");
            document["title"] = json!("Read-only");
            document["read_only"] = json!(true);
            answer::problem_document(409, &document)
        }
        Err(failure) => answer::failure(&failure, method.as_str(), uri.path()),
    }
}

/// A new folder at the top of an account's, by the name a person typed.
async fn folder_create(State(app): State<Arc<App>>, request: Request<Body>) -> Response<Body> {
    folder_change(app, request, |app, fields| {
        let (account, name) = (fields.text("account")?, fields.text("name")?);
        app.folder_changes.create(account, name)
    })
    .await
}

/// A folder called what a person typed, where it is.
async fn folder_rename(State(app): State<Arc<App>>, request: Request<Body>) -> Response<Body> {
    folder_change(app, request, |app, fields| {
        let (account, folder, name) = (fields.text("account")?, fields.text("folder")?, fields.text("name")?);
        app.folder_changes.rename(account, folder, name)
    })
    .await
}

/// A folder deleted: one holding mail only once a person has said so, its mail gone to Trash first.
async fn folder_delete(State(app): State<Arc<App>>, request: Request<Body>) -> Response<Body> {
    folder_change(app, request, |app, fields| {
        let (account, folder, confirm) = (fields.text("account")?, fields.text("folder")?, fields.flag("confirm")?);
        app.folder_changes.delete(account, folder, confirm == Some(true))
    })
    .await
}

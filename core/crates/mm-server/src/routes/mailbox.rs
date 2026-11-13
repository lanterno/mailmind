//! The Mailbox's routes (`mailmind.http.routers.mailbox`).

use std::sync::Arc;

use axum::Router;
use axum::body::Body;
use axum::extract::State;
use axum::http::{Method, Response, Uri};
use axum::routing::get;
use mm_kernel::errors::Problem;
use mm_mailbox::folders::AccountFolders;
use serde_json::{Value, json};

use crate::answer;
use crate::app::App;
use crate::query::Query;

pub(super) fn routes() -> Router<Arc<App>> {
    Router::new()
        .route("/api/folders", get(folders))
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
            json!({"name": f.name, "role": f.role, "unseen": f.unseen, "total": f.total, "takes_filing": takes_filing})
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

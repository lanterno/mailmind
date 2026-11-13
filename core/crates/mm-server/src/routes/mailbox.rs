//! The Mailbox's routes (`mailmind.http.routers.mailbox`).

use std::sync::Arc;

use axum::Router;
use axum::body::Body;
use axum::extract::State;
use axum::http::{Method, Response, Uri};
use axum::routing::get;
use mm_kernel::errors::{Failure, Problem};
use mm_mailbox::folders::AccountFolders;
use serde_json::{Value, json};

use crate::answer;
use crate::app::App;
use crate::query::Query;

pub(super) fn routes() -> Router<Arc<App>> {
    Router::new().route("/api/folders", get(folders)).route("/api/messages", get(messages))
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

/// A folder's messages. Only its arguments are read here so far: one that isn't a number is refused as the Python
/// app refuses it, and the list itself is not on the Rust server yet.
async fn messages(method: Method, uri: Uri) -> Response<Body> {
    let given = query(&uri);
    let read = given.number("offset", 0).and_then(|_| given.number("limit", 80));
    match read {
        Err(problem) => answer::failure(&Failure::Problem(problem), method.as_str(), uri.path()),
        Ok(_) => answer::not_yet(uri.path()),
    }
}

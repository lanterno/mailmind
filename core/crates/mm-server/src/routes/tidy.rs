//! Tidy's routes (`mailmind.http.routers.mailbox`'s `GET /api/tidy` and `POST /api/tidy/undo`): the newest run a
//! person can still take back, and taking a whole run back, which is the Mailbox's Undo of its group.

use std::sync::Arc;

use axum::Router;
use axum::body::Body;
use axum::extract::State;
use axum::http::{Method, Request, Response, Uri};
use axum::routing::{get, post};
use mm_kernel::errors::{Failure, Problem};

use crate::app::App;
use crate::body;
use crate::{answer, problem};

pub(super) fn routes() -> Router<Arc<App>> {
    Router::new().route("/api/tidy", get(tidy)).route("/api/tidy/undo", post(tidy_undo))
}

/// The newest run of Tidy that can still be taken back, or no group at all.
async fn tidy(State(app): State<Arc<App>>, method: Method, uri: Uri) -> Response<Body> {
    super::run(&app, &method, &uri, |app| Ok(app.tidying.latest()?)).await
}

/// Take a whole run back, wherever it stands on the undo stack, and say what went back as an Undo does. A group that
/// is no run of Tidy's is refused, so this reverses nothing else. A reversal that failed was logged, and is on the
/// stack again for another try.
async fn tidy_undo(State(app): State<Arc<App>>, request: Request<Body>) -> Response<Body> {
    let group = match body::read(request).await.map(|fields| fields.text("group").map(|g| g.map(str::to_owned))) {
        Ok(Ok(group)) => group,
        Ok(Err(refused)) => return answer::problem(&refused),
        Err(unread) => return unread.answer(),
    };
    if !mm_mailbox::tidy::is_run(group.as_deref()) {
        return answer::problem(&Problem::bad_request());
    }
    let done = tokio::task::spawn_blocking(move || {
        let done = app.undo.undo_group(group.as_deref().unwrap_or_default());
        if done.is_ok() {
            // Undo put messages back: the rail's badges are stale.
            let _ = app.folders.invalidate(None);
        }
        done
    })
    .await;
    match done {
        Ok(Ok(value)) => answer::json(200, &value),
        Ok(Err(Failure::Problem(no))) => answer::problem(&no),
        Ok(Err(Failure::Fault(_))) | Err(_) => {
            answer::problem_document(500, &problem::blank(500, "couldn\u{2019}t undo"))
        }
    }
}

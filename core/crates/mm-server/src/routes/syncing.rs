//! The Mailbox's sync routes (`mailmind.http.routers.mailbox`): a sync of every account, an account's sync asked
//! again, and the prune of mail gone from the servers.
//!
//! A sync a request sets going is answered at once and runs on as work of its own, which a stop and a test's
//! `settle` wait for.

use std::collections::BTreeSet;
use std::sync::Arc;

use axum::Router;
use axum::body::Body;
use axum::extract::State;
use axum::http::{Request, Response};
use axum::routing::post;
use serde_json::json;

use crate::app::App;
use crate::body;

pub(super) fn routes() -> Router<Arc<App>> {
    Router::new()
        .route("/api/refresh", post(refresh))
        .route("/api/sync-retry-account", post(sync_retry))
        .route("/api/reconcile", post(reconcile))
}

/// A sync of every account, set going.
async fn refresh(State(app): State<Arc<App>>, request: Request<Body>) -> Response<Body> {
    if let Err(unread) = body::read(request).await {
        return unread.answer();
    }
    let syncing = app.syncing.clone();
    app.work.spawn(move || syncing.sync_once());
    crate::answer::json(200, &json!({"ok": true, "msg": "sync started"}))
}

/// Clear an account's recorded sync error, and set its sync going.
async fn sync_retry(State(app): State<Arc<App>>, request: Request<Body>) -> Response<Body> {
    let (method, uri) = (request.method().clone(), request.uri().clone());
    let fields = match body::read(request).await {
        Ok(fields) => fields,
        Err(unread) => return unread.answer(),
    };
    super::run(&app.clone(), &method, &uri, move |held| {
        let account = held.syncing.retry(fields.text("account")?)?;
        let (syncing, named) = (held.syncing.clone(), BTreeSet::from([account.clone()]));
        app.work.spawn(move || syncing.sync_guarded(Some(named)));
        Ok(json!({"ok": true, "account": account}))
    })
    .await
}

/// Prune the account named, or every one, of mail gone from its server; a dry run only counts it.
async fn reconcile(State(app): State<Arc<App>>, request: Request<Body>) -> Response<Body> {
    let (method, uri) = (request.method().clone(), request.uri().clone());
    let fields = match body::read(request).await {
        Ok(fields) => fields,
        Err(unread) => return unread.answer(),
    };
    super::run(&app, &method, &uri, move |app| {
        let account = match fields.given("account") {
            None => Some("all"),
            Some(_) => fields.text("account")?,
        };
        let dry_run = fields.flag("dry_run")?.unwrap_or(false);
        app.reconciler.do_reconcile(account, dry_run)
    })
    .await
}

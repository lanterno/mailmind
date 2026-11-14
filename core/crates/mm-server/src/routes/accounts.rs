//! The accounts' routes (`mailmind.http.routers.accounts`). Each action names its path, so one that isn't an action
//! is no route.
//!
//! Accounts words its own refusals, and the page shows them, so a field one of them judges is taken as it came (`Any`
//! in the Python app's schemas, `taken` here): a schema's `bad request` would swallow the sentence.

use std::collections::BTreeSet;
use std::sync::Arc;

use axum::Router;
use axum::body::Body;
use axum::extract::State;
use axum::http::{HeaderMap, Method, Response, Uri};
use axum::routing::{get, post};
use mm_accounts::connections::Trusted;
use mm_kernel::errors::Problem;
use serde_json::{Map, Value, json};

use crate::app::App;
use crate::schema;

pub(super) fn routes() -> Router<Arc<App>> {
    Router::new()
        .route("/api/accounts", get(listed))
        .route("/api/account-health", get(account_health))
        .route("/api/accounts/test", post(test))
        .route("/api/accounts/trust-cert", post(trust_cert))
        .route("/api/accounts/add", post(add))
        .route("/api/accounts/edit", post(edit))
        .route("/api/accounts/delete", post(delete))
}

/// Every account as the API may show it, the default signature, and when this was said.
async fn listed(State(app): State<Arc<App>>, method: Method, uri: Uri) -> Response<Body> {
    super::run(&app, &method, &uri, |app| app.directory.listed()).await
}

/// How each account stands: its last sync that worked and its last that failed, and how its sync stands now.
async fn account_health(State(app): State<Arc<App>>, method: Method, uri: Uri) -> Response<Body> {
    super::run(&app, &method, &uri, |app| app.health.answer()).await
}

/// Try a saved account's login at its saved server, or a draft's where it was typed (`TryBody`).
async fn test(State(app): State<Arc<App>>, method: Method, uri: Uri, headers: HeaderMap, body: Body) -> Response<Body> {
    let asked = match crate::body::object(uri.path(), &headers, body).await {
        Ok(body) => tried(&body),
        Err(refused) => return *refused,
    };
    super::run(&app, &method, &uri, move |app| app.connections.test(&asked?)).await
}

/// Trust the account's servers again, and sync it if it is enabled, which pins the certificate it meets now
/// (`NamesAnAccount`).
async fn trust_cert(
    State(app): State<Arc<App>>,
    method: Method,
    uri: Uri,
    headers: HeaderMap,
    body: Body,
) -> Response<Body> {
    let asked = match crate::body::object(uri.path(), &headers, body).await {
        Ok(body) => names_an_account(&body),
        Err(refused) => return *refused,
    };
    super::run(&app.clone(), &method, &uri, move |held| {
        let asked = asked?;
        match held.connections.trust_cert(asked.get("email").and_then(Value::as_str))? {
            Trusted::SyncAgain(email) => {
                let account = held.syncing.retry(Some(&email))?;
                held.logins.forget(&account);
                let (syncing, named) = (held.syncing.clone(), BTreeSet::from([account.clone()]));
                app.work.spawn(move || syncing.sync_guarded(Some(named)));
                Ok(json!({"ok": true, "account": account}))
            }
            Trusted::Waits(email) => Ok(json!({"ok": true, "account": email})),
        }
    })
    .await
}

/// Add an account (`AccountBody`).
async fn add(State(app): State<Arc<App>>, method: Method, uri: Uri, headers: HeaderMap, body: Body) -> Response<Body> {
    let asked = match crate::body::object(uri.path(), &headers, body).await {
        Ok(body) => account_body(&body),
        Err(refused) => return *refused,
    };
    super::run(&app, &method, &uri, move |app| app.connections.add(&asked?)).await
}

/// Change what the body names of an account (`AccountBody`).
async fn edit(State(app): State<Arc<App>>, method: Method, uri: Uri, headers: HeaderMap, body: Body) -> Response<Body> {
    let asked = match crate::body::object(uri.path(), &headers, body).await {
        Ok(body) => account_body(&body),
        Err(refused) => return *refused,
    };
    super::run(&app, &method, &uri, move |app| app.connections.edit(&asked?)).await
}

/// Remove an account, and its own Keychain item (`NamesAnAccount`).
async fn delete(
    State(app): State<Arc<App>>,
    method: Method,
    uri: Uri,
    headers: HeaderMap,
    body: Body,
) -> Response<Body> {
    let asked = match crate::body::object(uri.path(), &headers, body).await {
        Ok(body) => names_an_account(&body),
        Err(refused) => return *refused,
    };
    super::run(&app, &method, &uri, move |app| app.connections.delete(&asked?)).await
}

/// `Any`: the value as it came, for the use case to judge in its own words.
fn taken(value: &Value) -> Option<Value> {
    Some(value.clone())
}

/// `NamesAnAccount`: an address, or null.
fn names_an_account(body: &Map<String, Value>) -> Result<Map<String, Value>, Problem> {
    let mut dumped = Map::new();
    schema::optional(body, "email", schema::text, &mut dumped)?;
    Ok(dumped)
}

/// `AccountDraft`: an account not saved yet, its endpoint and its credential loose, as the draft's use case words
/// what it can't take.
fn account_draft(body: &Map<String, Value>) -> Result<Map<String, Value>, Problem> {
    let mut dumped = names_an_account(body)?;
    for name in ["type", "host", "port"] {
        schema::optional(body, name, taken, &mut dumped)?;
    }
    for name in ["starttls", "strip_spaces"] {
        schema::optional(body, name, schema::flag, &mut dumped)?;
    }
    schema::optional(body, "credential", taken, &mut dumped)?;
    Ok(dumped)
}

/// `TryBody`: a saved account by its address, or else a draft, an object of `AccountDraft`'s fields.
fn tried(body: &Map<String, Value>) -> Result<Map<String, Value>, Problem> {
    let mut dumped = names_an_account(body)?;
    let draft = |value: &Value| match value {
        Value::Object(draft) => account_draft(draft).ok().map(Value::Object),
        _ => None,
    };
    schema::optional(body, "draft", draft, &mut dumped)?;
    Ok(dumped)
}

/// `AccountBody`: an account to add, or what an edit changes.
fn account_body(body: &Map<String, Value>) -> Result<Map<String, Value>, Problem> {
    let mut dumped = account_draft(body)?;
    for name in ["alias", "signature", "trash"] {
        schema::optional(body, name, schema::text, &mut dumped)?;
    }
    for name in ["smtp_host", "smtp_port"] {
        schema::optional(body, name, taken, &mut dumped)?;
    }
    for name in ["smtp_starttls", "disabled"] {
        schema::optional(body, name, schema::flag, &mut dumped)?;
    }
    Ok(dumped)
}

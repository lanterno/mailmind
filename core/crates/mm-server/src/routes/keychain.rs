//! What a person keeps in the login Keychain from Settings, beside each account's own password: an API key for each AI
//! provider that takes one (#49) and the Proton Bridge's password, each kept with a POST and taken out with another
//! (`mailmind.http.routers.keychain`).
//!
//! A secret comes in once, in the body, and is held as a `Secret` from there, wiped once it is kept. No answer holds
//! it, its length or any part of it: the keys' answer says only which providers have one, the Bridge's whether one
//! is set. Keeping one writes nothing to a mail server, so a read-only process keeps it too, as it keeps an account's
//! typed password.

use std::sync::Arc;

use axum::Router;
use axum::body::Body;
use axum::extract::State;
use axum::http::{HeaderMap, Method, Response, Uri};
use axum::routing::{get, post};
use mm_kernel::ai_provider::AiProvider;
use mm_kernel::errors::{Failure, Problem};
use mm_kernel::seams::Secret;
use serde_json::{Map, Value, json};

use crate::app::App;
use crate::schema;

pub(super) fn routes() -> Router<Arc<App>> {
    Router::new()
        .route("/api/ai/key", get(api_key).post(set_api_key))
        .route("/api/ai/key/clear", post(clear_api_key))
        .route("/api/accounts/bridge-password", get(bridge_password).post(set_bridge_password))
        .route("/api/accounts/bridge-password/clear", post(clear_bridge_password))
}

/// The answer to each: whether one is set.
fn kept(set: bool) -> Value {
    json!({"ok": true, "set": set})
}

/// The secret a body gives as text under `name`, taken out of it whole, so it is wiped once kept; anything else is the
/// `bad request` (a `str` its schema requires).
fn typed(mut body: Map<String, Value>, name: &str) -> Result<Secret, Problem> {
    match body.remove(name) {
        Some(Value::String(typed)) => Ok(Secret::new(typed)),
        _ => Err(Problem::bad_request()),
    }
}

/// The keys' answer: each provider the config file names a key for, in the closed set's order (`KeysKept`).
fn keys_kept(app: &App) -> Result<Value, Failure> {
    let kept: Vec<&str> = app.directory.api_keys_kept()?.into_iter().map(AiProvider::id).collect();
    Ok(json!({"ok": true, "kept": kept}))
}

/// The provider a body names (`AiProvider`), or the `bad request`.
fn provider_of(body: &Map<String, Value>) -> Result<AiProvider, Problem> {
    let named = schema::required(body, "provider", |v| schema::choice(v, &AiProvider::IDS))?;
    named.as_str().and_then(AiProvider::parse).ok_or_else(Problem::bad_request)
}

/// Which AI providers the config file names a key for.
async fn api_key(State(app): State<Arc<App>>, method: Method, uri: Uri) -> Response<Body> {
    super::run(&app, &method, &uri, keys_kept).await
}

/// Keep the API key the body gives for its provider (`KeyBody`) in the login Keychain, and name it in the config file.
async fn set_api_key(
    State(app): State<Arc<App>>,
    method: Method,
    uri: Uri,
    headers: HeaderMap,
    body: Body,
) -> Response<Body> {
    let asked = match crate::body::object(uri.path(), &headers, body).await {
        Ok(body) => provider_of(&body).and_then(|provider| Ok((provider, typed(body, "key")?))),
        Err(refused) => return *refused,
    };
    super::run(&app, &method, &uri, move |app| {
        let (provider, key) = asked?;
        app.directory.set_api_key(provider, key)?;
        keys_kept(app)
    })
    .await
}

/// Take the API key of the provider the body names (`ProviderBody`) out of the login Keychain and the config file.
async fn clear_api_key(
    State(app): State<Arc<App>>,
    method: Method,
    uri: Uri,
    headers: HeaderMap,
    body: Body,
) -> Response<Body> {
    let provider = match crate::body::object(uri.path(), &headers, body).await {
        Ok(body) => provider_of(&body),
        Err(refused) => return *refused,
    };
    super::run(&app, &method, &uri, move |app| {
        app.directory.clear_api_key(provider?)?;
        keys_kept(app)
    })
    .await
}

/// Whether the login Keychain keeps the Bridge's password.
async fn bridge_password(State(app): State<Arc<App>>, method: Method, uri: Uri) -> Response<Body> {
    super::run(&app, &method, &uri, |app| Ok(kept(app.directory.bridge_password_set()?))).await
}

/// Keep the Bridge's password the body gives (`PasswordBody`) in the login Keychain.
async fn set_bridge_password(
    State(app): State<Arc<App>>,
    method: Method,
    uri: Uri,
    headers: HeaderMap,
    body: Body,
) -> Response<Body> {
    let password = match crate::body::object(uri.path(), &headers, body).await {
        Ok(body) => typed(body, "password"),
        Err(refused) => return *refused,
    };
    super::run(&app, &method, &uri, move |app| {
        app.connections.set_bridge_password(password?)?;
        Ok(kept(true))
    })
    .await
}

/// Take the Bridge's password out of the login Keychain (`NoBody`).
async fn clear_bridge_password(
    State(app): State<Arc<App>>,
    method: Method,
    uri: Uri,
    headers: HeaderMap,
    body: Body,
) -> Response<Body> {
    if let Err(refused) = crate::body::object(uri.path(), &headers, body).await {
        return *refused;
    }
    super::run(&app, &method, &uri, |app| {
        app.connections.clear_bridge_password()?;
        Ok(kept(false))
    })
    .await
}

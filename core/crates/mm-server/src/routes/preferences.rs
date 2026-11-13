//! A person's preferences: what the Settings page shows and changes, read and written through the Directory
//! (`mailmind.http.routers.preferences`).

use std::sync::Arc;

use axum::Router;
use axum::body::Body;
use axum::extract::State;
use axum::http::{HeaderMap, Method, Response, Uri};
use axum::routing::{get, post};
use mm_accounts::preferences::{self, Kind};
use mm_kernel::errors::Problem;
use serde_json::{Map, Value};

use crate::app::App;
use crate::schema;

pub(super) fn routes() -> Router<Arc<App>> {
    Router::new().route("/api/preferences", get(current).post(change)).route("/api/preferences/import", post(bring_in))
}

/// Every preference as it stands.
async fn current(State(app): State<Arc<App>>, method: Method, uri: Uri) -> Response<Body> {
    super::run(&app, &method, &uri, |app| app.directory.preferences()).await
}

/// What changes: what a request doesn't name keeps what it was, so two tabs can each change their own.
async fn change(
    State(app): State<Arc<App>>,
    method: Method,
    uri: Uri,
    headers: HeaderMap,
    body: Body,
) -> Response<Body> {
    let asked = match crate::body::object(uri.path(), &headers, body).await {
        Ok(body) => changed(&body),
        Err(refused) => return *refused,
    };
    super::run(&app, &method, &uri, move |app| app.directory.set_preferences(&asked?)).await
}

/// What a browser kept under its old keys, once, in the block's words.
async fn bring_in(
    State(app): State<Arc<App>>,
    method: Method,
    uri: Uri,
    headers: HeaderMap,
    body: Body,
) -> Response<Body> {
    let held = match crate::body::object(uri.path(), &headers, body).await {
        Ok(body) => schema::required(&body, "held", schema::object),
        Err(refused) => return *refused,
    };
    super::run(&app, &method, &uri, move |app| match held? {
        Value::Object(held) => app.directory.import_preferences(&held),
        _ => Err(Problem::bad_request().into()),
    })
    .await
}

/// `ChangedPreferences`: each preference the body names, as its schema reads it, in the schema's order.
fn changed(body: &Map<String, Value>) -> Result<Map<String, Value>, Problem> {
    let mut dumped = Map::new();
    for (name, kind) in preferences::changeable() {
        match kind {
            Kind::Seconds(_) => schema::optional(body, name, schema::number, &mut dumped)?,
            Kind::Choice(_, words) => schema::optional(body, name, |v| schema::choice(v, words), &mut dumped)?,
            Kind::Flag(_) => schema::optional(body, name, schema::flag, &mut dumped)?,
            Kind::Time(_) => schema::optional(body, name, schema::text, &mut dumped)?,
            Kind::Dollars => schema::optional(body, name, schema::dollars, &mut dumped)?,
        }
    }
    Ok(dumped)
}

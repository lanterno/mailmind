//! The places the page's URLs name, kept in the database so that no URL names anything of a person's
//! (`mailmind.http.routers.places`). Opening one is the page's own route, in `page`.

use std::sync::Arc;

use axum::Router;
use axum::body::Body;
use axum::extract::State;
use axum::http::{HeaderMap, Method, Response, Uri};
use axum::routing::post;
use mm_kernel::errors::Problem;
use serde_json::{Map, Value, json};

use crate::app::App;

pub(super) fn routes() -> Router<Arc<App>> {
    Router::new().route("/api/places", post(keep))
}

/// The places the page made since it last sent any. The store words what it won't keep: an id the page couldn't have
/// made, a route too long, or more at once than it takes.
async fn keep(State(app): State<Arc<App>>, method: Method, uri: Uri, headers: HeaderMap, body: Body) -> Response<Body> {
    let given = match crate::body::object(uri.path(), &headers, body).await {
        Ok(body) => places(&body),
        Err(refused) => return *refused,
    };
    super::run(&app, &method, &uri, move |app| {
        app.places.keep(&given?)?;
        Ok(json!({"ok": true}))
    })
    .await
}

/// `PlacesBody`: a list of places, each an id that is text and a route that is an object.
fn places(body: &Map<String, Value>) -> Result<Vec<(String, Value)>, Problem> {
    let Some(Value::Array(listed)) = body.get("places") else {
        return Err(Problem::bad_request());
    };
    listed
        .iter()
        .map(|place| match (place.get("id"), place.get("route")) {
            (Some(Value::String(id)), Some(route @ Value::Object(_))) => Ok((id.clone(), route.clone())),
            _ => Err(Problem::bad_request()),
        })
        .collect()
}

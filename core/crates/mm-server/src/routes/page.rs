//! The app's page, with this run's token in it, and its files (`mailmind.http.routers.static`). These open from a
//! typed URL or another site's link, so they are no part of the API.
//!
//! A place's own URL (`/p/<id>`) and the old `/next` links are not on the Rust server yet.

use std::sync::Arc;

use axum::Router;
use axum::body::Body;
use axum::extract::State;
use axum::http::{HeaderMap, HeaderValue, Method, Response, StatusCode, Uri, header};
use axum::routing::get;
use mm_kernel::errors::{Failure, Fault};

use crate::answer;
use crate::app::App;
use crate::page::{Asset, FOREVER};
use crate::perimeter;

/// The paths the page answers on: the app's own places. A `{*place}` matches no empty rest, so each place that
/// takes one is listed with its slash as well, as Starlette's `{place:path}` matches an empty one.
const PLACES: [&str; 10] = [
    "/",
    "/all",
    "/all/",
    "/all/{*place}",
    "/search",
    "/documents",
    "/m/",
    "/m/{*place}",
    "/brief/",
    "/brief/{*place}",
];

pub(super) fn routes() -> Router<Arc<App>> {
    let mut routes = Router::new();
    for place in PLACES {
        routes = routes.route(place, get(page));
    }
    routes.route("/assets/{*file}", get(asset))
}

/// The app, or how to make one: only a build holds it.
async fn page(State(app): State<Arc<App>>, method: Method, uri: Uri) -> Response<Body> {
    let built = {
        let app = app.clone();
        tokio::task::spawn_blocking(move || app.page.page(&app.csrf)).await.unwrap_or(Err(Fault::new("Panicked")))
    };
    match built {
        Ok(Some(html)) => {
            let mut answer = answer::bytes(200, "text/html; charset=utf-8", html.into_bytes());
            let headers = answer.headers_mut();
            headers.insert(header::CONTENT_SECURITY_POLICY, HeaderValue::from_static(perimeter::page_policy()));
            // It holds this run's token.
            headers.insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
            answer
        }
        Ok(None) => {
            answer::bytes(404, "text/plain; charset=utf-8", b"The app isn't built. Run: just build-web\n".to_vec())
        }
        Err(fault) => answer::failure(&Failure::Fault(fault), method.as_str(), uri.path()),
    }
}

/// One of the page's files, by ETag, compressed for a browser that takes it.
async fn asset(State(app): State<Arc<App>>, method: Method, uri: Uri, headers: HeaderMap) -> Response<Body> {
    let path = uri.path().to_owned();
    let found = tokio::task::spawn_blocking(move || app.page.asset(&path)).await.unwrap_or(Err(Fault::new("Panicked")));
    match found {
        Ok(Some(asset)) => served(&asset, &headers),
        Ok(None) => answer::bytes(404, "text/plain", b"not found".to_vec()),
        Err(fault) => answer::failure(&Failure::Fault(fault), method.as_str(), uri.path()),
    }
}

fn served(asset: &Asset, asked: &HeaderMap) -> Response<Body> {
    let etag = HeaderValue::from_str(&asset.etag).unwrap_or(HeaderValue::from_static(""));
    let mut answer = if asked.get(header::IF_NONE_MATCH).is_some_and(|tag| tag.as_bytes() == asset.etag.as_bytes()) {
        let mut answer = Response::new(Body::empty());
        *answer.status_mut() = StatusCode::NOT_MODIFIED;
        answer
    } else {
        let takes_gzip =
            asked.get(header::ACCEPT_ENCODING).is_some_and(|said| said.as_bytes().windows(4).any(|w| w == b"gzip"));
        match (&asset.gzipped, takes_gzip) {
            (Some(gzipped), true) => {
                let mut answer = answer::bytes(200, asset.content_type, gzipped.clone());
                answer.headers_mut().insert(header::CONTENT_ENCODING, HeaderValue::from_static("gzip"));
                answer
            }
            _ => answer::bytes(200, asset.content_type, asset.body.clone()),
        }
    };
    let headers = answer.headers_mut();
    headers.insert(header::ETAG, etag);
    headers.insert(header::CACHE_CONTROL, HeaderValue::from_static(FOREVER));
    headers.insert(header::VARY, HeaderValue::from_static("Accept-Encoding"));
    answer
}

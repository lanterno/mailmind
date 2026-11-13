//! The app's page, with this run's token in it, and its files (`mailmind.http.routers.static`). These open from a
//! typed URL or another site's link, so they are no part of the API.
//!
//! A place's own URL (`/p/<id>`) opens the app holding the route the place keeps (`mm_store::places`), and a link
//! from when the app lived at `/next` goes to where it went.

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
    routes
        .route("/p/{place}", get(place))
        .route("/next", get(moved))
        .route("/next/", get(moved))
        .route("/next/{*rest}", get(moved))
        .route("/assets/{*file}", get(asset))
}

/// The app, or how to make one: only a build holds it.
async fn page(State(app): State<Arc<App>>, method: Method, uri: Uri) -> Response<Body> {
    let built = tokio::task::spawn_blocking(move || app.page.page(&app.csrf, None));
    the_app(built.await.unwrap_or(Err(Fault::new("Panicked"))), &method, &uri)
}

/// The app at a place, holding the route its id names, which opening it marks used. An id that names none, or no
/// longer does, is the app with no place, which opens at home. The id is the path's last part as sent.
async fn place(State(app): State<Arc<App>>, method: Method, uri: Uri) -> Response<Body> {
    let place = uri.path().strip_prefix("/p/").unwrap_or_default().to_owned();
    let built = tokio::task::spawn_blocking(move || {
        if !app.page.is_built() {
            return Ok(None);
        }
        let route = app.places.opened(&place)?;
        app.page.page(&app.csrf, route.as_deref())
    });
    the_app(built.await.unwrap_or(Err(Fault::new("Panicked"))), &method, &uri)
}

/// A place in the app, as `app/route.ts` writes one (`/p/<id>` for one that holds anything personal) or reads one
/// typed.
fn is_place(path: &str) -> bool {
    matches!(path, "/all" | "/search" | "/documents")
        || ["/m/", "/brief/", "/all/", "/p/"].iter().any(|start| path.starts_with(start))
}

/// A link from when the app lived at /next. Only one of its own places goes on the redirect, so no path can send a
/// browser anywhere else (`/next//elsewhere.example` would, as a path starting `//` names a host). The path and the
/// query go on as they were sent.
async fn moved(uri: Uri) -> Response<Body> {
    let rest = uri.path().strip_prefix("/next").unwrap_or_default();
    let mut whither = if is_place(rest) { rest.to_owned() } else { "/".to_owned() };
    if let Some(query) = uri.query().filter(|q| !q.is_empty()) {
        whither.push('?');
        whither.push_str(query);
    }
    let mut answer = answer::bytes(302, "text/plain", Vec::new());
    // A request's target is visible ASCII, so it is a header's value as it stands.
    if let Ok(location) = HeaderValue::from_str(&whither) {
        answer.headers_mut().insert(header::LOCATION, location);
    }
    answer
}

/// The page built, or how to make one, or the failure.
fn the_app(built: Result<Option<String>, Fault>, method: &Method, uri: &Uri) -> Response<Body> {
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

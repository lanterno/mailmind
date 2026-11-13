//! Discovery's routes (`mailmind.http.routers.discovery`): search, saved views, contacts and who you know.

use std::sync::Arc;

use axum::Router;
use axum::body::Body;
use axum::extract::{Request, State};
use axum::http::{Method, Response, Uri};
use axum::routing::get;
use mm_discovery::search::{Asked, Search};
use mm_discovery::views::Change;
use mm_kernel::errors::{Failure, Problem};
use serde_json::Value;

use crate::answer;
use crate::app::App;
use crate::posted::{self, Posted};
use crate::query::Query;

pub(super) fn routes() -> Router<Arc<App>> {
    Router::new()
        .route("/api/search", get(search))
        .route("/api/suggest", get(suggest))
        .route("/api/views", get(views).post(views_post))
        .route("/api/contacts", get(contacts))
        .route("/api/contact", get(contact))
        .route("/api/contacts/known", axum::routing::post(known))
}

fn query(uri: &Uri) -> Query {
    Query::parse(uri.query().unwrap_or_default().as_bytes())
}

fn refused(problem: Problem, method: &Method, uri: &Uri) -> Response<Body> {
    answer::failure(&Failure::Problem(problem), method.as_str(), uri.path())
}

/// Local full-text and operator search across the synced mail. A limit or an offset that won't read is refused only
/// once something has been typed, so an empty box answers whatever else the request holds.
async fn search(State(app): State<Arc<App>>, method: Method, uri: Uri) -> Response<Body> {
    let given = query(&uri);
    let q = mm_kernel::text::strip(given.text("q").unwrap_or_default()).to_owned();
    if q.is_empty() {
        return answer::json(200, &Search::nothing_asked());
    }
    let read = given.number("limit", 80).and_then(|limit| Ok((limit, given.number("offset", 0)?)));
    let (limit, offset) = match read {
        Ok(read) => read,
        Err(problem) => return refused(problem, &method, &uri),
    };
    let asked = Asked::new(&q, given.text("scope"), given.text("account"), limit, offset, given.text("thread"));
    super::run(&app, &method, &uri, move |app| app.discovery.search.answer(&asked)).await
}

/// What the search box offers for the word being typed.
async fn suggest(State(app): State<Arc<App>>, method: Method, uri: Uri) -> Response<Body> {
    let q = query(&uri).text("q").map(str::to_owned);
    super::run(&app, &method, &uri, move |app| app.discovery.suggesting.answer(q.as_deref())).await
}

/// The saved views, the built-in ones first.
async fn views(State(app): State<Arc<App>>, method: Method, uri: Uri) -> Response<Body> {
    super::run(&app, &method, &uri, |app| app.discovery.views.listed()).await
}

fn view_change(body: &Posted) -> Result<Change, Problem> {
    let verb = body.one_of("verb", &["create", "delete"])?;
    let (id, name, q) = (body.text("id")?, body.text("name")?, body.text("q")?);
    Ok(match verb.as_deref() {
        Some("create") => Change::Create { name, q },
        Some("delete") => Change::Delete { id },
        _ => Change::Other,
    })
}

/// Make a view or delete one, answering the views after.
async fn views_post(State(app): State<Arc<App>>, request: Request) -> Response<Body> {
    let (method, uri) = (request.method().clone(), request.uri().clone());
    let change = match posted::read(request).await.map(|body| view_change(&body)) {
        Ok(Ok(change)) => change,
        Ok(Err(problem)) => return refused(problem, &method, &uri),
        Err(answer) => return *answer,
    };
    super::run(&app, &method, &uri, move |app| app.discovery.views.post(change)).await
}

/// Everyone who has written to you, for autocomplete.
async fn contacts(State(app): State<Arc<App>>, method: Method, uri: Uri) -> Response<Body> {
    let q = query(&uri).text("q").map(str::to_owned);
    super::run(&app, &method, &uri, move |app| app.discovery.contacts.autocomplete(q.as_deref())).await
}

/// One sender's card.
async fn contact(State(app): State<Arc<App>>, method: Method, uri: Uri) -> Response<Body> {
    let addr = query(&uri).text("addr").map(str::to_owned);
    super::run(&app, &method, &uri, move |app| app.discovery.contacts.card(addr.as_deref())).await
}

/// Which recipients you've exchanged mail with. Read-only. A failure is logged by class alone: the request is a list
/// of addresses.
async fn known(State(app): State<Arc<App>>, request: Request) -> Response<Body> {
    let (method, uri) = (request.method().clone(), request.uri().clone());
    let addrs: Option<Vec<Value>> =
        match posted::read(request).await.map(|body| body.list("addrs").map(|a| a.map(<[Value]>::to_vec))) {
            Ok(Ok(addrs)) => addrs,
            Ok(Err(problem)) => return refused(problem, &method, &uri),
            Err(answer) => return *answer,
        };
    let answered = super::run(&app, &method, &uri, move |app| app.discovery.known.ask(addrs.as_deref())).await;
    if answered.status() == 500 {
        return answer::problem_document(500, &crate::problem::blank(500, "lookup failed"));
    }
    answered
}

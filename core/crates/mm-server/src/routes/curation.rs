//! Curation's routes (`mailmind.http.routers.curation`): the rules, filters, the Screener and the audit trail.

use std::sync::Arc;

use axum::Router;
use axum::body::Body;
use axum::extract::{Request, State};
use axum::http::{Method, Response, Uri};
use axum::routing::{get, post};
use mm_curation::filters::Change;
use mm_curation::screener::Ruled;
use mm_kernel::errors::{Failure, Fault, Problem};

use crate::answer;
use crate::app::App;
use crate::posted::{self, Posted};
use crate::query::Query;

pub(super) fn routes() -> Router<Arc<App>> {
    Router::new()
        .route("/api/rules", get(rules).post(rule))
        .route("/api/filters", post(filters))
        .route("/api/audit", get(audit))
        .route("/api/screener", get(screener))
}

fn query(uri: &Uri) -> Query {
    Query::parse(uri.query().unwrap_or_default().as_bytes())
}

fn refused(problem: Problem, method: &Method, uri: &Uri) -> Response<Body> {
    answer::failure(&Failure::Problem(problem), method.as_str(), uri.path())
}

/// The keep and block lists, each sender's shelf life and the filters.
async fn rules(State(app): State<Arc<App>>, method: Method, uri: Uri) -> Response<Body> {
    super::run(&app, &method, &uri, |app| app.curation.screener.listed()).await
}

/// What a rule's request says: the token, the verdict, and the copy a block trashes too, as its key, folder and uid.
struct RuleAsked {
    token: Option<String>,
    verdict: Option<String>,
    copy: Option<(i64, String, Option<i64>)>,
}

fn rule_asked(body: &Posted) -> Result<RuleAsked, Problem> {
    let (key, folder, uid) = (body.number("key")?, body.text("folder")?, body.number("uid")?);
    let token = body.text("token")?;
    let verdict = body.one_of("verdict", &["keep", "block", "remove"])?;
    Ok(RuleAsked { token, verdict, copy: key.map(|key| (key, folder.unwrap_or_default(), uid)) })
}

/// A keep, block or remove on a sender or a domain. A block that names a copy trashes it too: that is a change on its
/// mail server, so the rule is saved and the rest answered as not yet served.
async fn rule(State(app): State<Arc<App>>, request: Request) -> Response<Body> {
    let (method, uri) = (request.method().clone(), request.uri().clone());
    let asked = match posted::read(request).await.map(|body| rule_asked(&body)) {
        Ok(Ok(asked)) => asked,
        Ok(Err(problem)) => return refused(problem, &method, &uri),
        Err(answer) => return *answer,
    };
    let work = {
        let app = app.clone();
        move || -> Result<Ruled, Failure> {
            let curation = &app.curation;
            // A copy that isn't there any more is no pk, and the rule still stands.
            let pk = match &asked.copy {
                Some((key, folder, uid)) => curation.copy_pk(*key, folder, *uid)?,
                None => None,
            };
            curation.screener.rule(asked.token.as_deref(), asked.verdict.as_deref(), pk)
        }
    };
    let done = tokio::task::spawn_blocking(work).await;
    match done.unwrap_or(Err(Failure::Fault(Fault::new("Panicked")))) {
        Ok(Ruled::Done(value)) => answer::json(200, &value),
        Ok(Ruled::TrashNotYet) => answer::not_yet(uri.path()),
        Err(failure) => answer::failure(&failure, method.as_str(), uri.path()),
    }
}

fn filter_change(body: &Posted) -> Result<Change, Problem> {
    let verb = body.one_of("verb", &["create", "update", "delete"])?;
    let (id, q) = (body.text("id")?, body.text("q")?);
    let (star, read) = (body.flag("star")?.unwrap_or(false), body.flag("read")?.unwrap_or(false));
    Ok(match verb.as_deref() {
        Some("create") => Change::Create { q, star, read },
        Some("update") => Change::Update { id, q, star, read },
        Some("delete") => Change::Delete { id },
        _ => Change::Other,
    })
}

/// Make, change or delete a filter, answering the filters after.
async fn filters(State(app): State<Arc<App>>, request: Request) -> Response<Body> {
    let (method, uri) = (request.method().clone(), request.uri().clone());
    let change = match posted::read(request).await.map(|body| filter_change(&body)) {
        Ok(Ok(change)) => change,
        Ok(Err(problem)) => return refused(problem, &method, &uri),
        Err(answer) => return *answer,
    };
    super::run(&app, &method, &uri, move |app| app.curation.filters.post(change)).await
}

/// The newest records of the audit trails, newest first.
async fn audit(State(app): State<Arc<App>>, method: Method, uri: Uri) -> Response<Body> {
    let limit = match query(&uri).number("limit", 120) {
        Ok(limit) => limit,
        Err(problem) => return refused(problem, &method, &uri),
    };
    super::run(&app, &method, &uri, move |app| app.curation.audit.read(limit)).await
}

/// The first-contact gate: the account's Inbox senders no rule decides yet.
async fn screener(State(app): State<Arc<App>>, method: Method, uri: Uri) -> Response<Body> {
    let account = query(&uri).text("account").map(str::to_owned);
    super::run(&app, &method, &uri, move |app| app.curation.screener.queue(account.as_deref())).await
}

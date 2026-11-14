//! What the page asks of the calendar (`mailmind.http.routers.calendar`): Today & next, an answer to an invite, and
//! a file for an event someone confirmed.
//!
//! A POST names its message by its key, and the copy the page has by its folder and uid (`named.with_message`): a key
//! with no copy left is the `not found` 404 before the use case runs. An answer this server can't send yet answers
//! as not yet served, having refused what the Python app refuses.

use std::sync::Arc;

use axum::Router;
use axum::body::Body;
use axum::extract::State;
use axum::http::{Method, Request, Response, Uri};
use axum::routing::{get, post};
use mm_calendar::{publish, rsvp};
use mm_kernel::errors::{Failure, Fault, NOT_YET, Problem};
use mm_kernel::log;
use serde_json::Value;

use crate::app::App;
use crate::body::{self, Fields};
use crate::{answer, problem};

const TAG: &str = "rsvp";
/// What a person may answer an invite (`RsvpDecision`).
const DECISIONS: [&str; 3] = ["accept", "decline", "tentative"];

pub(super) fn routes() -> Router<Arc<App>> {
    Router::new()
        .route("/api/upcoming", get(upcoming))
        .route("/api/invite/rsvp", post(invite_rsvp))
        .route("/api/calendar/event", post(calendar_event))
}

/// What names a message: its key, and the folder and uid of the copy the page has.
type Named = (Option<i64>, Option<String>, Option<i64>);

fn named(fields: &Fields, key: Option<i64>) -> Result<Named, Problem> {
    Ok((key, fields.text("folder")?.map(str::to_owned), fields.number("uid")?))
}

/// The pk of a copy of the message named, None where it names no key; a key with no copy left is `not found`.
fn with_message(app: &App, (key, folder, uid): Named) -> Result<Option<i64>, Failure> {
    let Some(key) = key else { return Ok(None) };
    match app.calendar.message_pk(key, folder.as_deref(), uid)? {
        Some(pk) => Ok(Some(pk)),
        None => Err(Failure::Problem(Problem::not_found("not found"))),
    }
}

/// A POST's body read and decoded, or the answer that refuses it.
async fn decoded<T>(
    request: Request<Body>,
    decode: impl FnOnce(&Fields) -> Result<T, Problem>,
) -> Result<T, Box<Response<Body>>> {
    match body::read(request).await {
        Ok(fields) => decode(&fields).map_err(|refused| Box::new(answer::problem(&refused))),
        Err(unread) => Err(Box::new(unread.answer())),
    }
}

/// The events in invites of the last sixty days of mail, soonest first: read-only, nothing fetched.
async fn upcoming(State(app): State<Arc<App>>, method: Method, uri: Uri) -> Response<Body> {
    super::run(&app, &method, &uri, |app| Ok(app.calendar.agenda.upcoming()?)).await
}

/// An answer to an invite, to its organizer through the outbox. Whatever fails that isn't a refusal is the route's
/// own 500, logged by its class alone: a send route never shows what an error said.
async fn invite_rsvp(State(app): State<Arc<App>>, request: Request<Body>) -> Response<Body> {
    let uri = request.uri().clone();
    let asked = decoded(request, |fields| {
        let message = named(fields, Some(fields.required_number("key")?))?;
        Ok((message, fields.one_of("decision", &DECISIONS)?.map(str::to_owned)))
    })
    .await;
    let (message, decision) = match asked {
        Ok(asked) => asked,
        Err(refused) => return *refused,
    };
    let done = tokio::task::spawn_blocking(move || {
        let pk = with_message(&app, message)?;
        app.calendar.rsvp.respond(&rsvp::Asked { pk, decision })
    })
    .await;
    match done.unwrap_or(Err(Failure::Fault(Fault::new("Panicked")))) {
        Ok(value) => answer::json(200, &value),
        Err(Failure::Problem(refused)) => answer::problem(&refused),
        Err(Failure::Fault(NOT_YET)) => answer::not_yet(uri.path()),
        Err(Failure::Fault(fault)) => {
            log::error(TAG, "error", &[], Some(fault.class));
            answer::problem_document(500, &problem::blank(500, "RSVP failed"))
        }
    }
}

/// A file for the person's calendar, of an event they confirmed from a message: nothing is sent or made.
async fn calendar_event(State(app): State<Arc<App>>, request: Request<Body>) -> Response<Body> {
    let (method, uri) = (request.method().clone(), request.uri().clone());
    let asked = decoded(request, |fields| {
        let text = |name: &str| fields.text(name).map(|t| t.map(str::to_owned));
        let message = named(fields, fields.number("key")?)?;
        Ok((message, text("title")?, text("start")?, text("end")?, text("place")?))
    })
    .await;
    let (message, title, start, end, place) = match asked {
        Ok(asked) => asked,
        Err(refused) => return *refused,
    };
    super::run(&app, &method, &uri, move |app| -> Result<Value, Failure> {
        let pk = with_message(app, message)?;
        app.calendar.publishing.event(&publish::Asked { pk, title, start, end, place })
    })
    .await
}

//! Compose's routes (`mailmind.http.routers.compose`): a send, a draft saved, discarded or opened back into the
//! composer, the composer's preview, the outbox tray and what it asks of a send (Undo Send, Retry, Send now), and the
//! snippets.

use std::collections::BTreeSet;
use std::sync::Arc;

use axum::Router;
use axum::body::Body;
use axum::extract::{Request, State};
use axum::http::{HeaderMap, Method, Response, Uri};
use axum::routing::{get, post};
use mm_compose::attachments::PICTURES;
use mm_compose::composing::Composition;
use mm_kernel::errors::{Failure, Fault, Kind, Problem};
use mm_kernel::log::{self, Field};
use serde_json::{Map, Value, json};

use super::reading::query;
use crate::answer;
use crate::app::App;
use crate::body::{self, Fields};
use crate::problem;
use crate::schema;

pub(super) fn routes() -> Router<Arc<App>> {
    Router::new()
        .route("/api/outbox", get(outbox))
        .route("/api/snippets", get(snippets).post(snippets_post))
        .route("/api/send", post(send))
        .route("/api/draft", post(draft))
        .route("/api/draft/resume", post(draft_resume))
        .route("/api/compose/preview", post(compose_preview))
        .route("/api/outbox/retry", post(outbox_retry))
        .route("/api/outbox/cancel", post(outbox_cancel))
        .route("/api/outbox/sendnow", post(outbox_send_now))
}

/// The newest sends, `limit` of them (50 unless asked), and the drafts whose saves are overdue.
async fn outbox(State(app): State<Arc<App>>, method: Method, uri: Uri) -> Response<Body> {
    let limit = match query(&uri).number("limit", 50) {
        Ok(limit) => limit,
        Err(problem) => return answer::failure(&Failure::Problem(problem), method.as_str(), uri.path()),
    };
    super::reading::run(&app, &method, &uri, move |app| Ok(app.reading.outbox.tray(limit)?)).await
}

/// Every snippet kept.
async fn snippets(State(app): State<Arc<App>>, method: Method, uri: Uri) -> Response<Body> {
    super::run(&app, &method, &uri, |app| app.snippets.listed()).await
}

/// A snippet saved or deleted, and the snippets after it.
async fn snippets_post(
    State(app): State<Arc<App>>,
    method: Method,
    uri: Uri,
    headers: HeaderMap,
    body: Body,
) -> Response<Body> {
    let asked = match crate::body::object(uri.path(), &headers, body).await {
        Ok(body) => snippet_change(&body),
        Err(refused) => return *refused,
    };
    super::run(&app, &method, &uri, move |app| app.snippets.post(&asked?)).await
}

/// `SnippetChange`: a verb, save or delete (left out is the use case's to word), a trigger and a text.
fn snippet_change(body: &Map<String, Value>) -> Result<Map<String, Value>, Problem> {
    let mut dumped = Map::new();
    schema::optional(body, "verb", |v| schema::choice(v, &["save", "delete"]), &mut dumped)?;
    schema::optional(body, "trigger", schema::text, &mut dumped)?;
    schema::optional(body, "text", schema::text, &mut dumped)?;
    Ok(dumped)
}

const TAG: &str = "send";
const RESUME_TAG: &str = "resume";

/// A POST's answer: its body read, then `work` on the blocking pool. A fault answers the fixed 500, or, where the
/// route words its own (`said`), a 500 in those words.
async fn posted(
    app: Arc<App>,
    request: Request<Body>,
    said: Option<(&'static str, &'static str)>,
    work: impl FnOnce(&App, Fields) -> Result<Value, Failure> + Send + 'static,
) -> Response<Body> {
    let (method, uri) = (request.method().clone(), request.uri().clone());
    let fields = match body::read(request).await {
        Ok(fields) => fields,
        Err(unread) => return unread.answer(),
    };
    let Some((tag, said)) = said else {
        return super::run(&app, &method, &uri, move |app| work(app, fields)).await;
    };
    let app = app.clone();
    let done = tokio::task::spawn_blocking(move || work(&app, fields)).await;
    match done.unwrap_or(Err(Failure::Fault(Fault::new("Panicked")))) {
        Ok(value) => answer::json(200, &value),
        Err(Failure::Problem(p)) => answer::problem(&p),
        Err(Failure::Fault(fault)) => {
            log::error(tag, "error", &[(Field::Path, uri.path())], Some(fault.class));
            answer::problem_document(500, &problem::blank(500, said))
        }
    }
}

const MODES: [&str; 4] = ["new", "reply", "replyall", "forward"];

/// `Recipients`: a string, a list of strings, or nothing. A string is the one recipient it names.
fn recipients(fields: &Fields, name: &str) -> Result<Vec<String>, Problem> {
    match fields.given(name) {
        None | Some(Value::Null) => Ok(Vec::new()),
        Some(Value::String(one)) => Ok(if one.is_empty() { Vec::new() } else { vec![one.clone()] }),
        Some(Value::Array(items)) => {
            items.iter().map(|item| item.as_str().map(str::to_owned).ok_or_else(Problem::bad_request)).collect()
        }
        Some(_) => Err(Problem::bad_request()),
    }
}

/// `Composition`, and a send's and a save's own fields, as the request gave them once its schema let it through.
fn composition(fields: &Fields) -> Result<(Composition, Option<i64>), Problem> {
    let attachments = match fields.given("attachments") {
        None | Some(Value::Null) => None,
        Some(Value::Array(items)) => Some(items.clone()),
        Some(_) => return Err(Problem::bad_request()),
    };
    let resume = match fields.given("resume") {
        None | Some(Value::Null) => None,
        Some(Value::Object(fields)) => Some(fields.clone()),
        Some(_) => return Err(Problem::bad_request()),
    };
    let mode = fields.one_of("mode", &MODES)?.and_then(|m| MODES.iter().find(|known| **known == m).copied());
    let forward_attachments = match fields.given("forward_attachments") {
        None => None,
        // Said null, `body.get("forward_attachments", True)` reads it as no.
        Some(Value::Null) => Some(false),
        Some(_) => fields.flag("forward_attachments")?,
    };
    let asked = Composition {
        account: fields.text("account")?.map(str::to_owned),
        mode,
        to: recipients(fields, "to")?,
        cc: recipients(fields, "cc")?,
        bcc: recipients(fields, "bcc")?,
        subject: fields.text("subject")?.map(str::to_owned),
        text: fields.text("text")?.map(str::to_owned),
        attachments,
        resume,
        in_reply_to_pk: None,
        forward_attachments,
        draft_id: fields.number("draft_id")?,
        idempotency_key: fields.text("idempotency_key")?.map(str::to_owned),
        send_after: fields.number("send_after")?,
        delete: fields.flag("delete")?,
        autosave: fields.flag("autosave")?,
        base_version: fields.number("base_version")?,
    };
    Ok((asked, fields.number("original_key")?))
}

/// A send's or a save's fields, with `in_reply_to_pk` for a copy of the original its `original_key` names (`composed`).
/// One that has gone stops a reply or forward from being written, but not a reopened draft's save or send, which
/// carries its own quote and threading.
fn composed(app: &App, fields: &Fields) -> Result<Composition, Failure> {
    let (mut asked, original_key) = composition(fields)?;
    if let Some(key) = original_key {
        let pk = app.compose.message_pk(key)?;
        if pk.is_none() && asked.resume.is_none() && matches!(asked.mode, Some("reply" | "replyall" | "forward")) {
            return Err(Problem::not_found("original message not found").into());
        }
        asked.in_reply_to_pk = pk;
    }
    Ok(asked)
}

/// A message built and queued in the outbox: its id, and the seconds Undo Send has.
async fn send(State(app): State<Arc<App>>, request: Request<Body>) -> Response<Body> {
    posted(app, request, Some((TAG, "send failed")), |app, fields| {
        // The schema's refusals first: `send_after` and the rest are judged before anything is looked up.
        let asked = composed(app, &fields)?;
        app.compose.sending.send(&asked)
    })
    .await
}

/// A draft saved, locally first and on its server after; or, `delete`, discarded.
async fn draft(State(app): State<Arc<App>>, request: Request<Body>) -> Response<Body> {
    posted(app, request, None, |app, fields| {
        let asked = composed(app, &fields)?;
        app.compose.drafting.save(&asked)
    })
    .await
}

/// A saved draft, back into the composer: by its id, or a copy of it in Drafts by its key, folder and uid.
async fn draft_resume(State(app): State<Arc<App>>, request: Request<Body>) -> Response<Body> {
    posted(app, request, Some((RESUME_TAG, "Couldn\u{2019}t open this draft.")), |app, fields| {
        let (key, folder, uid, draft_id) =
            (fields.number("key")?, fields.text("folder")?, fields.number("uid")?, fields.number("draft_id")?);
        let pk = match key {
            None => None,
            Some(key) => Some(
                app.compose
                    .copy_pk(key, folder.unwrap_or_default(), uid)?
                    .ok_or_else(|| Problem::not_found("That draft is no longer in Drafts."))?,
            ),
        };
        app.compose.drafting.resume(draft_id, pk)
    })
    .await
}

/// `PreviewPicture`: a file the message carries in its text, by its cid, of a type a picture can be.
fn preview_picture(fields: &Fields) -> Result<String, Problem> {
    let cid = fields.required_text("cid")?.to_owned();
    fields.one_of("content_type", &PICTURES)?.ok_or_else(Problem::bad_request)?;
    Ok(cid)
}

/// What the person typed, as it would go out. It reads and writes nothing.
async fn compose_preview(State(app): State<Arc<App>>, request: Request<Body>) -> Response<Body> {
    posted(app, request, None, |app, fields| {
        let text = fields.text("text")?.unwrap_or_default().to_owned();
        let pictures: BTreeSet<String> =
            fields.list("pictures", preview_picture)?.unwrap_or_default().into_iter().collect();
        Ok(app.compose.composing.preview(&text, &pictures))
    })
    .await
}

/// A send in error back to the queue, or one found in Sent already marked sent.
async fn outbox_retry(State(app): State<Arc<App>>, request: Request<Body>) -> Response<Body> {
    posted(app, request, None, |app, fields| {
        let id = fields.required_number("id")?;
        let answer = app.compose.delivery.retry(id)?;
        if answer.get("ok") != Some(&Value::Bool(true)) {
            return Err(Problem::not_found("").into());
        }
        Ok(answer)
    })
    .await
}

/// Undo Send: the send dropped while it is still in its window.
async fn outbox_cancel(State(app): State<Arc<App>>, request: Request<Body>) -> Response<Body> {
    posted(app, request, None, |app, fields| {
        let id = fields.required_number("id")?;
        if !app.compose.delivery.cancel(id)? {
            return Err(Problem::new(Kind::Conflict, "too late to cancel").into());
        }
        Ok(json!({"ok": true}))
    })
    .await
}

/// A scheduled send brought forward to now.
async fn outbox_send_now(State(app): State<Arc<App>>, request: Request<Body>) -> Response<Body> {
    posted(app, request, None, |app, fields| {
        let id = fields.required_number("id")?;
        if !app.compose.delivery.send_now(id)? {
            return Err(Problem::not_found("").into());
        }
        Ok(json!({"ok": true}))
    })
    .await
}

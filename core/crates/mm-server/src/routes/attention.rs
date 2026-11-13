//! Attention's routes (`mailmind.http.routers.attention`): snooze, mute, follow-ups, Reply Later and tasks, and who you
//! wait on (`GET /api/waiting-on-others`, the Mailbox router's in the Python app, over Attention's table alone).

use std::sync::Arc;

use axum::Router;
use axum::body::Body;
use axum::extract::{Request, State};
use axum::http::{Method, Response, Uri};
use axum::routing::{get, post};
use mm_attention::snoozing::{BULK_CAP, PRESETS, Target, Until};
use mm_attention::threads::Named;
use mm_kernel::errors::{Failure, Fault, Problem};
use serde_json::Value;

use crate::answer;
use crate::app::App;
use crate::posted::{self, Posted, as_int};

pub(super) fn routes() -> Router<Arc<App>> {
    Router::new()
        .route("/api/snooze", post(snooze))
        .route("/api/unsnooze", post(unsnooze))
        .route("/api/mute", post(mute))
        .route("/api/unmute", post(unmute))
        .route("/api/followups/dismiss", post(dismiss))
        .route("/api/replylater", post(reply_later))
        .route("/api/tasks", get(tasks).post(add_task))
        .route("/api/tasks/done", post(task_done))
        .route("/api/tasks/remove", post(remove_task))
        .route("/api/waiting-on-others", get(waiting_on_others))
}

fn refused(problem: Problem, method: &Method, uri: &Uri) -> Response<Body> {
    answer::failure(&Failure::Problem(problem), method.as_str(), uri.path())
}

/// A POST's body read as `read` reads it, or the answer that refuses it.
async fn body<T>(
    request: Request,
    read: impl FnOnce(&Posted) -> Result<T, Problem>,
) -> Result<(T, Method, Uri), Box<Response<Body>>> {
    let (method, uri) = (request.method().clone(), request.uri().clone());
    match posted::read(request).await.map(|body| read(&body)) {
        Ok(Ok(asked)) => Ok((asked, method, uri)),
        Ok(Err(problem)) => Err(Box::new(refused(problem, &method, &uri))),
        Err(answer) => Err(answer),
    }
}

/// A copy a request names among many (`ACopy`): its key, the folder it is in, and its uid there.
type ACopy = (i64, String, Option<i64>);

/// `NamesAMessage`: the key, the folder and the uid.
type MessageNamed = (Option<i64>, Option<String>, Option<i64>);

fn names_a_message(body: &Posted) -> Result<MessageNamed, Problem> {
    Ok((body.number("key")?, body.text("folder")?, body.number("uid")?))
}

/// `NamesAThread`: what names a message, and the account and thread id.
struct ThreadAsked {
    key: Option<i64>,
    folder: Option<String>,
    uid: Option<i64>,
    account: Option<String>,
    thread_id: Option<String>,
}

fn names_a_thread(body: &Posted) -> Result<ThreadAsked, Problem> {
    let (key, folder, uid) = names_a_message(body)?;
    Ok(ThreadAsked { key, folder, uid, account: body.text("account")?, thread_id: body.text("thread_id")? })
}

/// `copies`: at most BULK_CAP of them, more being the `bad request`, never the first of them.
fn copies(body: &Posted) -> Result<Option<Vec<ACopy>>, Problem> {
    let Some(listed) = body.list("copies")? else { return Ok(None) };
    if listed.len() > BULK_CAP {
        return Err(Problem::bad_request());
    }
    listed
        .iter()
        .map(|one| {
            let one = one.as_object().ok_or_else(Problem::bad_request)?;
            let key = one.get("key").and_then(as_int).ok_or_else(Problem::bad_request)?;
            let folder = one.get("folder").and_then(Value::as_str).ok_or_else(Problem::bad_request)?;
            let uid = match one.get("uid") {
                None | Some(Value::Null) => None,
                Some(uid) => Some(as_int(uid).ok_or_else(Problem::bad_request)?),
            };
            Ok((key, folder.to_owned(), uid))
        })
        .collect::<Result<_, _>>()
        .map(Some)
}

/// What names a thread, with `pk` for the message of it the page has. One with no copy left names none, as a thread
/// the request names by its id goes first.
fn named(app: &App, asked: ThreadAsked) -> Result<Named, Fault> {
    let pk = match asked.key {
        Some(key) => app.attention.message_pk(key, asked.folder.as_deref(), asked.uid)?,
        None => None,
    };
    Ok(Named { account: asked.account, thread_id: asked.thread_id, pk })
}

/// What a snooze names: a thread by its account and id, the copies still there of `copies`, and the copy `key`,
/// `folder` and `uid` name.
struct Snoozed {
    thread: ThreadAsked,
    copies: Option<Vec<ACopy>>,
}

fn snoozed(body: &Posted) -> Result<Snoozed, Problem> {
    Ok(Snoozed { thread: names_a_thread(body)?, copies: copies(body)? })
}

fn target(app: &App, asked: Snoozed) -> Result<Target, Fault> {
    let pks = match asked.copies {
        None => None,
        Some(copies) => {
            let mut there = Vec::new();
            for (key, folder, uid) in copies {
                there.extend(app.attention.copy_pk(key, &folder, uid)?);
            }
            Some(there)
        }
    };
    let t = asked.thread;
    let pk = match t.key {
        Some(key) => app.attention.copy_pk(key, t.folder.as_deref().unwrap_or_default(), t.uid)?,
        None => None,
    };
    Ok(Target { account: t.account, thread_id: t.thread_id, pks, pk })
}

/// `until`: seconds, or text.
fn until(body: &Posted) -> Result<Option<Until>, Problem> {
    match body.get("until") {
        None => Ok(None),
        Some(Value::String(text)) => Ok(Some(Until::Text(text.clone()))),
        Some(given @ (Value::Number(_) | Value::Bool(_))) => {
            as_int(given).map(|s| Some(Until::Seconds(s))).ok_or_else(Problem::bad_request)
        }
        Some(_) => Err(Problem::bad_request()),
    }
}

/// Hide messages until a preset or a time: a whole thread, the copies listed, or the one named.
async fn snooze(State(app): State<Arc<App>>, request: Request) -> Response<Body> {
    let read = |b: &Posted| Ok((snoozed(b)?, b.one_of("preset", &PRESETS)?, until(b)?, b.text("undo_group")?));
    let ((asked, preset, until, group), method, uri) = match body(request, read).await {
        Ok(read) => read,
        Err(answer) => return *answer,
    };
    super::run(&app, &method, &uri, move |app| {
        let target = target(app, asked)?;
        app.attention.snoozing.snooze(&target, preset.as_deref(), until.as_ref(), group.as_deref())
    })
    .await
}

/// Wake them now.
async fn unsnooze(State(app): State<Arc<App>>, request: Request) -> Response<Body> {
    let (asked, method, uri) = match body(request, snoozed).await {
        Ok(read) => read,
        Err(answer) => return *answer,
    };
    super::run(&app, &method, &uri, move |app| app.attention.snoozing.unsnooze(&target(app, asked)?)).await
}

/// Silence a thread, here and on its server. Marking its unread mail read there goes through a queue this server
/// hasn't yet, so where there is any, the mute is kept here and the rest answered as not yet served.
async fn mute(State(app): State<Arc<App>>, request: Request) -> Response<Body> {
    let read = |b: &Posted| Ok((names_a_thread(b)?, b.text("undo_group")?));
    let ((asked, group), method, uri) = match body(request, read).await {
        Ok(read) => read,
        Err(answer) => return *answer,
    };
    let work = {
        let app = app.clone();
        move || -> Result<Value, Failure> { app.attention.muting.mute(&named(&app, asked)?, group.as_deref()) }
    };
    match tokio::task::spawn_blocking(work).await.unwrap_or(Err(Failure::Fault(Fault::new("Panicked")))) {
        Ok(value) => answer::json(200, &value),
        Err(failure) => answer::failure(&failure, method.as_str(), uri.path()),
    }
}

async fn unmute(State(app): State<Arc<App>>, request: Request) -> Response<Body> {
    let (asked, method, uri) = match body(request, names_a_thread).await {
        Ok(read) => read,
        Err(answer) => return *answer,
    };
    super::run(&app, &method, &uri, move |app| app.attention.muting.unmute(&named(app, asked)?)).await
}

/// A person dismissed a follow-up: its row stays, so nothing starts another on the thread.
async fn dismiss(State(app): State<Arc<App>>, request: Request) -> Response<Body> {
    let (asked, method, uri) = match body(request, names_a_thread).await {
        Ok(read) => read,
        Err(answer) => return *answer,
    };
    super::run(&app, &method, &uri, move |app| app.attention.follow_ups.dismiss(&named(app, asked)?)).await
}

/// Pin a thread to the Reply Later stack; `on` false takes it off, and left out it pins.
async fn reply_later(State(app): State<Arc<App>>, request: Request) -> Response<Body> {
    // A null `on` was said, and isn't true.
    let read = |b: &Posted| Ok((names_a_thread(b)?, if b.has("on") { b.flag("on")?.unwrap_or(false) } else { true }));
    let ((asked, on), method, uri) = match body(request, read).await {
        Ok(read) => read,
        Err(answer) => return *answer,
    };
    super::run(&app, &method, &uri, move |app| app.attention.follow_ups.reply_later(&named(app, asked)?, on)).await
}

/// Every enabled account's tasks.
async fn tasks(State(app): State<Arc<App>>, method: Method, uri: Uri) -> Response<Body> {
    super::run(&app, &method, &uri, |app| app.attention.tasks.listing()).await
}

/// A task someone confirmed, from the message `key` names.
async fn add_task(State(app): State<Arc<App>>, request: Request) -> Response<Body> {
    let read = |b: &Posted| Ok((names_a_message(b)?, b.text("title")?, b.text("due")?));
    let (((key, folder, uid), title, due), method, uri) = match body(request, read).await {
        Ok(read) => read,
        Err(answer) => return *answer,
    };
    super::run(&app, &method, &uri, move |app| {
        let pk = match key {
            Some(key) => Some(
                app.attention
                    .message_pk(key, folder.as_deref(), uid)?
                    .ok_or_else(|| Failure::from(Problem::not_found("not found")))?,
            ),
            None => None,
        };
        app.attention.tasks.add(pk, title.as_deref(), due.as_deref())
    })
    .await
}

/// Done, or with `done` false open again.
async fn task_done(State(app): State<Arc<App>>, request: Request) -> Response<Body> {
    let read = |b: &Posted| Ok((b.required_number("id")?, b.flag("done")?));
    let ((id, done), method, uri) = match body(request, read).await {
        Ok(read) => read,
        Err(answer) => return *answer,
    };
    super::run(&app, &method, &uri, move |app| app.attention.tasks.mark(id, done)).await
}

async fn remove_task(State(app): State<Arc<App>>, request: Request) -> Response<Body> {
    let (id, method, uri) = match body(request, |b| b.required_number("id")).await {
        Ok(read) => read,
        Err(answer) => return *answer,
    };
    super::run(&app, &method, &uri, move |app| app.attention.tasks.remove(id)).await
}

/// Every account's sent threads still with no reply, the longest wait first.
async fn waiting_on_others(State(app): State<Arc<App>>, method: Method, uri: Uri) -> Response<Body> {
    super::run(&app, &method, &uri, |app| app.attention.follow_ups.waiting_on_others()).await
}

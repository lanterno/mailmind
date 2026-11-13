//! The Mailbox's filing routes (`mailmind.http.routers.mailbox`): a flag, a move of one message, of a conversation
//! or of marked rows, Undo, and the tray's Retry and Discard.
//!
//! Each reads its body as its Python schema does, so a field the schema refuses is the `bad request` 400 before
//! anything is looked up. A copy the page names by key, folder and uid is then the row it is now
//! (`named.with_copy`), or `not found`.

use std::sync::Arc;

use axum::Router;
use axum::body::Body;
use axum::extract::State;
use axum::http::{Request, Response};
use axum::routing::post;
use mm_kernel::errors::{Failure, Problem};
use mm_mailbox::command_ports;
use mm_mailbox::filing::{BULK_MAX, FILING_VERBS, FLAG_NAMES, GONE, NamedCopy};
use serde_json::Value;

use crate::answer;
use crate::app::App;
use crate::body::{self, Fields};
use crate::problem;

pub(super) fn routes() -> Router<Arc<App>> {
    Router::new()
        .route("/api/seen", post(seen))
        .route("/api/flag", post(flag))
        .route("/api/archive", post(archive))
        .route("/api/junk", post(junk))
        .route("/api/trash", post(trash))
        .route("/api/move", post(move_to))
        .route("/api/thread/archive", post(thread_archive))
        .route("/api/thread/trash", post(thread_trash))
        .route("/api/thread/move", post(thread_move))
        .route("/api/bulk/file", post(bulk_file))
        .route("/api/bulk/move", post(bulk_move))
        .route("/api/undo", post(undo))
        .route("/api/pending/retry", post(pending_retry))
        .route("/api/pending/discard", post(pending_discard))
}

/// A POST's answer: its body read, then `work` on the blocking pool.
async fn posted(
    app: Arc<App>,
    request: Request<Body>,
    work: impl FnOnce(&App, Fields) -> Result<Value, Failure> + Send + 'static,
) -> Response<Body> {
    let (method, uri) = (request.method().clone(), request.uri().clone());
    match body::read(request).await {
        Ok(fields) => super::run(&app, &method, &uri, move |app| work(app, fields)).await,
        Err(unread) => unread.answer(),
    }
}

/// `ACopy`: a copy of a message by its key, the folder it is in and its uid there.
fn a_copy(fields: &Fields) -> Result<NamedCopy, Problem> {
    Ok((fields.required_number("key")?, fields.required_text("folder")?.to_owned(), fields.number("uid")?))
}

/// The row the copy is now, or `not found`.
fn with_copy(app: &App, (key, folder, uid): &NamedCopy) -> Result<i64, Failure> {
    let pk = command_ports::read(app.commands.store(), |tx| tx.copy_pk(*key, folder, *uid))?;
    pk.ok_or_else(|| Problem::not_found(GONE).into())
}

/// `MoveBody`'s fields past the copy: the undo group and the request's id.
struct Moving {
    copy: NamedCopy,
    undo_group: Option<String>,
    mutation_id: Option<String>,
}

fn moving(fields: &Fields) -> Result<Moving, Problem> {
    Ok(Moving {
        copy: a_copy(fields)?,
        undo_group: fields.text("undo_group")?.map(str::to_owned),
        mutation_id: fields.text("mutation_id")?.map(str::to_owned),
    })
}

/// `bool(body.get(name, True))`: left out it is true, and null is false.
fn on_unless_said(fields: &Fields, name: &str) -> Result<bool, Problem> {
    Ok(match fields.given(name) {
        None => true,
        Some(_) => fields.flag(name)?.unwrap_or(false),
    })
}

/// Mark a message read or unread.
async fn seen(State(app): State<Arc<App>>, request: Request<Body>) -> Response<Body> {
    posted(app, request, |app, fields| {
        let (copy, seen) = (a_copy(&fields)?, on_unless_said(&fields, "seen")?);
        let mutation_id = fields.text("mutation_id")?;
        let pk = with_copy(app, &copy)?;
        app.filing.set_flags(pk, &[("seen", seen)], mutation_id)
    })
    .await
}

/// Star a message, or set another flag a request may set.
async fn flag(State(app): State<Arc<App>>, request: Request<Body>) -> Response<Body> {
    posted(app, request, |app, fields| {
        let copy = a_copy(&fields)?;
        fields.one_of("flag", &FLAG_NAMES)?;
        let on = on_unless_said(&fields, "on")?;
        let mutation_id = fields.text("mutation_id")?;
        let pk = with_copy(app, &copy)?;
        let name = match fields.given("flag") {
            None => "flagged",
            Some(given) => given.as_str().ok_or_else(|| Problem::refused("unknown flag"))?,
        };
        app.filing.set_flags(pk, &[(name, on)], mutation_id)
    })
    .await
}

/// A move of one message by a filing verb.
fn file_one(app: &App, fields: &Fields, kind: &str) -> Result<Value, Failure> {
    let asked = moving(fields)?;
    let pk = with_copy(app, &asked.copy)?;
    app.filing.file(kind, pk, asked.undo_group.as_deref(), asked.mutation_id.as_deref(), None)
}

async fn archive(State(app): State<Arc<App>>, request: Request<Body>) -> Response<Body> {
    posted(app, request, |app, fields| file_one(app, &fields, "archive")).await
}

async fn junk(State(app): State<Arc<App>>, request: Request<Body>) -> Response<Body> {
    posted(app, request, |app, fields| file_one(app, &fields, "junk")).await
}

async fn trash(State(app): State<Arc<App>>, request: Request<Body>) -> Response<Body> {
    posted(app, request, |app, fields| file_one(app, &fields, "trash")).await
}

/// Move one message to a folder of its account's that a person picked.
async fn move_to(State(app): State<Arc<App>>, request: Request<Body>) -> Response<Body> {
    posted(app, request, |app, fields| {
        let (asked, to) = (moving(&fields)?, fields.required_text("to")?);
        let pk = with_copy(app, &asked.copy)?;
        app.filing.move_to(pk, to, asked.undo_group.as_deref(), asked.mutation_id.as_deref())
    })
    .await
}

/// A whole conversation, by a verb or to a picked folder.
fn file_thread(app: &App, fields: &Fields, kind: &str) -> Result<Value, Failure> {
    let asked = moving(fields)?;
    let to = if kind == "move" { Some(fields.required_text("to")?) } else { None };
    let pk = with_copy(app, &asked.copy)?;
    app.filing.file_thread(kind, pk, to, asked.undo_group.as_deref(), asked.mutation_id.as_deref())
}

async fn thread_archive(State(app): State<Arc<App>>, request: Request<Body>) -> Response<Body> {
    posted(app, request, |app, fields| file_thread(app, &fields, "archive")).await
}

async fn thread_trash(State(app): State<Arc<App>>, request: Request<Body>) -> Response<Body> {
    posted(app, request, |app, fields| file_thread(app, &fields, "trash")).await
}

async fn thread_move(State(app): State<Arc<App>>, request: Request<Body>) -> Response<Body> {
    posted(app, request, |app, fields| file_thread(app, &fields, "move")).await
}

/// `BulkRow`: a copy, and whether the row stands for its conversation.
fn bulk_row(fields: &Fields) -> Result<(NamedCopy, bool), Problem> {
    Ok((a_copy(fields)?, fields.flag("whole")? == Some(true)))
}

/// `filing.marked_rows`: one row at least and BULK_MAX at most, refused whole past that, never cut short; `whole`
/// only where the verb takes conversations.
fn marked_rows(rows: Option<Vec<(NamedCopy, bool)>>, whole_allowed: bool) -> Result<Vec<(NamedCopy, bool)>, Problem> {
    match rows {
        Some(rows) if !rows.is_empty() && rows.len() <= BULK_MAX => {
            Ok(rows.into_iter().map(|(copy, whole)| (copy, whole && whole_allowed)).collect())
        }
        _ => Err(Problem::bad_request()),
    }
}

/// Archive, trash or junk the marked rows as one.
async fn bulk_file(State(app): State<Arc<App>>, request: Request<Body>) -> Response<Body> {
    posted(app, request, |app, fields| {
        let verb = fields.one_of("verb", &FILING_VERBS)?;
        let rows = fields.list("rows", bulk_row)?;
        let (undo_group, mutation_id) = (fields.text("undo_group")?, fields.text("mutation_id")?);
        let verb = verb.ok_or_else(Problem::bad_request)?;
        // Junk is said of one sender's message, never a conversation.
        let rows = marked_rows(rows, verb != "junk")?;
        app.filing.file_many(verb, &rows, undo_group, mutation_id)
    })
    .await
}

/// Move the marked rows to a folder a person picked.
async fn bulk_move(State(app): State<Arc<App>>, request: Request<Body>) -> Response<Body> {
    posted(app, request, |app, fields| {
        let to = fields.required_text("to")?;
        let rows = fields.list("rows", bulk_row)?;
        let (undo_group, mutation_id) = (fields.text("undo_group")?, fields.text("mutation_id")?);
        let rows = marked_rows(rows, true)?;
        app.filing.move_many(to, &rows, undo_group, mutation_id)
    })
    .await
}

/// Reverse the last thing done. A reversal that failed was logged, and is on the stack again for another try.
async fn undo(State(app): State<Arc<App>>, request: Request<Body>) -> Response<Body> {
    if let Err(unread) = body::read(request).await {
        return unread.answer();
    }
    let done = tokio::task::spawn_blocking(move || {
        let done = app.undo.undo_last();
        if done.is_ok() {
            // Undo put a message back or took one away: the rail's badges are stale.
            let _ = app.folders.invalidate(None);
        }
        done
    })
    .await;
    match done {
        Ok(Ok(value)) => answer::json(200, &value),
        Ok(Err(Failure::Problem(no))) => answer::problem(&no),
        Ok(Err(Failure::Fault(_))) | Err(_) => {
            answer::problem_document(500, &problem::blank(500, "couldn\u{2019}t undo"))
        }
    }
}

/// `NamesAPending`: the change by its id.
fn pending_id(fields: &Fields) -> Result<i64, Problem> {
    fields.required_number("id")
}

async fn pending_retry(State(app): State<Arc<App>>, request: Request<Body>) -> Response<Body> {
    posted(app, request, |app, fields| app.filing.pending_retry(pending_id(&fields)?)).await
}

async fn pending_discard(State(app): State<Arc<App>>, request: Request<Body>) -> Response<Body> {
    posted(app, request, |app, fields| app.filing.pending_discard(pending_id(&fields)?)).await
}

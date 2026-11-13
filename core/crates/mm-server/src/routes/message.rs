//! A message opened (`mailmind.http.routers.mailbox`): its body, the document its frame loads, a part of it as a
//! download, and an image's preview.
//!
//! A frame's document and a preview carry their own policy (`perimeter.mail_frame_headers` and
//! `attachment_preview_headers`), and this origin alone may frame them: with each other, the only answers that
//! aren't DENY. A download is never shown from the top frame: it is an attachment, under a sandbox that allows
//! nothing, whatever its type.

use std::sync::Arc;

use axum::Router;
use axum::body::Body;
use axum::extract::State;
use axum::http::{HeaderValue, Method, Response, Uri, header};
use axum::routing::get;
use mm_kernel::errors::{Failure, Fault, Problem};
use mm_mailbox::bodies::{Document, Part};
use mm_mailbox::ports::NOT_YET;
use serde_json::Value;

use super::reading::{query, run};
use crate::app::App;
use crate::query::{Query, as_int};
use crate::{answer, problem};

/// The decoded bytes of attachments one message may carry (`compose.domain.limits.MAX_ATTACH_BYTES`): a part past
/// it isn't sent.
pub const MAX_ATTACH_BYTES: usize = 25 << 20;

pub(super) fn routes() -> Router<Arc<App>> {
    Router::new()
        .route("/api/message/body", get(body))
        .route("/api/message/body.html", get(body_document))
        .route("/api/message/attachment", get(attachment))
        .route("/api/message/attachment/preview.html", get(preview))
}

/// The policy on a mail body's own document (`csp.mail_frame_policy`). A sandbox set here holds even if the frame's
/// attribute goes. Allowing the same origin is what lets the parent size the frame and take its link clicks, and it
/// is safe only because nothing here lets a script run. Remote images come only when asked for.
fn mail_frame_policy(load_images: bool) -> &'static str {
    if load_images {
        "sandbox allow-same-origin; default-src 'none'; img-src data: https:; style-src 'unsafe-inline'; \
         font-src data:; base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
    } else {
        "sandbox allow-same-origin; default-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src data:; \
         base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
    }
}

/// The policy on a preview of a stranger's image (`csp.attachment_preview_policy`): its sandbox allows nothing, and
/// it fetches nothing, the image being in the document.
const PREVIEW_POLICY: &str = "sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'; \
                              base-uri 'none'; form-action 'none'; frame-ancestors 'self'";

fn refused(problem: Problem, method: &Method, uri: &Uri) -> Response<Body> {
    answer::failure(&Failure::Problem(problem), method.as_str(), uri.path())
}

/// The copy a request names (`CopyArgs`): a key and a folder it must give, and a uid it may, each number as the
/// schemas read one.
fn copy_named(given: &Query) -> Result<(i64, String, Option<i64>), Problem> {
    let key = given.text("key").and_then(as_int).ok_or_else(Problem::bad_request)?;
    let folder = given.text("folder").ok_or_else(Problem::bad_request)?.to_owned();
    let uid = match given.text("uid") {
        None => None,
        Some(uid) => Some(as_int(uid).ok_or_else(Problem::bad_request)?),
    };
    Ok((key, folder, uid))
}

/// A message opened, or with `prefetch=1` its body stored ahead of the reader.
async fn body(State(app): State<Arc<App>>, method: Method, uri: Uri) -> Response<Body> {
    let given = query(&uri);
    let (key, folder, uid) = match copy_named(&given) {
        Ok(named) => named,
        Err(problem) => return refused(problem, &method, &uri),
    };
    let prefetch = given.text("prefetch") == Some("1");
    run(&app, &method, &uri, move |app| {
        let bodies = &app.reading.bodies;
        bodies.message_body(bodies.copy_pk(key, &folder, uid)?, prefetch)
    })
    .await
}

/// A frame's document, or a preview's, with the headers it carries.
fn framed(document: Document, policy: &'static str) -> Response<Body> {
    let (status, text) = document;
    let mut answer = answer::bytes(status, "text/html; charset=utf-8", text.into_bytes());
    let headers = answer.headers_mut();
    headers.insert(header::CONTENT_SECURITY_POLICY, HeaderValue::from_static(policy));
    headers.insert(header::X_FRAME_OPTIONS, HeaderValue::from_static("SAMEORIGIN"));
    answer
}

/// Work on the blocking pool, a part the Rust server can't do yet answering as such.
async fn document(
    app: &Arc<App>,
    uri: &Uri,
    policy: &'static str,
    work: impl FnOnce(&App) -> Result<Document, Fault> + Send + 'static,
) -> Response<Body> {
    let app = app.clone();
    match tokio::task::spawn_blocking(move || work(&app)).await {
        Ok(Ok(document)) => framed(document, policy),
        Ok(Err(fault)) if fault == NOT_YET => answer::not_yet(uri.path()),
        _ => framed((500, mm_content::frame::of_text(Some(mm_mailbox::bodies::FAILED))), policy),
    }
}

/// The document a message's frame loads (`CopyDocumentArgs`): text and nothing but, so nothing here is refused. A
/// copy these name no longer is a document saying so.
async fn body_document(State(app): State<Arc<App>>, uri: Uri) -> Response<Body> {
    let given = query(&uri);
    let text = |name: &str| given.text(name).unwrap_or_default().to_owned();
    let (key, folder, uid) = (text("key"), text("folder"), text("uid"));
    let load_images = given.text("images") == Some("1");
    document(&app, &uri, mail_frame_policy(load_images), move |app| {
        app.reading.bodies.copy_document(&key, &folder, &uid, load_images)
    })
    .await
}

/// The document an image's preview loads (`PreviewArgs`): text and nothing but, as the frame's.
async fn preview(State(app): State<Arc<App>>, uri: Uri) -> Response<Body> {
    let given = query(&uri);
    let text = |name: &str| given.text(name).unwrap_or_default().to_owned();
    let (key, folder, uid, part_id) = (text("key"), text("folder"), text("uid"), text("part_id"));
    document(&app, &uri, PREVIEW_POLICY, move |app| app.reading.bodies.preview_document(&key, &folder, &uid, &part_id))
        .await
}

/// Never trust a filename a message gave: no path, and no control character, quote or line break.
pub fn safe_filename(name: &Value) -> String {
    let name = name.as_str().filter(|n| !n.is_empty()).unwrap_or("attachment");
    let name = name.rsplit('/').next().unwrap_or_default();
    let name = name.rsplit('\\').next().unwrap_or_default();
    let cleaned: String =
        name.chars().map(|c| if c < '\u{20}' || c == '\u{7f}' || c == '"' { '_' } else { c }).collect();
    let cleaned = cleaned.trim_matches([' ', '.']);
    let cleaned = if cleaned.is_empty() { "attachment" } else { cleaned };
    cleaned.chars().take(255).collect()
}

/// `urllib.parse.quote` with nothing safe: each byte that isn't a letter, a digit or one of `_.-~`, as `%XX`.
fn quoted(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for byte in text.bytes() {
        if byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'.' | b'-' | b'~') {
            out.push(char::from(byte));
        } else {
            out.push_str(&format!("%{byte:02X}"));
        }
    }
    out
}

/// A part as a download: its own type, never shown in the top frame, under a sandbox that allows nothing.
fn download(part: Part) -> Response<Body> {
    if part.data.len() > MAX_ATTACH_BYTES {
        return answer::problem_document(413, &problem::blank(413, "too large"));
    }
    let kind = part.content_type.as_str().filter(|t| !t.is_empty()).unwrap_or("application/octet-stream");
    let kind = mm_kernel::text::strip(kind.split(';').next().unwrap_or_default()).to_owned();
    let disposition = format!("attachment; filename*=UTF-8''{}", quoted(&safe_filename(&part.filename)));
    let (Ok(kind), Ok(disposition)) = (HeaderValue::from_str(&kind), HeaderValue::from_str(&disposition)) else {
        return answer::problem_document(500, &problem::blank(500, "request failed"));
    };
    let mut answer = answer::bytes(200, "application/octet-stream", part.data);
    let headers = answer.headers_mut();
    headers.insert(header::CONTENT_TYPE, kind);
    headers.insert(header::CONTENT_SECURITY_POLICY, HeaderValue::from_static("default-src 'none'; sandbox"));
    headers.insert(header::CONTENT_DISPOSITION, disposition);
    answer
}

/// A part of a copy (`PartArgs`), by the number its parsing gave it: only digits, since `$` would let a newline
/// after one through.
async fn attachment(State(app): State<Arc<App>>, method: Method, uri: Uri) -> Response<Body> {
    let given = query(&uri);
    let named = copy_named(&given)
        .and_then(|named| Ok((named, given.text("part_id").ok_or_else(Problem::bad_request)?.to_owned())));
    let ((key, folder, uid), part_id) = match named {
        Ok(named) => named,
        Err(problem) => return refused(problem, &method, &uri),
    };
    let found = {
        let app = app.clone();
        tokio::task::spawn_blocking(move || -> Result<Part, Failure> {
            let bodies = &app.reading.bodies;
            let pk = bodies.copy_pk(key, &folder, uid)?;
            if part_id.is_empty() || !part_id.bytes().all(|b| b.is_ascii_digit()) {
                return Err(Failure::Problem(Problem::refused("bad part id")));
            }
            bodies.attachment(pk, &part_id)?.map_err(|said| Failure::Problem(Problem::not_found(said)))
        })
        .await
    };
    match found.unwrap_or(Err(Failure::Fault(Fault::new("Panicked")))) {
        Ok(part) => download(part),
        Err(Failure::Fault(fault)) if fault == NOT_YET => answer::not_yet(uri.path()),
        Err(failure) => answer::failure(&failure, method.as_str(), uri.path()),
    }
}

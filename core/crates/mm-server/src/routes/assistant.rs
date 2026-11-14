//! What the page asks of the Assistant (`mailmind.http.routers.assistant`). A route that spends is a POST, and the GETs
//! read what is stored; the one GET that can start a call is drafting ahead's kick, which needs the page's token.
//!
//! Each runs its use case on the blocking pool, let go over the daily limit where the request's
//! `X-MailMind-AI-Over-Limit: allow` says the person said so. A call never sent answers as the page acts on it: over the
//! limit a 402 the page asks about, with AI off a 409. Any other failure is logged and answered in general terms.

use std::sync::Arc;

use axum::Router;
use axum::body::Body;
use axum::extract::{Request, State};
use axum::http::{HeaderMap, Method, Response, Uri};
use axum::routing::post;
use mm_assistant::governor::allowing_over;
use mm_assistant::prepared_replies::ReplyAsked;
use mm_assistant::refusals::{AiFailure, NotSent};
use mm_assistant::thread_digest::Asked;
use mm_kernel::errors::{Failure, Fault, NOT_YET, Problem};
use mm_kernel::log;
use serde_json::{Map, Value, json};

use crate::answer;
use crate::app::App;
use crate::posted::{self, Posted};
use crate::problem;

/// What says a request may go over the daily AI limit, and the value that does.
pub const AI_OVER_HEADER: &str = "x-mailmind-ai-over-limit";

pub(super) fn routes() -> Router<Arc<App>> {
    Router::new()
        .route("/api/ai/summarize", post(summarize))
        .route("/api/ai/draft-reply", post(draft_reply))
        .route("/api/ai/rewrite", post(rewrite))
        .route("/api/ai/extract", post(extract))
}

/// A problem document of one of the AI's own kinds.
fn document(status: u16, kind: &str, title: &str, detail: &str, more: &[(&str, Value)]) -> Value {
    let mut doc = Map::new();
    doc.insert("type".into(), json!(format!("/problems/{kind}")));
    doc.insert("title".into(), json!(title));
    doc.insert("status".into(), json!(status));
    doc.insert("detail".into(), json!(detail));
    for (name, value) in more {
        doc.insert((*name).to_owned(), value.clone());
    }
    Value::Object(doc)
}

/// What a call never sent answers: the 402 the page asks the person about before it goes over, the 409 of AI off,
/// and for anything else the fixed 500.
fn not_sent(not: NotSent, method: &Method, uri: &Uri) -> Response<Body> {
    match not {
        NotSent::OverLimit { spent, limit } => answer::problem_document(
            402,
            &document(
                402,
                "over-limit",
                "Over the daily AI limit",
                "today's AI spending has reached the daily limit",
                &[("over_limit", json!(true)), ("spent_micros", json!(spent)), ("limit_micros", json!(limit))],
            ),
        ),
        NotSent::AiOff => answer::problem_document(
            409,
            &document(
                409,
                "ai-off",
                "AI is off",
                "AI is off: MailMind has no Claude API key",
                &[("ai_off", json!(true))],
            ),
        ),
        NotSent::Deferred(_) => answer::failure(&Failure::Fault(Fault::new("Deferred")), method.as_str(), uri.path()),
    }
}

/// What an AI request that failed answers. A problem says what it says; what the Rust server can't do yet says so;
/// anything else is logged as `what` and answered as `failed`.
fn failed(failure: AiFailure, what: &'static str, said: &'static str, method: &Method, uri: &Uri) -> Response<Body> {
    match failure {
        AiFailure::NotSent(not) => not_sent(not, method, uri),
        AiFailure::Failure(Failure::Problem(p)) => answer::problem(&p),
        AiFailure::Failure(Failure::Fault(fault)) if fault == NOT_YET => answer::not_yet(uri.path()),
        AiFailure::Failure(Failure::Fault(fault)) => {
            log::error("ai", what, &[], Some(fault.class));
            answer::problem_document(500, &problem::blank(500, said))
        }
    }
}

/// Whether the person said this request may go over the daily limit.
fn may_go_over(headers: &HeaderMap) -> bool {
    headers.get(AI_OVER_HEADER).is_some_and(|v| v.as_bytes() == b"allow")
}

/// `work` on the blocking pool, let go over the limit where the request says so; its answer, or what its failure
/// answers.
async fn ai(
    app: &Arc<App>,
    request: (&Method, &Uri, &HeaderMap),
    what: (&'static str, &'static str),
    work: impl FnOnce(&App) -> Result<Response<Body>, AiFailure> + Send + 'static,
) -> Response<Body> {
    let (method, uri, headers) = request;
    let over = may_go_over(headers);
    let app = app.clone();
    let done = tokio::task::spawn_blocking(move || allowing_over(over, || work(&app))).await;
    match done.unwrap_or(Err(AiFailure::Failure(Failure::Fault(Fault::new("Panicked"))))) {
        Ok(answer) => answer,
        Err(failure) => failed(failure, what.0, what.1, method, uri),
    }
}

/// The body, or what refuses it.
async fn body_of(request: Request) -> Result<(Method, Uri, HeaderMap, Posted), Box<Response<Body>>> {
    let (method, uri, headers) = (request.method().clone(), request.uri().clone(), request.headers().clone());
    let body = posted::read(request).await?;
    Ok((method, uri, headers, body))
}

/// The copy a request names (`named.with_message`), as the pk of that message's copy now: the one at `uid` in
/// `folder`, else in `folder`, else another. None where it names no key; `not found` where none is left.
type Named = Option<(i64, Option<String>, Option<i64>)>;

fn named(body: &Posted) -> Result<Named, Problem> {
    let (key, folder, uid) = (body.number("key")?, body.text("folder")?, body.number("uid")?);
    Ok(key.map(|key| (key, folder, uid)))
}

fn pk_of(app: &App, named: Named) -> Result<Option<i64>, AiFailure> {
    match named {
        None => Ok(None),
        Some((key, folder, uid)) => {
            let pk = app.attention.message_pk(key, folder.as_deref(), uid)?;
            pk.map(Some).ok_or_else(|| Problem::not_found("not found").into())
        }
    }
}

fn refused(problem: Problem) -> Response<Body> {
    answer::problem(&problem)
}

/// A thread's or a message's summary: 504 while another request is still writing it.
async fn summarize(State(app): State<Arc<App>>, request: Request) -> Response<Body> {
    let (method, uri, headers, body) = match body_of(request).await {
        Ok(read) => read,
        Err(answer) => return *answer,
    };
    let read = (|| -> Result<_, Problem> {
        Ok((named(&body)?, body.text("account")?, body.text("thread_id")?, body.flag("force")?.unwrap_or(false)))
    })();
    let (copy, account, thread_id, force) = match read {
        Ok(read) => read,
        Err(problem) => return refused(problem),
    };
    ai(&app, (&method, &uri, &headers), ("error", "summary failed"), move |app| {
        let asked = Asked { pk: pk_of(app, copy)?, account, thread_id, force };
        Ok(match app.assistant.digest.asked(&asked)? {
            Some(summary) => answer::json(200, &summary),
            None => answer::problem_document(504, &problem::blank(504, "still summarizing")),
        })
    })
    .await
}

const TONES: [&str; 4] = ["brief", "balanced", "warm", "formal"];
const REWRITE_OPS: [&str; 6] = ["shorten", "elaborate", "formal", "friendly", "fix", "translate"];

/// An editable reply drafted by Claude; never sent.
async fn draft_reply(State(app): State<Arc<App>>, request: Request) -> Response<Body> {
    let (method, uri, headers, body) = match body_of(request).await {
        Ok(read) => read,
        Err(answer) => return *answer,
    };
    let read = (|| -> Result<_, Problem> {
        let asked = ReplyAsked {
            account: body.text("account")?,
            thread_id: body.text("thread_id")?,
            pk: None,
            tone: body.one_of("tone", &TONES)?,
            instruction: body.text("instruction")?,
        };
        Ok((named(&body)?, asked))
    })();
    let (copy, mut asked) = match read {
        Ok(read) => read,
        Err(problem) => return refused(problem),
    };
    ai(&app, (&method, &uri, &headers), ("draft error", "draft failed"), move |app| {
        asked.pk = pk_of(app, copy)?;
        Ok(answer::json(200, &app.assistant.drafting.reply(&asked)?))
    })
    .await
}

/// The composer's text rewritten, in another tone, or translated.
async fn rewrite(State(app): State<Arc<App>>, request: Request) -> Response<Body> {
    let (method, uri, headers, body) = match body_of(request).await {
        Ok(read) => read,
        Err(answer) => return *answer,
    };
    let read =
        (|| -> Result<_, Problem> { Ok((body.text("text")?, body.one_of("op", &REWRITE_OPS)?, body.text("lang")?)) })();
    let (text, op, lang) = match read {
        Ok(read) => read,
        Err(problem) => return refused(problem),
    };
    ai(&app, (&method, &uri, &headers), ("rewrite error", "rewrite failed"), move |app| {
        let text = text.unwrap_or_default();
        let lang = lang.filter(|l| !l.is_empty()).unwrap_or_else(|| "English".to_owned());
        let rewritten =
            mm_assistant::rewrite::rewrite(&app.assistant.calls, &text, op.as_deref().unwrap_or_default(), &lang)?;
        Ok(answer::json(200, &rewritten))
    })
    .await
}

/// What a message proposes for the calendar and the to-do list: suggestions, nothing made.
async fn extract(State(app): State<Arc<App>>, request: Request) -> Response<Body> {
    let (method, uri, headers, body) = match body_of(request).await {
        Ok(read) => read,
        Err(answer) => return *answer,
    };
    let copy = match named(&body) {
        Ok(copy) => copy,
        Err(problem) => return refused(problem),
    };
    ai(&app, (&method, &uri, &headers), ("extract error", "extract failed"), move |app| {
        let pk = pk_of(app, copy)?;
        Ok(answer::json(200, &app.assistant.extractor.read(pk)?))
    })
    .await
}

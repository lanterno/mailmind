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
use axum::routing::{get, post};
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
use crate::query::Query;

/// What says a request may go over the daily AI limit, and the value that does.
pub const AI_OVER_HEADER: &str = "x-mailmind-ai-over-limit";

pub(super) fn routes() -> Router<Arc<App>> {
    Router::new()
        .route("/api/ai/summarize", post(summarize))
        .route("/api/ai/draft-reply", post(draft_reply))
        .route("/api/ai/rewrite", post(rewrite))
        .route("/api/ai/extract", post(extract))
        .route("/api/ai/draft-ahead", get(draft_ahead))
        .route("/api/ai/ask", post(ask))
        .route("/api/ai/do-plan", post(do_plan))
        .route("/api/ai/do-confirm", post(do_confirm))
        .route("/api/briefing", get(briefing).post(write_briefing))
        .route("/api/briefing/refresh", post(refresh_briefing))
        .route("/api/briefing/since", get(delta).post(write_delta))
        .route("/api/review", get(review).post(write_review))
        .route("/api/review/refresh", post(refresh_review))
        .route("/api/cleanup/rationale", post(cleanup_rationale))
        .route("/api/screener/suggest", post(screener_suggest))
        .route("/api/contact/summary", post(sender_summary))
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

/// A question about the person's own mail, answered from what the search finds; nothing changed.
async fn ask(State(app): State<Arc<App>>, request: Request) -> Response<Body> {
    let (method, uri, headers, body) = match body_of(request).await {
        Ok(read) => read,
        Err(answer) => return *answer,
    };
    let (account, question) = match (|| -> Result<_, Problem> { Ok((body.text("account")?, body.text("question")?)) })()
    {
        Ok(read) => read,
        Err(problem) => return refused(problem),
    };
    ai(&app, (&method, &uri, &headers), ("ask error", "ask failed"), move |app| {
        Ok(answer::json(200, &app.assistant.asker.ask(account.as_deref(), question.as_deref())?))
    })
    .await
}

/// A command in words as a capped preview of what it would do, or a search: it changes nothing.
async fn do_plan(State(app): State<Arc<App>>, request: Request) -> Response<Body> {
    let (method, uri, headers, body) = match body_of(request).await {
        Ok(read) => read,
        Err(answer) => return *answer,
    };
    let cmd = match body.text("cmd") {
        Ok(cmd) => cmd.map(Value::String),
        Err(problem) => return refused(problem),
    };
    ai(&app, (&method, &uri, &headers), ("do-plan error", "plan failed"), move |app| {
        Ok(answer::json(200, &app.assistant.bulk.plan(cmd.as_ref())?))
    })
    .await
}

/// The plan a preview froze, carried out once. Filing what it holds is the Mailbox's, which the Rust server hasn't
/// yet: a plan that has expired, or that no one made, says so.
async fn do_confirm(State(app): State<Arc<App>>, request: Request) -> Response<Body> {
    let (_, uri, _, body) = match body_of(request).await {
        Ok(read) => read,
        Err(answer) => return *answer,
    };
    let read = (|| -> Result<_, Problem> { Ok((body.text("plan")?, body.text("undo_group")?)) })();
    let plan = match read {
        Ok((plan, _undo_group)) => plan.unwrap_or_default(),
        Err(problem) => return refused(problem),
    };
    match app.assistant.bulk.take(&plan) {
        None => {
            let mut expired = problem::blank(410, "this plan has expired");
            if let Some(doc) = expired.as_object_mut() {
                doc.insert("expired".into(), json!(true));
            }
            answer::problem_document(410, &expired)
        }
        Some(_) => answer::not_yet(uri.path()),
    }
}

/// A card's request: a GET only reads what is kept, and a POST, whose body must be a JSON object, writes.
async fn card(
    app: Arc<App>,
    request: Request,
    what: (&'static str, &'static str),
    work: impl FnOnce(&App) -> Result<Value, AiFailure> + Send + 'static,
) -> Response<Body> {
    let (method, uri, headers) = (request.method().clone(), request.uri().clone(), request.headers().clone());
    if method == Method::POST
        && let Err(answer) = posted::read(request).await
    {
        return *answer;
    }
    ai(&app, (&method, &uri, &headers), what, move |app| Ok(answer::json(200, &work(app)?))).await
}

/// What is kept of the "Today" card; a POST writes it.
async fn briefing(State(app): State<Arc<App>>, request: Request) -> Response<Body> {
    card(app, request, ("error", "briefing failed"), |app| app.assistant.asking.briefing(false, false)).await
}

/// The "Today" card, written once if none is kept.
async fn write_briefing(State(app): State<Arc<App>>, request: Request) -> Response<Body> {
    card(app, request, ("briefing error", "briefing failed"), |app| app.assistant.asking.briefing(false, true)).await
}

/// The "Today" card, written again.
async fn refresh_briefing(State(app): State<Arc<App>>, request: Request) -> Response<Body> {
    card(app, request, ("briefing error", "briefing failed"), |app| app.assistant.asking.briefing(true, true)).await
}

/// What is kept of the Weekly Review; a POST writes it.
async fn review(State(app): State<Arc<App>>, request: Request) -> Response<Body> {
    card(app, request, ("error", "review failed"), |app| app.assistant.asking.review(false, false)).await
}

/// The Weekly Review, written once if none is kept.
async fn write_review(State(app): State<Arc<App>>, request: Request) -> Response<Body> {
    card(app, request, ("review error", "review failed"), |app| app.assistant.asking.review(false, true)).await
}

/// The Weekly Review, written again.
async fn refresh_review(State(app): State<Arc<App>>, request: Request) -> Response<Body> {
    card(app, request, ("review error", "review failed"), |app| app.assistant.asking.review(true, true)).await
}

/// What is kept of "since you last looked", by `account` and `since` in the query string.
async fn delta(State(app): State<Arc<App>>, request: Request) -> Response<Body> {
    let given = Query::parse(request.uri().query().unwrap_or_default().as_bytes());
    let account = given.text("account").map(|a| Value::String(a.to_owned()));
    let since = given.text("since").map(|s| Value::String(s.to_owned()));
    card(app, request, ("error", "delta failed"), move |app| {
        app.assistant.asking.delta(account.as_ref(), since.as_ref(), false)
    })
    .await
}

/// "Since you last looked", written if none is kept for the mail since `since`.
async fn write_delta(State(app): State<Arc<App>>, request: Request) -> Response<Body> {
    let (method, uri, headers, body) = match body_of(request).await {
        Ok(read) => read,
        Err(answer) => return *answer,
    };
    // `Number | str | None`: text as it is, and a number as `as_int` reads one.
    let read = (|| -> Result<_, Problem> {
        let since = match body.get("since") {
            None => None,
            Some(Value::String(s)) => Some(Value::String(s.clone())),
            Some(v) => Some(json!(posted::as_int(v).ok_or_else(Problem::bad_request)?)),
        };
        Ok((body.text("account")?.map(Value::String), since))
    })();
    let (account, since) = match read {
        Ok(read) => read,
        Err(problem) => return refused(problem),
    };
    ai(&app, (&method, &uri, &headers), ("delta error", "delta failed"), move |app| {
        Ok(answer::json(200, &app.assistant.asking.delta(account.as_ref(), since.as_ref(), true)?))
    })
    .await
}

/// A reply drafted ahead for the message the query names: ready, being made, or none. Asking never drafts: where the
/// mail wants a reply and none is ready, a request that carries the page's CSRF token sets one going, since a kick
/// spends a call and another site's `<img>` mustn't. `peek=1` only looks.
async fn draft_ahead(State(app): State<Arc<App>>, request: Request) -> Response<Body> {
    let (method, uri, headers) = (request.method().clone(), request.uri().clone(), request.headers().clone());
    let given = Query::parse(uri.query().unwrap_or_default().as_bytes());
    let read = (|| -> Result<_, Problem> {
        let key = given.text("key").and_then(crate::query::as_int).ok_or_else(Problem::bad_request)?;
        let uid = given.text("uid").map(|u| crate::query::as_int(u).ok_or_else(Problem::bad_request)).transpose()?;
        Ok((key, given.text("folder").map(str::to_owned), uid, given.text("peek") == Some("1")))
    })();
    let (key, folder, uid, peek) = match read {
        Ok(read) => read,
        Err(problem) => return refused(problem),
    };
    let kick = crate::perimeter::token_matches(&headers, &app.csrf);
    ai(&app, (&method, &uri, &headers), ("error", "draft-ahead failed"), move |app| {
        let Some(pk) = app.attention.message_pk(key, folder.as_deref(), uid)? else {
            return Ok(answer::json(200, &json!({"ok": true, "status": "none"})));
        };
        let (said, start) = app.assistant.drafting.get(pk, peek, kick)?;
        if let Some((account, pk)) = start {
            let (drafting, events) = (app.assistant.drafting.clone(), app.events.clone());
            app.work.spawn(move || {
                drafting.generate(Some(&account), pk, "dwell", &*events);
            });
        }
        Ok(answer::json(200, &said))
    })
    .await
}

/// Why each sender a cleanup names is worth cleaning up, in a sentence; it does nothing.
async fn cleanup_rationale(State(app): State<Arc<App>>, request: Request) -> Response<Body> {
    let (method, uri, headers, body) = match body_of(request).await {
        Ok(read) => read,
        Err(answer) => return *answer,
    };
    // `list[str] | None`: a list of text, or nothing.
    let read = body.list("senders").and_then(|listed| {
        listed
            .map(|items| items.iter().map(|v| v.as_str().map(str::to_owned).ok_or_else(Problem::bad_request)).collect())
            .transpose()
    });
    let senders: Option<Vec<String>> = match read {
        Ok(senders) => senders,
        Err(problem) => return refused(problem),
    };
    ai(&app, (&method, &uri, &headers), ("cleanup rationale error", "rationale failed"), move |app| {
        Ok(answer::json(200, &app.assistant.rationales.explain(senders.as_deref())?))
    })
    .await
}

/// What the AI would allow or screen out of the senders waiting in an account's Screener; only a person's click
/// writes a rule. Who is waiting is Curation's to say, so the route hands its queue over.
async fn screener_suggest(State(app): State<Arc<App>>, request: Request) -> Response<Body> {
    let (method, uri, headers, body) = match body_of(request).await {
        Ok(read) => read,
        Err(answer) => return *answer,
    };
    let account = match body.text("account") {
        Ok(account) => account,
        Err(problem) => return refused(problem),
    };
    ai(&app, (&method, &uri, &headers), ("screener suggest error", "suggestions failed"), move |app| {
        let queue = app.curation.screener.queue(account.as_deref())?;
        let waiting = queue.get("items").and_then(Value::as_array).map(Vec::as_slice).unwrap_or_default();
        Ok(answer::json(200, &app.assistant.screening.suggest(account.as_deref(), waiting)?))
    })
    .await
}

/// What one sender has been writing about, on a person's click from their card.
async fn sender_summary(State(app): State<Arc<App>>, request: Request) -> Response<Body> {
    let (method, uri, headers, body) = match body_of(request).await {
        Ok(read) => read,
        Err(answer) => return *answer,
    };
    let addr = match body.text("addr") {
        Ok(addr) => addr,
        Err(problem) => return refused(problem),
    };
    ai(&app, (&method, &uri, &headers), ("sender summary error", "summary failed"), move |app| {
        Ok(answer::json(200, &app.assistant.sender_digest.summarize(addr.as_deref())?))
    })
    .await
}

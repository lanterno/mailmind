//! The server: HTTP/1.1 on 127.0.0.1 only, each request through the perimeter, then a route.
//!
//! The perimeter runs on every request before any route and before a body is read, on the path as sent, never as
//! decoded: `/%61pi/folders` is no part of the API, and no route takes it for `/api/folders`. An answer, a refusal
//! included, carries the headers the perimeter adds, and no Server or Date header: it says what its route gave it.

use std::convert::Infallible;
use std::net::{Ipv4Addr, SocketAddr};
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, Instant};

use axum::Router;
use axum::body::Body;
use axum::http::{Method, Request, Response, Uri};
use hyper::body::Incoming;
use hyper::server::conn::http1;
use hyper::service::service_fn;
use hyper_util::rt::{TokioIo, TokioTimer};
use mm_kernel::log::{self, Field};
use tokio::net::TcpListener;
use tokio::sync::watch;
use tower_service::Service;

use crate::answer;
use crate::app::App;
use crate::perimeter;

const TAG: &str = "http";

/// The requests being answered now: what a stop, or a test's `settle`, waits for.
#[derive(Debug, Default)]
pub struct Work {
    running: Mutex<usize>,
    idle: Condvar,
}

/// One request being answered, until it drops.
struct Running<'a>(&'a Work);

impl Work {
    fn start(&self) -> Running<'_> {
        if let Ok(mut running) = self.running.lock() {
            *running += 1;
        }
        Running(self)
    }

    /// Run `job` on a thread of its own, counted as a request being answered until it ends: work a request sets
    /// going and answers before it is done (`/api/refresh`'s sync), which a stop and `settle` wait for too.
    pub fn spawn(self: &Arc<Self>, job: impl FnOnce() + Send + 'static) {
        if let Ok(mut running) = self.running.lock() {
            *running += 1;
        }
        let work = self.clone();
        std::thread::spawn(move || {
            let _running = Running(&work);
            job();
        });
    }

    /// Wait until nothing is being answered, for at most `timeout`; how many requests still are.
    pub fn settle(&self, timeout: Duration) -> usize {
        let deadline = Instant::now() + timeout;
        let Ok(mut running) = self.running.lock() else { return 0 };
        while *running > 0 {
            let left = deadline.saturating_duration_since(Instant::now());
            if left.is_zero() {
                break;
            }
            running = match self.idle.wait_timeout(running, left) {
                Ok((running, _)) => running,
                Err(_) => return 0,
            };
        }
        *running
    }
}

impl Drop for Running<'_> {
    fn drop(&mut self) {
        if let Ok(mut running) = self.0.running.lock() {
            *running -= 1;
            if *running == 0 {
                self.0.idle.notify_all();
            }
        }
    }
}

/// The most a POST to `path` may carry, in bytes, judged by its Content-Length before the body is read: 38 MB where
/// mail and its attachments go up (compose's `MAX_SEND_POST`), 1 MB everywhere else. Any other method carries none.
pub fn body_cap(method: &Method, path: &str) -> u64 {
    match (method, path) {
        (&Method::POST, "/api/send" | "/api/draft") => 38 << 20,
        (&Method::POST, _) => 1 << 20,
        _ => 0,
    }
}

/// The request's path as its request line sent it: for an absolute-form target, the whole of it before the query.
fn path_as_sent(uri: &Uri) -> String {
    if uri.scheme().is_none() && uri.authority().is_none() {
        return uri.path().to_owned();
    }
    let whole = uri.to_string();
    whole.split_once('?').map_or(whole.clone(), |(path, _)| path.to_owned())
}

/// One request's answer: the perimeter's refusal, or the route's answer, with the perimeter's headers added.
pub async fn answer(app: &App, routes: Router, request: Request<Incoming>) -> Response<Body> {
    let _running = app.work.start();
    let method = request.method().clone();
    let path = path_as_sent(request.uri());
    let headers = request.headers();
    let refusal = if method == Method::GET || method == Method::HEAD {
        perimeter::refuse_get(&path, headers, app.port)
    } else {
        perimeter::refuse_post(&path, headers, app.port, &app.csrf, body_cap(&method, &path))
    };
    let origin_form = request.uri().scheme().is_none() && request.uri().authority().is_none();
    let mut response = match refusal {
        Some(refusal) => {
            answer::problem_document(refusal.status, &crate::problem::blank(refusal.status, refusal.error))
        }
        // Only GET and POST have routes, and only a path that starts with `/` can match one.
        None if !(method == Method::GET || method == Method::POST) || !origin_form => {
            answer::no_route(method.as_str(), &path)
        }
        None => {
            let mut routes = routes;
            match routes.call(request).await {
                Ok(response) => response,
                Err(never) => match never {},
            }
        }
    };
    perimeter::add_answer_headers(response.headers_mut(), &path);
    response
}

/// A listening socket on 127.0.0.1 and the app it serves.
#[derive(Debug)]
pub struct Server {
    listener: TcpListener,
}

impl Server {
    /// Listen on 127.0.0.1:`port`, any free port for 0.
    pub async fn bind(port: u16) -> std::io::Result<Server> {
        let listener = TcpListener::bind(SocketAddr::from((Ipv4Addr::LOCALHOST, port))).await?;
        Ok(Server { listener })
    }

    pub fn port(&self) -> std::io::Result<u16> {
        Ok(self.listener.local_addr()?.port())
    }

    /// Answer every connection with `app` until `stop` says true; then wait up to `grace` for the requests being
    /// answered, as uvicorn's shutdown does.
    pub async fn run(self, app: Arc<App>, mut stop: watch::Receiver<bool>, grace: Duration) {
        let accepting = tokio::spawn(accept(self.listener, app.clone()));
        let _ = stop.wait_for(|stopped| *stopped).await;
        accepting.abort();
        let work = app.work.clone();
        let _ = tokio::task::spawn_blocking(move || work.settle(grace)).await;
    }
}

/// Each connection, answered on a task of its own.
async fn accept(listener: TcpListener, app: Arc<App>) {
    let routes = crate::routes::router(app.clone());
    loop {
        let stream = match listener.accept().await {
            Ok((stream, _)) => stream,
            Err(e) => {
                let port = app.port.to_string();
                log::warning(
                    TAG,
                    "a connection on %(port)s wasn't accepted",
                    &[(Field::Port, &port)],
                    Some(io_class(&e)),
                );
                // Out of descriptors, say: give the connections being answered a moment to end.
                tokio::time::sleep(Duration::from_millis(50)).await;
                continue;
            }
        };
        let (app, routes) = (app.clone(), routes.clone());
        tokio::spawn(async move {
            let service = service_fn(move |request| {
                let (app, routes) = (app.clone(), routes.clone());
                async move { Ok::<_, Infallible>(answer(&app, routes, request).await) }
            });
            let _ = http1::Builder::new()
                .timer(TokioTimer::new())
                .auto_date_header(false)
                .serve_connection(TokioIo::new(stream), service)
                .await;
        });
    }
}

fn io_class(e: &std::io::Error) -> &'static str {
    match e.kind() {
        std::io::ErrorKind::ConnectionAborted => "ConnectionAborted",
        std::io::ErrorKind::ConnectionReset => "ConnectionReset",
        _ => "OSError",
    }
}

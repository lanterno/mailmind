//! The app in a shell's own process: the `App` `mailmind-server` assembles, with its background workers, answering the
//! routes the page calls without a port open.
//!
//! A shell opens a `Home`: the data folder it chose (the core never guesses a platform's path), the environment it
//! gives the app (never the process's own), and for a demo or test home a stand-in for the login Keychain. A request
//! goes through `serve::answer`, the very perimeter and router the HTTP server runs, with the Host, the Origin, the
//! fetch metadata and this run's CSRF token a page of the app's own would send: so an answer, a refusal included, is
//! what the page gets, byte for byte. The one header a shell can add is the page's leave to go over the day's AI limit
//! (`request_over_limit`), so nothing else the perimeter reads can be forged through the host. The event stream
//! (`/api/events`) is read as it comes and each event handed to a `Subscriber`. `close` ends the streams, stops the
//! workers and waits for the work the requests set going.
//!
//! The host owns the runtime the routes run on. `request` blocks its caller until the answer is whole, so a shell calls
//! it off its main thread.
//!
//! A test's home can ask for the test world the served suites run in (`mm_testworld`): with a keychain stand-in, and
//! `MAILMIND_TEST_CLOCK=paused` among its variables, the app reads a clock that stands until the test moves it
//! (`MAILMIND_TEST_NOW`, or where the system's stood as it opened), and starts no workers, so a job runs only when the
//! test pumps it. `test_world` is the test's hold on them. A home with no stand-in, which a person's always is, never
//! gets one, whatever its variables say.

pub mod stand_in;
pub mod stream;

/// The test world a test's home can run in: its clock, its paused jobs and the lines that move them.
pub use mm_testworld as testworld;

use std::collections::HashMap;
use std::path::PathBuf;
use std::pin::Pin;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex, MutexGuard, PoisonError, RwLock, Weak};
use std::time::Duration;

use axum::Router;
use axum::body::Body;
use axum::http::{HeaderValue, Method, Request, Response, header};
use futures_core::Stream;
use mm_kernel::seams::{Clock, Moment};
use mm_server::environment::{Environment, Settings, TEST_NOW};
use mm_server::real::{Sanitizing, SystemClock};
use mm_server::workers::Workers;
use mm_server::{App, Seams};
use mm_testworld::Controls;
use mm_testworld::clock::{TestClock, parse_iso};
use tokio::runtime::Runtime;
use tokio::task::AbortHandle;

pub use stream::Event;

/// No port is open. The perimeter's Host and Origin checks are given this one, which nothing listens on, and every
/// request the host makes names it.
pub const PORT: u16 = 0;
/// The page's build, which the host never serves: a folder inside the home that nothing makes.
const NO_PAGE: &str = ".page-not-served";
/// How long `close` waits for the requests being answered and the work they set going.
const GRACE: Duration = Duration::from_secs(10);
/// What the event stream's route is.
const EVENTS: &str = "/api/events";

/// Where the app runs and what it is given.
#[derive(Clone)]
pub struct Home {
    /// The data folder, which must exist: the database, `mailmind.config.json` and the log (`mailmind.log`) go in it.
    pub folder: PathBuf,
    /// The environment the app reads (`mm_server::environment`), as the shell gives it: `MAILMIND_PROFILE`, say, or
    /// a demo's `MAILMIND_DB_KEY`. `MAILMIND_HOME` is `folder`, whatever this says.
    pub variables: Vec<(String, String)>,
    /// A demo or test home's stand-in for the login Keychain: a file of item to secret (`stand_in::FileKeychain`).
    /// With one, the app also reaches no host off this machine and runs no program. Without, it runs on the real
    /// seams, the login Keychain among them.
    pub stand_in: Option<PathBuf>,
}

impl Home {
    /// The environment the app reads: `variables`, with `MAILMIND_HOME` left out (it is `folder`).
    fn environment(&self) -> Environment {
        let pairs = self.variables.iter().filter(|(name, _)| name != mm_server::environment::HOME);
        Environment::from_pairs(pairs.map(|(name, value)| (name.as_str(), value.as_str())))
    }

    /// Where the test clock stands as the app opens, when this home runs in a test world: only a home with a keychain
    /// stand-in (a test's or a demo's) whose variables say `MAILMIND_TEST_CLOCK=paused`; it stands at
    /// `MAILMIND_TEST_NOW`, or where the system's clock stood. None for any other home: one with no stand-in, which a
    /// person's always is, isn't asked what its variables say. A test's home that names another value, or a time that
    /// isn't one, doesn't open.
    pub fn test_clock(&self) -> Result<Option<Moment>, String> {
        if self.stand_in.is_none() {
            return Ok(None);
        }
        let environment = self.environment();
        if !environment.test_clock_paused()? {
            return Ok(None);
        }
        match environment.test_now() {
            Some(text) => parse_iso(text).map(Some).ok_or_else(|| format!("{TEST_NOW} isn't a time")),
            None => Ok(Some(SystemClock.now())),
        }
    }
}

/// Names only: a value may be a secret (a demo's database key).
impl std::fmt::Debug for Home {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let names: Vec<&str> = self.variables.iter().map(|(name, _)| name.as_str()).collect();
        f.debug_struct("Home")
            .field("folder", &self.folder)
            .field("variables", &names)
            .field("stand_in", &self.stand_in.is_some())
            .finish()
    }
}

/// An answer, as the page would have read it off the socket.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Answer {
    pub status: u16,
    /// Its Content-Type, empty when it has none.
    pub content_type: String,
    /// Every header it carries, the perimeter's among them, in order.
    pub headers: Vec<(String, String)>,
    /// The body, whole. An event stream's is left empty: `subscribe` reads one.
    pub body: Vec<u8>,
}

/// What a shell gives to hear the event stream. Calls come on one of the host's threads, in the order sent.
pub trait Subscriber: Send + Sync {
    fn event(&self, event: Event);
    /// The stream ended on the host's side, as `close` ends it: nothing more will come.
    fn ended(&self);
}

/// One stream being read.
struct Listening {
    task: AbortHandle,
    subscriber: Arc<dyn Subscriber>,
}

type Streams = Mutex<HashMap<u64, Listening>>;

fn held<T>(lock: &Mutex<T>) -> MutexGuard<'_, T> {
    // Each change under these locks is whole by the time it is let go.
    lock.lock().unwrap_or_else(PoisonError::into_inner)
}

/// From now on a panic in this process is logged by its thread's name and where it happened, never by what it said,
/// which can quote mail (`mm_server::logging::log_panics`). A shell's process calls it before it opens a home; a
/// test's doesn't, so a failing test still says why.
pub fn log_panics() {
    mm_server::logging::log_panics();
}

/// The app, running.
pub struct Host {
    app: Arc<App>,
    routes: Router,
    /// The runtime the routes run on, until `close`: a request holds it while it is answered, so close takes it only
    /// once none is.
    runtime: RwLock<Option<Runtime>>,
    closing: AtomicBool,
    workers: Mutex<Option<Workers>>,
    streams: Arc<Streams>,
    next: AtomicU64,
    /// A test world's clock and paused jobs, for a home that asked for them (`Home::test_clock`).
    test: Option<Controls>,
}

impl std::fmt::Debug for Host {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Host")
            .field("closing", &self.closing.load(Ordering::SeqCst))
            .field("test_world", &self.test.is_some())
            .finish_non_exhaustive()
    }
}

impl Host {
    /// Open `home`: the database made or upgraded, the app assembled over it and its background workers started (all
    /// but the minute's round and the reading of a first scan's older mail left out when the profile only reads, as
    /// `Workers::start` leaves them). A home in a test world (`Home::test_clock`) gets the test clock and starts no
    /// workers. It fails saying why in words a person can act on, naming no secret.
    pub fn open(home: Home) -> Result<Host, String> {
        let environment = home.environment();
        if !home.folder.is_dir() {
            mm_server::logging::to_terminal(environment.log_debug());
            return Err("MailMind's data folder isn't there".into());
        }
        // The log goes to mailmind.log in the data folder too, which a Mac app opened from the Finder keeps when
        // standard error goes nowhere.
        mm_server::logging::keep_in(&home.folder, environment.log_debug());
        let settings = Settings { home: home.folder.clone() };
        let paused = home.test_clock()?.map(|at| Arc::new(TestClock::at(at)));
        let mut seams = match &home.stand_in {
            Some(file) => stand_in_seams(file.clone()),
            None => mm_server::real::seams(),
        };
        if let Some(clock) = &paused {
            seams.clock = clock.clone();
        }
        let runtime = tokio::runtime::Builder::new_multi_thread()
            .enable_all()
            .thread_name("mailmind-core")
            .build()
            .map_err(|_| "MailMind's core couldn't start")?;
        let (app, workers) = {
            let _inside = runtime.enter();
            let app = Arc::new(App::assemble(&environment, &settings, home.folder.join(NO_PAGE), PORT, &seams)?);
            // In a test world nothing runs in the background: a job runs when the test pumps it.
            let workers = paused.is_none().then(|| {
                Workers::start(&app, environment.read_only(), environment.reconcile_secs(), environment.expire_secs())
            });
            (app, workers)
        };
        let routes = mm_server::routes::router(app.clone());
        let test = paused.map(|clock| Controls::new(clock, app.clone()));
        Ok(Host {
            app,
            routes,
            runtime: RwLock::new(Some(runtime)),
            closing: AtomicBool::new(false),
            workers: Mutex::new(workers),
            streams: Arc::new(Mutex::new(HashMap::new())),
            next: AtomicU64::new(0),
            test,
        })
    }

    /// The test world's clock and paused jobs, for a home that runs in one (`Home::test_clock`); None for every other,
    /// and once the host has closed.
    pub fn test_world(&self) -> Option<&Controls> {
        self.test.as_ref().filter(|_| !self.closing.load(Ordering::SeqCst))
    }

    /// The computer woke from sleep, or its network changed: a connection may be dead without a word, and mail may
    /// have come while nothing listened. IDLE's workers let their connections go and open fresh ones at once, and
    /// every account is synced now rather than at the next round. A failure from before the sleep is no measure of
    /// now: trouble that may pass counts afresh, so the sync on waking, while the network comes back, shows nothing
    /// on its own. A first scan's older mail, whose last round the sleep or the network may have cut short, is read
    /// on now too, rather than up to ten minutes on. A host that only reads syncs nothing, as its background sync
    /// does, but reads on the older mail, as it does that; one that has closed does nothing.
    pub fn woke(&self) {
        if self.closing.load(Ordering::SeqCst) {
            return;
        }
        // Each connection kept is likely dead after a sleep or a change of network: the next job opens a new one.
        self.app.kept.woke();
        self.app.syncing.woke();
        self.app.realtime.renew();
        self.app.jobs.kick("sync");
        self.app.jobs.kick(mm_server::workers::OLDER_MAIL);
    }

    /// The app itself, for what is asked of it directly rather than through a route.
    pub fn app(&self) -> &App {
        &self.app
    }

    /// The answer to `method` on `target` (a path and its query, as a request line names it), with `body` for a POST:
    /// the route's, or the perimeter's refusal, as a problem document. After `close`, a 503.
    pub fn request(&self, method: &str, target: &str, body: &[u8]) -> Answer {
        self.answer(method, target, body, false)
    }

    /// `request`, with the person's leave to go over today's AI limit: `X-MailMind-AI-Over-Limit: allow`, which the
    /// page adds when it sends a request again once they say yes to its question. Without it, an AI call at the limit
    /// is the 402 that asks.
    pub fn request_over_limit(&self, method: &str, target: &str, body: &[u8]) -> Answer {
        self.answer(method, target, body, true)
    }

    fn answer(&self, method: &str, target: &str, body: &[u8], over_limit: bool) -> Answer {
        if self.closing.load(Ordering::SeqCst) {
            return fixed(503, "MailMind has closed");
        }
        let runtime = self.runtime.read().unwrap_or_else(PoisonError::into_inner);
        let Some(runtime) = runtime.as_ref() else { return fixed(503, "MailMind has closed") };
        let Some(request) = self.made(method, target, body, over_limit) else { return fixed(400, "bad request") };
        runtime.block_on(async {
            let response = mm_server::serve::answer(&self.app, self.routes.clone(), request).await;
            whole(response).await
        })
    }

    /// Hear the event stream: each event from now on goes to `subscriber`, until the `Subscription` drops or the host
    /// closes. What the route answers instead (a 503 once too many streams are open) is the refusal.
    pub fn subscribe(&self, subscriber: Arc<dyn Subscriber>) -> Result<Subscription, Answer> {
        if self.closing.load(Ordering::SeqCst) {
            return Err(fixed(503, "MailMind has closed"));
        }
        let runtime = self.runtime.read().unwrap_or_else(PoisonError::into_inner);
        let Some(runtime) = runtime.as_ref() else { return Err(fixed(503, "MailMind has closed")) };
        let Some(request) = self.made("GET", EVENTS, &[], false) else { return Err(fixed(400, "bad request")) };
        let response = runtime.block_on(mm_server::serve::answer(&self.app, self.routes.clone(), request));
        if response.status() != 200 || !is_stream(&response) {
            return Err(runtime.block_on(whole(response)));
        }
        let id = self.next.fetch_add(1, Ordering::SeqCst);
        // Held while the task starts, so a stream that ends at once still finds itself listed.
        let mut streams = held(&self.streams);
        let (listed, heard) = (Arc::downgrade(&self.streams), subscriber.clone());
        let task = runtime.spawn(async move {
            let mut body = response.into_body().into_data_stream();
            let mut frames = stream::Frames::default();
            while let Some(Ok(chunk)) = std::future::poll_fn(|cx| Pin::new(&mut body).poll_next(cx)).await {
                for event in frames.take(&chunk) {
                    heard.event(event);
                }
            }
            if let Some(streams) = listed.upgrade()
                && held(&streams).remove(&id).is_some()
            {
                heard.ended();
            }
        });
        streams.insert(id, Listening { task: task.abort_handle(), subscriber });
        Ok(Subscription { id, streams: Arc::downgrade(&self.streams) })
    }

    /// End every stream, stop the workers once each round in progress returns, wait for the requests being answered
    /// and the work they set going (a sync `/api/refresh` started, say) for up to ten seconds, then stop the runtime.
    /// A request from now on is answered 503. Closing twice does nothing more.
    pub fn close(&self) {
        if self.closing.swap(true, Ordering::SeqCst) {
            return;
        }
        let ending: Vec<Listening> = held(&self.streams).drain().map(|(_, listening)| listening).collect();
        for listening in ending {
            listening.task.abort();
            listening.subscriber.ended();
        }
        if let Some(workers) = held(&self.workers).take() {
            workers.stop();
        }
        self.app.work.settle(GRACE);
        // Each account's kept connection logs out, and one a job still has as it gives it back.
        self.app.kept.close();
        let runtime = self.runtime.write().unwrap_or_else(PoisonError::into_inner).take();
        if let Some(runtime) = runtime {
            // A runtime can't wait for itself on one of its own threads, where the last reference may drop.
            if tokio::runtime::Handle::try_current().is_ok() {
                runtime.shutdown_background();
            } else {
                runtime.shutdown_timeout(Duration::from_secs(1));
            }
        }
    }

    /// The request a page of the app's own would send: this server's Host, same-origin, this run's CSRF token on
    /// every request as the page's client attaches it, the leave to go over the AI limit where the person gave it,
    /// and on any method but GET and HEAD the Origin and the body's length and type. None for a method or target no
    /// request line could carry.
    fn made(&self, method: &str, target: &str, body: &[u8], over_limit: bool) -> Option<Request<Body>> {
        let method = Method::from_bytes(method.as_bytes()).ok()?;
        if !target.starts_with('/') {
            return None;
        }
        let reads = method == Method::GET || method == Method::HEAD;
        let mut request = Request::builder()
            .method(method)
            .uri(target)
            .header(header::HOST, format!("127.0.0.1:{PORT}"))
            .header("sec-fetch-site", "same-origin")
            .header("x-csrf-token", HeaderValue::from_str(&self.app.csrf).ok()?);
        if over_limit {
            request = request.header(mm_server::routes::AI_OVER_HEADER, "allow");
        }
        if reads {
            return request.body(Body::empty()).ok();
        }
        request = request
            .header(header::ORIGIN, format!("http://127.0.0.1:{PORT}"))
            .header(header::CONTENT_TYPE, "application/json")
            .header(header::CONTENT_LENGTH, body.len());
        request.body(Body::from(body.to_vec())).ok()
    }
}

impl Drop for Host {
    fn drop(&mut self) {
        self.close();
    }
}

/// A stream being heard. Dropping it ends the stream, and its subscriber hears nothing more.
#[derive(Debug)]
pub struct Subscription {
    id: u64,
    streams: Weak<Streams>,
}

impl Subscription {
    pub fn cancel(&self) {
        if let Some(streams) = self.streams.upgrade()
            && let Some(listening) = held(&streams).remove(&self.id)
        {
            listening.task.abort();
        }
    }
}

impl Drop for Subscription {
    fn drop(&mut self) {
        self.cancel();
    }
}

/// The seams a demo or test home runs on: the system's clock and the sanitizer itself, as `real::seams`, with the
/// Keychain a file, no host but this machine, and no nameserver or model server asked.
fn stand_in_seams(file: PathBuf) -> Seams {
    Seams {
        clock: Arc::new(SystemClock),
        outside: Arc::new(stand_in::LoopbackOnly),
        keychain: Arc::new(stand_in::FileKeychain::new(file)),
        sanitizer: Arc::new(Sanitizing),
        mail_hosts: Arc::new(mm_server::mail_hosts::NoLookups),
        local_ai: Arc::new(mm_server::local_ai::NoLocalServers),
    }
}

fn is_stream(response: &Response<Body>) -> bool {
    let content_type = response.headers().get(header::CONTENT_TYPE).map(HeaderValue::as_bytes).unwrap_or_default();
    content_type.starts_with(b"text/event-stream")
}

/// `response` read whole: its status, its headers and its body, or for an event stream its head alone.
async fn whole(response: Response<Body>) -> Answer {
    let status = response.status().as_u16();
    let stream = is_stream(&response);
    let (parts, body) = response.into_parts();
    let headers: Vec<(String, String)> = parts
        .headers
        .iter()
        .map(|(name, value)| (name.as_str().to_owned(), value.as_bytes().iter().map(|&b| char::from(b)).collect()))
        .collect();
    let content_type = headers.iter().find(|(name, _)| name == header::CONTENT_TYPE.as_str()).map(|(_, v)| v.clone());
    let body = if stream {
        Vec::new()
    } else {
        match axum::body::to_bytes(body, usize::MAX).await {
            Ok(bytes) => bytes.to_vec(),
            // A download that failed partway: what came is no answer.
            Err(_) => return fixed(500, "request failed"),
        }
    };
    Answer { status, content_type: content_type.unwrap_or_default(), headers, body }
}

/// A problem document of the host's own, `about:blank` as the perimeter's are: a request it can't make, or one after
/// it closed.
fn fixed(status: u16, said: &str) -> Answer {
    let content_type = mm_server::problem::PROBLEM_JSON.to_owned();
    Answer {
        status,
        headers: vec![(header::CONTENT_TYPE.as_str().to_owned(), content_type.clone())],
        content_type,
        body: mm_server::json::dumps(&mm_server::problem::blank(status, said)).into_bytes(),
    }
}

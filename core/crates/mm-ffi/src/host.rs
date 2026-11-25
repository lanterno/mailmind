//! The app in the shell's own process (`mm_host`), for Swift: a home opened, the routes the page calls answered, and
//! the event stream heard through a listener. What crosses is a request's method, target and body (with, once a person
//! says yes to going over the day's AI limit, their leave), and an answer's status, headers and bytes: the routes' own
//! JSON, which the shell decodes with the types the API's schemas make.

use std::collections::HashMap;
use std::sync::Arc;

use zeroize::Zeroize;

/// Where the app runs and what it is given. The shell chooses the folder; the core never guesses a platform's path.
#[derive(uniffi::Record)]
pub struct CoreHome {
    /// The data folder, which must exist.
    pub folder: String,
    /// The environment the app reads (`MAILMIND_PROFILE`, a demo's `MAILMIND_DB_KEY`): never the process's own unless
    /// the shell passes it on. Wiped once the core holds it, since a value may be a key.
    pub variables: HashMap<String, String>,
    /// A demo or test home's stand-in for the login Keychain: a file of item to secret. With one, the app reaches no
    /// host off this machine and runs no program.
    pub stand_in: Option<String>,
}

/// Names only: a value may be a secret.
impl std::fmt::Debug for CoreHome {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let mut names: Vec<&String> = self.variables.keys().collect();
        names.sort();
        f.debug_struct("CoreHome").field("folder", &self.folder).field("variables", &names).finish_non_exhaustive()
    }
}

/// One header of an answer.
#[derive(Clone, Debug, PartialEq, Eq, uniffi::Record)]
pub struct CoreHeader {
    pub name: String,
    pub value: String,
}

/// An answer, as the page would have read it: a route's JSON, a file, or a problem document.
#[derive(Clone, Debug, PartialEq, Eq, uniffi::Record)]
pub struct CoreAnswer {
    pub status: u16,
    /// Its Content-Type, empty when it has none.
    pub content_type: String,
    pub headers: Vec<CoreHeader>,
    pub body: Vec<u8>,
}

impl From<mm_host::Answer> for CoreAnswer {
    fn from(answer: mm_host::Answer) -> CoreAnswer {
        CoreAnswer {
            status: answer.status,
            content_type: answer.content_type,
            headers: answer.headers.into_iter().map(|(name, value)| CoreHeader { name, value }).collect(),
            body: answer.body,
        }
    }
}

/// One event of the stream: its id, its name (a topic, `hello` or `resync`) and its JSON data.
#[derive(Clone, Debug, PartialEq, Eq, uniffi::Record)]
pub struct CoreEvent {
    pub id: String,
    pub name: String,
    pub data: String,
}

/// Why the host did not do what it was asked.
#[derive(Debug, PartialEq, Eq, uniffi::Error)]
pub enum HostProblem {
    /// The home didn't open, in words a person can act on, naming no secret.
    NotOpened { why: String },
    /// The route refused: its problem document.
    Refused { answer: CoreAnswer },
}

impl std::fmt::Display for HostProblem {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            HostProblem::NotOpened { why } => f.write_str(why),
            HostProblem::Refused { answer } => write!(f, "refused with {}", answer.status),
        }
    }
}

/// Why the test world didn't do what the test asked.
#[derive(Debug, PartialEq, Eq, uniffi::Error)]
pub enum TestWorldProblem {
    /// The host doesn't run in a test world (its home has no keychain stand-in, or didn't ask with
    /// `MAILMIND_TEST_CLOCK=paused`), or it has closed.
    NotATestWorld,
    /// What was asked can't be done: a time that isn't one, a job that failed or isn't on this core yet. It names a
    /// class, never a value.
    Failed { why: String },
}

impl std::fmt::Display for TestWorldProblem {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            TestWorldProblem::NotATestWorld => f.write_str("not a test world"),
            TestWorldProblem::Failed { why } => f.write_str(why),
        }
    }
}

fn failed(why: String) -> TestWorldProblem {
    TestWorldProblem::Failed { why }
}

/// What a shell gives to hear the event stream. Calls come on one of the core's threads, in the order sent.
#[uniffi::export(with_foreign)]
pub trait EventListener: Send + Sync {
    fn on_event(&self, event: CoreEvent);
    /// The stream ended on the core's side, as closing ends it: nothing more will come.
    fn on_end(&self);
}

/// The app, running in this process. Releasing the last reference closes it.
#[derive(Debug, uniffi::Object)]
pub struct CoreHost {
    inner: mm_host::Host,
}

#[uniffi::export]
impl CoreHost {
    /// Open `home`: its database made or upgraded, the app assembled and its background workers started. It reads
    /// the disk and may wait on the Keychain: never call it on the main thread.
    #[uniffi::constructor]
    pub fn open(mut home: CoreHome) -> Result<Arc<CoreHost>, HostProblem> {
        // A panic in the app's process is logged by its thread and where, never by what it said, which can quote mail.
        mm_host::log_panics();
        let opened = mm_host::Host::open(mm_host::Home {
            folder: home.folder.clone().into(),
            variables: home.variables.iter().map(|(name, value)| (name.clone(), value.clone())).collect(),
            stand_in: home.stand_in.clone().map(Into::into),
        });
        for value in home.variables.values_mut() {
            value.zeroize();
        }
        let inner = opened.map_err(|why| HostProblem::NotOpened { why })?;
        Ok(Arc::new(CoreHost { inner }))
    }

    /// The answer to `method` on `target`, a path and its query (`/api/messages?folder=INBOX`), with `body` for a POST
    /// (JSON). It blocks until the answer is whole: call it off the main thread.
    pub fn request(&self, method: String, target: String, body: Vec<u8>) -> CoreAnswer {
        self.inner.request(&method, &target, &body).into()
    }

    /// `request`, with the person's leave to go over today's AI limit, as the page sends a request again once they
    /// say yes to its question. It is the only header a shell can add. It blocks as `request` does.
    pub fn request_over_limit(&self, method: String, target: String, body: Vec<u8>) -> CoreAnswer {
        self.inner.request_over_limit(&method, &target, &body).into()
    }

    /// Hear the event stream until the subscription is cancelled or released, or the host closes.
    pub fn subscribe(&self, listener: Arc<dyn EventListener>) -> Result<Arc<EventSubscription>, HostProblem> {
        let inner = self
            .inner
            .subscribe(Arc::new(Forward(listener)))
            .map_err(|answer| HostProblem::Refused { answer: answer.into() })?;
        Ok(Arc::new(EventSubscription { inner }))
    }

    /// The computer woke from sleep, or its network changed: IDLE's connections are opened afresh and every account
    /// is synced now. It returns at once; the work runs in the background.
    pub fn woke(&self) {
        self.inner.woke();
    }

    /// End every stream, stop the background work once each round returns, and wait for the work requests set
    /// going. A request from then on is answered 503.
    pub fn close(&self) {
        self.inner.close();
    }

    /// Whether the app runs in a test world: a test's home (with a keychain stand-in) whose variables say
    /// `MAILMIND_TEST_CLOCK=paused`. Its clock stands until the test moves it, and no job runs until the test pumps
    /// it. A person's run never does.
    pub fn in_test_world(&self) -> bool {
        self.inner.test_world().is_some()
    }

    /// Where the test clock stands, in ISO 8601, UTC.
    pub fn test_now(&self) -> Result<String, TestWorldProblem> {
        Ok(mm_host::testworld::clock::iso(self.world()?.now()))
    }

    /// Stand the test clock at `at`, a time in ISO 8601 (one without an offset is UTC), before or after where it was.
    pub fn test_set_clock(&self, at: String) -> Result<(), TestWorldProblem> {
        let world = self.world()?;
        let moment = mm_host::testworld::clock::parse_iso(&at).ok_or_else(|| failed("not a time".into()))?;
        world.set(moment);
        Ok(())
    }

    /// Move the test clock on by `seconds`: where it stands now, in ISO 8601. Nothing runs by itself for it: a job
    /// that came due runs when the test pumps it.
    pub fn test_advance_clock(&self, seconds: f64) -> Result<String, TestWorldProblem> {
        let world = self.world()?;
        let micros = (seconds * 1e6).round();
        if !micros.is_finite() || micros.abs() >= 9.0e18 {
            return Err(failed("not a span of time".into()));
        }
        // Whole, and within i64, as checked above.
        let now = world.advance(micros as i64);
        Ok(mm_host::testworld::clock::iso(now))
    }

    /// Kick `job`, as the app kicks one: its pass waits for `test_pump_kicked`.
    pub fn test_kick(&self, job: String) -> Result<(), TestWorldProblem> {
        self.world()?.kick(&job);
        Ok(())
    }

    /// One pass of `job` with `arguments` (a folder's sync takes the account and the folder), as its lane would run
    /// it: `outbox`, `commands`, `drafts`, `snoozes`, `follow-ups`, `sync`, `reconcile`, `forget`, `backfill`, `warm`,
    /// `retry`, `assess`, `draft-ahead` or `tidy`. It runs on the caller's thread: call it off the main thread.
    pub fn test_pump(&self, job: String, arguments: Vec<String>) -> Result<(), TestWorldProblem> {
        let arguments: Vec<&str> = arguments.iter().map(String::as_str).collect();
        self.world()?.pump(&job, &arguments).map_err(failed)
    }

    /// A pass of each job the app kicked since the last, as its lane would have run it then. It runs on the caller's
    /// thread: call it off the main thread.
    pub fn test_pump_kicked(&self) -> Result<(), TestWorldProblem> {
        self.world()?.pump_kicked().map_err(failed)
    }

    /// Wait until no request is being answered, nor any work one set going, for at most `timeout_seconds`: how many
    /// still are, 0 once settled. It blocks: call it off the main thread.
    pub fn test_settle(&self, timeout_seconds: f64) -> Result<u32, TestWorldProblem> {
        let world = self.world()?;
        let timeout = std::time::Duration::try_from_secs_f64(timeout_seconds.max(0.0))
            .map_err(|_| failed("not a span of time".into()))?;
        Ok(u32::try_from(world.settle(timeout)).unwrap_or(u32::MAX))
    }

    /// The id of the last event the app published (`run-n`): a subscriber has heard all there is once it has heard
    /// this one.
    pub fn test_high_water(&self) -> Result<String, TestWorldProblem> {
        Ok(self.world()?.high_water())
    }
}

impl CoreHost {
    fn world(&self) -> Result<&mm_host::testworld::Controls, TestWorldProblem> {
        self.inner.test_world().ok_or(TestWorldProblem::NotATestWorld)
    }
}

/// A stream being heard.
#[derive(Debug, uniffi::Object)]
pub struct EventSubscription {
    inner: mm_host::Subscription,
}

#[uniffi::export]
impl EventSubscription {
    /// Hear nothing more.
    pub fn cancel(&self) {
        self.inner.cancel();
    }
}

struct Forward(Arc<dyn EventListener>);

impl mm_host::Subscriber for Forward {
    fn event(&self, event: mm_host::Event) {
        self.0.on_event(CoreEvent { id: event.id, name: event.name, data: event.data });
    }

    fn ended(&self) {
        self.0.on_end();
    }
}

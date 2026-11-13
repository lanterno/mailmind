//! The live event stream's hub (`mailmind.platform.sse`): what the app tells open tabs, numbered so a tab that
//! reconnects misses nothing.
//!
//! `publish` numbers each event and keeps the last `RING` of them. Each tab's stream is a `Listener` that reads the
//! kept events from its own position, so a slow tab falls behind instead of losing events. A tab that reconnects sends
//! the id of the last event it saw (EventSource sends it as Last-Event-ID by itself), and its new stream resumes right
//! after it. When the hub no longer keeps that position, or the id comes from an earlier run of the process, the stream
//! sends one `resync` event instead, and the tab fetches afresh what it shows.
//!
//! An id is `<run>-<n>`: n counts the events this process published, from 1, and run is drawn once per hub, so an id
//! from before a restart never passes for one of this run's.
//!
//! A tab's stream waits on no thread: it is polled for its next frame, and the hub wakes it when an event comes. When
//! the tab hangs up, its stream is dropped, and its listener with it.

use std::collections::{HashMap, VecDeque};
use std::convert::Infallible;
use std::pin::Pin;
use std::sync::{Arc, Mutex, MutexGuard};
use std::task::{Context, Poll, Waker};
use std::time::Duration;

use futures_core::Stream;
use mm_kernel::events::{Events, Topic};
use serde_json::{Value, json};
use tokio::time::{Instant, Sleep};

use crate::json;

/// The events kept for tabs that reconnect.
pub const RING: usize = 512;
/// The streams open at once.
pub const MAX_LISTENERS: usize = 8;
/// Between keepalives on a quiet stream, which find a dead socket.
pub const HEARTBEAT: Duration = Duration::from_secs(20);
/// How soon EventSource reconnects, in milliseconds.
pub const RETRY_MS: u32 = 3000;
/// Opens every stream, naming what the server serves now: never published.
const HELLO: &str = "hello";
/// This tab missed events it can't be told: fetch afresh.
const RESYNC: &str = "resync";

/// One tab's place in the stream.
#[derive(Debug)]
struct Tab {
    position: u64,
    resync: bool,
    /// What to wake when an event comes.
    waker: Option<Waker>,
}

#[derive(Debug, Default)]
struct State {
    /// The events published so far.
    n: u64,
    kept: VecDeque<(u64, Topic, Value)>,
    open: HashMap<u64, Tab>,
    tabs: u64,
}

impl State {
    /// The number of the oldest event kept, or the next one's when none is.
    fn oldest(&self) -> u64 {
        self.kept.front().map_or(self.n + 1, |(n, _, _)| *n)
    }
}

#[derive(Debug)]
pub struct Hub {
    run: String,
    state: Mutex<State>,
}

impl Hub {
    /// A hub of a run of its own, drawn from the operating system's random source.
    pub fn new() -> Result<Hub, getrandom::Error> {
        let mut drawn = [0u8; 4];
        getrandom::fill(&mut drawn)?;
        Ok(Hub { run: drawn.iter().map(|b| format!("{b:02x}")).collect(), state: Mutex::default() })
    }

    fn state(&self) -> MutexGuard<'_, State> {
        // A thread that panicked holding it left the numbers whole: each change is one assignment.
        self.state.lock().unwrap_or_else(std::sync::PoisonError::into_inner)
    }

    fn id(&self, n: u64) -> String {
        format!("{}-{n}", self.run)
    }

    /// A stream for one tab, from just after `last_event_id` (the Last-Event-ID it sent), or from now when it sent
    /// none. None once `MAX_LISTENERS` streams are open.
    pub fn listen(self: &Arc<Self>, last_event_id: Option<&str>) -> Option<Listener> {
        let mut state = self.state();
        if state.open.len() >= MAX_LISTENERS {
            return None;
        }
        let resumed = match last_event_id.filter(|id| !id.is_empty()) {
            Some(id) => self.position(&state, mm_kernel::text::strip(id)),
            None => Some(state.n),
        };
        state.tabs += 1;
        let tab = state.tabs;
        let position = resumed.unwrap_or(state.n);
        state.open.insert(tab, Tab { position, resync: resumed.is_none(), waker: None });
        Some(Listener { hub: self.clone(), tab, position })
    }

    /// The event number `last_event_id` names, if this hub can resume after it.
    fn position(&self, state: &State, last_event_id: &str) -> Option<u64> {
        let (run, n) = last_event_id.rsplit_once('-').unwrap_or(("", last_event_id));
        if run != self.run || n.is_empty() || !n.bytes().all(|b| b.is_ascii_digit()) {
            return None;
        }
        let n: u64 = n.parse().ok().filter(|n| *n <= state.n)?;
        (n + 1 >= state.oldest()).then_some(n)
    }

    /// How many streams are open.
    pub fn listening(&self) -> usize {
        self.state().open.len()
    }
}

impl Events for Hub {
    /// Number the event, keep it, and wake every listener.
    fn publish(&self, topic: Topic, data: Value) {
        let data = if data.is_object() { data } else { json!({}) };
        let mut state = self.state();
        state.n += 1;
        let n = state.n;
        state.kept.push_back((n, topic, data));
        if state.kept.len() > RING {
            state.kept.pop_front();
        }
        for tab in state.open.values_mut() {
            if let Some(waker) = tab.waker.take() {
                waker.wake();
            }
        }
    }
}

/// One tab's stream: the events after its position, as they come. Dropping it ends the stream.
#[derive(Debug)]
pub struct Listener {
    hub: Arc<Hub>,
    tab: u64,
    /// Where it opened.
    position: u64,
}

impl Listener {
    /// What the stream sends first: how soon to reconnect, and its position, so a tab that drops before any event
    /// arrives still resumes from here.
    pub fn opening(&self) -> String {
        format!("retry: {RETRY_MS}\nid: {}\n: connected\n\n", self.hub.id(self.position))
    }

    /// The frames of the events after this stream's position, or None, with `waker` woken when one comes. A stream
    /// that fell behind what the hub keeps gets one resync instead.
    fn next(&self, waker: &Waker) -> Option<String> {
        let mut state = self.hub.state();
        let (n, oldest) = (state.n, state.oldest());
        let state = &mut *state;
        let tab = state.open.get_mut(&self.tab)?;
        if tab.resync || tab.position + 1 < oldest {
            tab.resync = false;
            tab.position = n;
            return Some(frame(&self.hub.id(n), RESYNC, &json!({})));
        }
        if n > tab.position {
            let after = tab.position;
            tab.position = n;
            let frames = state.kept.iter().filter(|(k, _, _)| *k > after);
            return Some(frames.map(|(k, topic, data)| frame(&self.hub.id(*k), topic.name(), data)).collect());
        }
        tab.waker = Some(waker.clone());
        None
    }
}

impl Drop for Listener {
    fn drop(&mut self) {
        self.hub.state().open.remove(&self.tab);
    }
}

fn frame(id: &str, topic: &str, data: &Value) -> String {
    format!("id: {id}\nevent: {topic}\ndata: {}\n\n", json::dumps(data))
}

/// The stream's body: the opening and the hello, then each event as it comes, and a keepalive after `HEARTBEAT` with
/// none.
#[derive(Debug)]
pub struct TabStream {
    listener: Listener,
    first: Option<String>,
    heartbeat: Pin<Box<Sleep>>,
}

impl TabStream {
    /// A stream for `listener`, naming `build` (`Page::edition`), the build the page it served names.
    pub fn new(listener: Listener, build: &str) -> TabStream {
        let hello = format!("event: {HELLO}\ndata: {}\n\n", json::dumps(&json!({"build": build})));
        let first = Some(listener.opening() + &hello);
        TabStream { listener, first, heartbeat: Box::pin(tokio::time::sleep(HEARTBEAT)) }
    }
}

impl Stream for TabStream {
    type Item = Result<String, Infallible>;

    fn poll_next(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        let this = self.get_mut();
        if let Some(first) = this.first.take() {
            return Poll::Ready(Some(Ok(first)));
        }
        if let Some(frames) = this.listener.next(cx.waker()) {
            this.heartbeat.as_mut().reset(Instant::now() + HEARTBEAT);
            return Poll::Ready(Some(Ok(frames)));
        }
        if this.heartbeat.as_mut().poll(cx).is_ready() {
            this.heartbeat.as_mut().reset(Instant::now() + HEARTBEAT);
            return Poll::Ready(Some(Ok(": keepalive\n\n".to_owned())));
        }
        Poll::Pending
    }
}

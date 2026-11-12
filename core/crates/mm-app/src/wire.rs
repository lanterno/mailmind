//! The contract as JSON lines, for the carriers that cross a process or a thread: the browser's worker, the
//! desktop shell's channel and the daemon's socket. One `Dispatcher` serves one connection.
//!
//! A request carries an id, and its reply names the id back. A view's batches arrive at any time after the
//! reply that opened it.

use std::collections::HashMap;
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::{Arc, Mutex};

use mm_kernel::Refusal;
use serde::{Deserialize, Serialize};

use crate::list::{ListQuery, ListView, ViewBatch, ViewObserver};
use crate::{Session, lock};

/// One request from a shell.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[cfg_attr(feature = "ts", derive(ts_rs::TS), ts(export))]
pub struct Request {
    pub id: u32,
    #[serde(flatten)]
    pub call: Call,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "method", content = "params", rename_all = "snake_case")]
#[cfg_attr(feature = "ts", derive(ts_rs::TS), ts(export))]
pub enum Call {
    OpenList { query: ListQuery },
    SetVisible { view: u32, first: u32, count: u32 },
    CloseView { view: u32 },
}

/// Everything the core sends a shell.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
#[cfg_attr(feature = "ts", derive(ts_rs::TS), ts(export))]
pub enum Outgoing {
    Reply { id: u32, outcome: Outcome },
    Refused { id: u32, refusal: Refusal },
    Batch { view: u32, batch: ViewBatch },
    ViewRefused { view: u32, refusal: Refusal },
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
#[cfg_attr(feature = "ts", derive(ts_rs::TS), ts(export))]
pub enum Outcome {
    Opened { view: u32, total: u32 },
    Done,
}

/// Where a dispatcher's messages go: a socket, a worker's `postMessage`, a channel.
pub type Sink = Arc<dyn Fn(Outgoing) + Send + Sync>;

/// Serves one connection: turns requests into calls on the session, and views' answers into messages.
pub struct Dispatcher {
    session: Session,
    views: Mutex<HashMap<u32, Arc<ListView>>>,
    next_view: AtomicU32,
    sink: Sink,
}

impl std::fmt::Debug for Dispatcher {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Dispatcher").finish_non_exhaustive()
    }
}

impl Dispatcher {
    pub fn new(session: Session, sink: Sink) -> Dispatcher {
        Dispatcher { session, views: Mutex::new(HashMap::new()), next_view: AtomicU32::new(1), sink }
    }

    /// Serve one line of JSON. A line that is not a request is refused under its id, or 0 if it has none.
    pub fn handle_line(&self, line: &str) {
        match serde_json::from_str::<Request>(line) {
            Ok(request) => self.handle(request),
            Err(e) => {
                let id = serde_json::from_str::<serde_json::Value>(line)
                    .ok()
                    .and_then(|v| v.get("id")?.as_u64())
                    .and_then(|id| u32::try_from(id).ok())
                    .unwrap_or(0);
                // The input is not echoed: it may hold what a person typed.
                let detail = format!("not a request this core knows (line {}, column {})", e.line(), e.column());
                (self.sink)(Outgoing::Refused { id, refusal: Refusal::invalid(detail) });
            }
        }
    }

    pub fn handle(&self, request: Request) {
        let id = request.id;
        let message = match self.call(request.call) {
            Ok(outcome) => Outgoing::Reply { id, outcome },
            Err(refusal) => Outgoing::Refused { id, refusal },
        };
        (self.sink)(message);
    }

    fn call(&self, call: Call) -> Result<Outcome, Refusal> {
        match call {
            Call::OpenList { query } => {
                let view = self.next_view.fetch_add(1, Ordering::Relaxed);
                let observer = Arc::new(WireObserver { view, sink: Arc::clone(&self.sink) });
                let list = self.session.open_list(query, observer)?;
                let total = list.total();
                lock(&self.views).insert(view, list);
                Ok(Outcome::Opened { view, total })
            }
            Call::SetVisible { view, first, count } => {
                let list = lock(&self.views).get(&view).cloned().ok_or_else(|| Refusal::not_found("this view"))?;
                list.set_visible(first, count);
                Ok(Outcome::Done)
            }
            Call::CloseView { view } => {
                lock(&self.views).remove(&view).ok_or_else(|| Refusal::not_found("this view"))?;
                Ok(Outcome::Done)
            }
        }
    }
}

struct WireObserver {
    view: u32,
    sink: Sink,
}

impl ViewObserver for WireObserver {
    fn on_batch(&self, batch: ViewBatch) {
        (self.sink)(Outgoing::Batch { view: self.view, batch });
    }

    fn on_refusal(&self, refusal: Refusal) {
        (self.sink)(Outgoing::ViewRefused { view: self.view, refusal });
    }
}

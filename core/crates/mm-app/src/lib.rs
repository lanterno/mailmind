//! The contract: everything that crosses between the core and a shell.
//!
//! Four kinds of thing cross. Commands go in; views, events and blobs come out. Step 0 has one view, the message
//! list, and no commands yet. A shell reaches this crate through a carrier: UniFFI for Swift and Kotlin
//! (`mm-ffi`), and JSON lines (`wire`) for the browser worker, the desktop shell and the daemon. Nothing from
//! inside the core crosses: a shell sees message keys, never row numbers.

mod list;

use std::path::PathBuf;
use std::sync::{Arc, Mutex, MutexGuard, mpsc};

use mm_store::Store;

pub use list::{ListQuery, ListView, MessageRow, RowRange, ViewBatch, ViewObserver};
pub use mm_kernel::{MessageKey, Refusal};
pub use mm_store::DbKey;

/// How a session is opened. The key comes from the platform's key store; the core never asks for it twice.
#[derive(Debug)]
pub struct SessionConfig {
    pub database: PathBuf,
    pub key: DbKey,
    /// Threads that serve views: 0 serves each request on the caller's thread (the browser's worker), 1 gives
    /// views a thread of their own (phones and desktops).
    pub workers: u8,
}

/// One open profile: its database and the views open on it. Cloning shares it.
#[derive(Clone, Debug)]
pub struct Session {
    inner: Arc<Inner>,
}

#[derive(Debug)]
pub(crate) struct Inner {
    pub(crate) store: Mutex<Store>,
    pub(crate) runner: Runner,
    /// Rows a view loads at a time, and keeps on each side of what is visible.
    pub(crate) page: u32,
}

#[derive(Debug)]
pub(crate) enum Runner {
    Inline,
    Thread(mpsc::Sender<Job>),
}

/// What the view thread is asked to do.
pub(crate) enum Job {
    Serve(std::sync::Weak<ListView>),
    /// Answer once every job before this one is done.
    Settle(mpsc::SyncSender<()>),
}

/// Rows a view loads at a time, as Signal's lists do: a page of 50, and one page kept on each side.
pub const PAGE: u32 = 50;

impl Session {
    pub fn open(config: SessionConfig) -> Result<Session, Refusal> {
        let store = Store::open(&config.database, &config.key)?;
        Session::with_store(store, config.workers)
    }

    /// A session over a store already open, such as one in memory. For tests.
    #[doc(hidden)]
    pub fn with_store(store: Store, workers: u8) -> Result<Session, Refusal> {
        let runner = if workers == 0 { Runner::Inline } else { Runner::Thread(list::spawn_worker()?) };
        Ok(Session { inner: Arc::new(Inner { store: Mutex::new(store), runner, page: PAGE }) })
    }

    /// Open a list. It loads nothing until the shell says which rows are visible.
    pub fn open_list(&self, query: ListQuery, observer: Arc<dyn ViewObserver>) -> Result<Arc<ListView>, Refusal> {
        let order = lock(&self.inner.store).list_order(&query.accounts, &query.folder)?;
        Ok(ListView::new(order, observer, &self.inner))
    }

    /// Wait until every window asked for so far has been served: before a program exits, or a test looks.
    pub fn settle(&self) {
        if let Runner::Thread(worker) = &self.inner.runner {
            let (done, wait) = mpsc::sync_channel(1);
            if worker.send(Job::Settle(done)).is_ok() {
                let _ = wait.recv();
            }
        }
    }
}

pub(crate) fn lock<T>(m: &Mutex<T>) -> MutexGuard<'_, T> {
    // A thread that panicked while holding the lock left nothing half-written: every write is one statement
    // or one transaction. So the lock is taken back rather than spreading the panic.
    m.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
}

//! The core in the browser: the WebAssembly the web shell's dedicated worker runs.
//!
//! The database lives in the origin's private file system (OPFS) through the "sahpool" VFS, whose synchronous
//! file handles only a dedicated worker has. The page never loads this module: it talks to the worker in the
//! JSON lines of `mm_app::wire`, the same lines the desktop shell and the daemon speak.
//!
//! One worker runs one core. Views are served on the worker's own thread (`workers: 0`): there is no other.

#![cfg(target_arch = "wasm32")]

use std::cell::RefCell;
use std::path::Path;
use std::sync::Arc;

use js_sys::Function;
use mm_app::wire::{Dispatcher, Outgoing, Sink};
use mm_app::{DbKey, Refusal, Session, SessionConfig};
use rsqlite_vfs::VfsFilesManager;
use rsqlite_vfs::transfer::DbTransfer;
use sqlite_wasm_vfs::sahpool::{self, OpfsSAHPoolCfgBuilder, OpfsSAHPoolUtil};
use wasm_bindgen::prelude::*;
use zeroize::Zeroizing;

thread_local! {
    /// The pool of OPFS files, once installed.
    static POOL: RefCell<Option<OpfsSAHPoolUtil>> = const { RefCell::new(None) };
    /// Where the open core's lines go. A sink must be `Send`, and a JS function is not, so the sink reaches it
    /// here: the worker has one thread, and one core.
    static LISTENER: RefCell<Option<Function>> = const { RefCell::new(None) };
}

/// Make the origin's private files SQLite's default VFS. Call it once, before anything else; again is harmless.
/// The promise is made here with js-sys's own executor, rather than an `async fn` export, which would need
/// wasm-bindgen-futures for nothing else.
#[wasm_bindgen(js_name = installStorage)]
pub fn install_storage() -> js_sys::Promise {
    js_sys::futures::future_to_promise(async {
        if POOL.with(|pool| pool.borrow().is_some()) {
            return Ok(JsValue::UNDEFINED);
        }
        let config = OpfsSAHPoolCfgBuilder::new().vfs_name("mailmind").directory(".mailmind").build();
        let pool = sahpool::install::<sqlite_wasm_rs::WasmOsCallback>(&config, true)
            .await
            .map_err(|e| JsValue::from(JsError::new(&format!("the browser's private files are not available: {e}"))))?;
        POOL.with(|slot| *slot.borrow_mut() = Some(pool));
        Ok(JsValue::UNDEFINED)
    })
}

/// Whether a database named `name` is in the pool.
#[wasm_bindgen(js_name = databaseExists)]
pub fn database_exists(name: &str) -> Result<bool, JsError> {
    with_pool(|pool| pool.contains(name).map_err(failed))
}

/// The key that opens a fixture. It guards only fictional mail.
#[wasm_bindgen(js_name = fixtureKeyHex)]
pub fn fixture_key_hex() -> String {
    mm_fixtures::FIXTURE_KEY_HEX.to_owned()
}

/// Make the fictional mailbox at `name` with `count` messages, keyed with the fixture key. Returns how many were
/// added. Refused where a database already is.
#[wasm_bindgen(js_name = buildFixture)]
pub fn build_fixture(name: &str, count: u32) -> Result<u32, JsError> {
    if database_exists(name)? {
        return Err(JsError::new("a fixture is only made where nothing is yet"));
    }
    let key = DbKey::from_hex(mm_fixtures::FIXTURE_KEY_HEX).map_err(refused)?;
    mm_fixtures::build(Path::new(name), &key, count, mm_fixtures::SEED).map_err(refused)
}

/// Put a database file into the pool under `name`, byte for byte: an encrypted file has no header to check.
/// No core may have it open.
#[wasm_bindgen(js_name = importDatabase)]
pub fn import_database(name: &str, bytes: &[u8]) -> Result<(), JsError> {
    with_pool(|pool| pool.import_db_unchecked(name, bytes).map_err(failed))
}

/// The bytes of the database `name`, as a native build would read them. No core may have it open.
#[wasm_bindgen(js_name = exportDatabase)]
pub fn export_database(name: &str) -> Result<Vec<u8>, JsError> {
    with_pool(|pool| pool.export_db(name).map_err(failed))
}

/// Bytes SQLite holds in memory now: its page cache and statements, for the shell's measurements.
#[wasm_bindgen(js_name = sqliteMemoryUsed)]
pub fn sqlite_memory_used() -> f64 {
    mm_store::Store::sqlite_memory_used() as f64
}

/// One open database and the connection the page talks to it through.
#[wasm_bindgen]
pub struct Core {
    dispatcher: Dispatcher,
}

impl std::fmt::Debug for Core {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Core").finish_non_exhaustive()
    }
}

#[wasm_bindgen]
impl Core {
    /// Open the database `name` with a key of 64 hex digits. Every line the core sends goes to `on_line`, as a
    /// string of JSON. The key's copy in this module is wiped once it is read; the caller drops its own.
    pub fn open(name: &str, key_hex: String, on_line: Function) -> Result<Core, JsError> {
        let key_hex = Zeroizing::new(key_hex);
        let key = DbKey::from_hex(&key_hex).map_err(refused)?;
        let session = Session::open(SessionConfig { database: name.into(), key, workers: 0 }).map_err(refused)?;
        LISTENER.with(|listener| *listener.borrow_mut() = Some(on_line));
        let sink: Sink = Arc::new(|message: Outgoing| {
            let Ok(line) = serde_json::to_string(&message) else {
                return;
            };
            LISTENER.with(|listener| {
                if let Some(on_line) = listener.borrow().as_ref() {
                    // A listener that throws has nothing the core could do about it.
                    let _ = on_line.call1(&JsValue::NULL, &JsValue::from_str(&line));
                }
            });
        });
        Ok(Core { dispatcher: Dispatcher::new(session, sink) })
    }

    /// Serve one line of JSON: a request of `mm_app::wire`. Replies and batches come through `on_line`, before
    /// this returns.
    pub fn send(&self, line: &str) {
        self.dispatcher.handle_line(line);
    }
}

impl Drop for Core {
    fn drop(&mut self) {
        LISTENER.with(|listener| listener.borrow_mut().take());
    }
}

fn with_pool<T>(f: impl FnOnce(&OpfsSAHPoolUtil) -> Result<T, JsError>) -> Result<T, JsError> {
    POOL.with(|pool| match pool.borrow().as_ref() {
        Some(pool) => f(pool),
        None => Err(JsError::new("installStorage has not run")),
    })
}

fn refused(refusal: Refusal) -> JsError {
    // A refusal's words never hold a person's data, so they can cross as they are.
    JsError::new(&refusal.to_string())
}

fn failed(e: impl std::fmt::Display) -> JsError {
    // The pool's errors name operations and file slots, never what is in a file.
    JsError::new(&format!("the browser's private files failed: {e}"))
}

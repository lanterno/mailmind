//! The desktop shell: the web shell's page in a native window, and the core running natively in this process.
//!
//! The page speaks the JSON lines of `mm_app::wire` over two commands and nothing else: `core_listen` hands the
//! core a channel for its lines, and `core_send` passes it one line. No plugin is loaded, so the page reaches
//! no file, shell or network through Tauri.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::sync::{Arc, Mutex, MutexGuard, mpsc};
use std::time::Instant;

use mm_app::wire::{Dispatcher, Outgoing, Sink};
use mm_app::{DbKey, Session, SessionConfig};
use tauri::ipc::Channel;
use tauri::{Manager, State};

mod measure;

/// Where the core's lines go: the page's channel, once it has given one.
type Listener = Arc<Mutex<Option<Channel<String>>>>;

struct Core {
    /// Lines for the core, served in order on a thread of their own so the window's thread never waits on one.
    lines: mpsc::Sender<String>,
    listener: Listener,
}

/// Keep the page's channel: every line the core sends from now on goes into it.
#[tauri::command]
fn core_listen(core: State<'_, Core>, channel: Channel<String>) {
    *lock(&core.listener) = Some(channel);
}

/// One line for the core. Its answers come down the channel.
#[tauri::command]
fn core_send(core: State<'_, Core>, line: String) {
    // The core's thread ends only with the process, so a failed send has nowhere to be reported.
    let _ = core.lines.send(line);
}

fn main() {
    let launched = Instant::now();
    tauri::Builder::default()
        .setup(move |app| {
            let listener: Listener = Arc::new(Mutex::new(None));
            let session = open_demo(app.path().app_data_dir()?)?;
            let lines = serve(session, sink(&listener))?;
            app.manage(Core { lines, listener });
            // Made here rather than in tauri.conf.json, to follow the page's title.
            tauri::WebviewWindowBuilder::new(app, "main", tauri::WebviewUrl::default())
                .title("MailMind")
                .inner_size(1100.0, 760.0)
                .min_inner_size(480.0, 360.0)
                .on_document_title_changed(move |window, title| measure::title_changed(&window, &title, launched))
                .build()?;
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![core_listen, core_send])
        .run(tauri::generate_context!())
        .expect("the desktop shell could not start");
}

/// Step 0 opens only the demo mailbox: 100,000 fictional messages, made in the app's data folder on first launch
/// and keyed with the fixture key, which guards only fictional mail.
fn open_demo(folder: std::path::PathBuf) -> Result<Session, Box<dyn std::error::Error>> {
    std::fs::create_dir_all(&folder)?;
    let database = folder.join("demo.db");
    if !database.exists() {
        mm_fixtures::build(
            &database,
            &DbKey::from_bytes(mm_fixtures::FIXTURE_KEY),
            mm_fixtures::BUDGET_MESSAGES,
            mm_fixtures::SEED,
        )?;
    }
    let key = DbKey::from_bytes(mm_fixtures::FIXTURE_KEY);
    Ok(Session::open(SessionConfig { database, key, workers: 1 })?)
}

/// Each message the core sends, as one line of JSON into the page's channel.
fn sink(listener: &Listener) -> Sink {
    let listener = Arc::clone(listener);
    Arc::new(move |message: Outgoing| {
        let Ok(line) = serde_json::to_string(&message) else {
            return;
        };
        if let Some(channel) = lock(&listener).as_ref() {
            // A page that has gone away is not worth stopping for: a reload gives a new channel.
            let _ = channel.send(line);
        }
    })
}

/// The thread that hands the page's lines to the core, one at a time and in the order they came.
fn serve(session: Session, sink: Sink) -> std::io::Result<mpsc::Sender<String>> {
    let (lines, incoming) = mpsc::channel::<String>();
    let dispatcher = Dispatcher::new(session, sink);
    std::thread::Builder::new().name("mm-wire".into()).spawn(move || {
        for line in incoming {
            dispatcher.handle_line(&line);
        }
    })?;
    Ok(lines)
}

fn lock<T>(m: &Mutex<T>) -> MutexGuard<'_, T> {
    // Nothing is left half-written under these locks, so one a panic poisoned is taken back.
    m.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
}

//! What `just desktop measure` reads. The page changes its title once its first rows are in the DOM (the web
//! shell's `useFirstRowsMark`). The window takes that title, and with MAILMIND_MEASURE set the first such change
//! is reported on standard error, with the time since `main` began.

use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Instant;

use tauri::{Runtime, WebviewWindow};

static REPORTED: AtomicBool = AtomicBool::new(false);

/// The page's title became `title`.
pub fn title_changed<R: Runtime>(window: &WebviewWindow<R>, title: &str, launched: Instant) {
    let _ = window.set_title(title);
    let measuring = std::env::var_os("MAILMIND_MEASURE").is_some();
    if measuring && title.starts_with("Inbox") && !REPORTED.swap(true, Ordering::Relaxed) {
        eprintln!("mailmind-measure rows-committed {} ms after main", launched.elapsed().as_millis());
    }
}

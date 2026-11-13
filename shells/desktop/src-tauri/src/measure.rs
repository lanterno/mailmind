//! What `just desktop measure` and `just desktop smoke` read. The page changes its title once its first rows are
//! in the DOM (the web shell's `useFirstRowsMark`). The window takes that title, and with MAILMIND_MEASURE set the
//! first such change is reported on standard error, with the time since `main` began.
//!
//! With `MAILMIND_MEASURE=scroll` the app then scrolls its list nine tenths of the way down, waits for real rows
//! there, and exits: 0 once they are in the page, 3 if a minute passes without them. That is the smoke test CI runs
//! on each system, whose exit code says what standard error cannot (a Windows release build has none).

use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Instant;

use tauri::{Manager, Runtime, WebviewWindow};

static REPORTED: AtomicBool = AtomicBool::new(false);
static SCROLLED: AtomicBool = AtomicBool::new(false);

/// Scroll to nine tenths of the list, then name the window after what came: rows there, or none in a minute.
/// Run by the webview itself, so the page's content security policy, which allows no inline script, is not asked.
const SCROLL: &str = r#"(() => {
  const list = document.querySelector(".list");
  const total = Number(document.querySelector(".row")?.getAttribute("aria-setsize") ?? 0);
  if (!list || total === 0) { document.title = "Stuck · MailMind"; return; }
  list.scrollTop = list.scrollHeight * 0.9;
  // A window nobody sees may run no rendering steps, and so send no scroll event of its own.
  list.dispatchEvent(new Event("scroll"));
  const started = performance.now();
  const check = setInterval(() => {
    const far = [...document.querySelectorAll(".row:not(.placeholder)")]
      .some((row) => Number(row.getAttribute("aria-posinset")) > total * 0.85);
    if (far) { clearInterval(check); document.title = "Scrolled · MailMind"; }
    else if (performance.now() - started > 60000) { clearInterval(check); document.title = "Stuck · MailMind"; }
  }, 50);
})();"#;

/// The page's title became `title`.
pub fn title_changed<R: Runtime>(window: &WebviewWindow<R>, title: &str, launched: Instant) {
    let _ = window.set_title(title);
    let Some(mode) = std::env::var_os("MAILMIND_MEASURE") else {
        return;
    };
    if title.starts_with("Inbox") && !REPORTED.swap(true, Ordering::Relaxed) {
        eprintln!("mailmind-measure rows-committed {} ms after main", launched.elapsed().as_millis());
        if mode == "scroll" && window.eval(SCROLL).is_err() {
            window.app_handle().exit(3);
        }
    } else if mode == "scroll" && !SCROLLED.load(Ordering::Relaxed) {
        if title.starts_with("Scrolled") {
            SCROLLED.store(true, Ordering::Relaxed);
            eprintln!("mailmind-measure scrolled {} ms after main", launched.elapsed().as_millis());
            window.app_handle().exit(0);
        } else if title.starts_with("Stuck") {
            SCROLLED.store(true, Ordering::Relaxed);
            eprintln!("mailmind-measure no rows came after scrolling");
            window.app_handle().exit(3);
        }
    }
}

//! `unsanitized on|off`: the sanitizer taken away from the bodies the reader and its frame show, and given back. The
//! frame's sandbox and its document's policy are the boundary, and the sanitizer the defence behind them
//! (docs/security.md), so a test turns it off to show the walls hold alone. Only this launcher can: `mailmind-server`
//! has the sanitizer and nothing else (`mm_server::real::seams`).

use std::sync::atomic::{AtomicBool, Ordering};

use mm_content::sanitize::{Blocked, Sanitized, sanitize};
use mm_mailbox::ports::Sanitizer;

/// The sanitizer, until `unsanitized on`. From then on a body comes back as it was given, as if the sanitizer had
/// missed everything, and says it held back an image, so the reader offers to load them (the Python launcher's
/// stand-in says the same).
#[derive(Debug, Default)]
pub struct Switch {
    off: AtomicBool,
}

impl Switch {
    /// The protocol's answer to `unsanitized <argument>`.
    pub fn answer(&self, argument: &str) -> String {
        match argument {
            "on" => self.off.store(true, Ordering::SeqCst),
            "off" => self.off.store(false, Ordering::SeqCst),
            _ => return "failed unsanitized: on or off".to_owned(),
        }
        "ok".to_owned()
    }
}

impl Sanitizer for Switch {
    fn sanitize(&self, html: &str, load_images: bool, fold: bool) -> Sanitized {
        if self.off.load(Ordering::SeqCst) {
            Sanitized { html: html.to_owned(), blocked: Blocked { images: 1, ..Blocked::default() } }
        } else {
            sanitize(html, load_images, fold)
        }
    }
}

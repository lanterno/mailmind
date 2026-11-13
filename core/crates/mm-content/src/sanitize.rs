//! A message's HTML, made safe to show: an allowlist over the tokens a browser would read, every value escaped.
//!
//! This is defence in depth. The boundary is the reader's frame, sandboxed with a deny-all policy, so a miss here
//! alone runs no script (docs/security.md). The sanitizer is the backstop behind it: it writes the message again from
//! an allowlist, and leaves out scripts, handlers, schemes it doesn't keep, embedded objects and, unless the person
//! asks for them, remote images, which would tell a sender the message was opened.
//!
//! `html` is the HTML sanitizer and `css` the CSS one under it, for `style` attributes and `<style>` sheets. The
//! Python app's sanitizer was the model: the core answers as it did, except where a browser reads the HTML otherwise,
//! when the browser's reading wins.

pub mod css;
pub mod html;
mod text;

pub use css::{CssContext, sanitize_css_stylesheet, sanitize_inline_style};
pub use html::{Blocked, FOLD_CLOSE, FOLD_OPEN, Read, Sanitized, read, sanitize, without_fold};

//! The network: an account's SMTP server, as the outbox delivers through it (`mailmind.compose.adapters.smtp`).
//!
//! `Server::connect` opens a connection through `Outside` and starts TLS on it (STARTTLS, or TLS from the first byte),
//! both `mm-net`'s, and hands back the server's certificate before anything names the account, so the caller can
//! check its pin; `login` then gives a `Session`, which sends a message as `smtplib.SMTP.sendmail` does and tells a
//! message that went out unanswered from one that never went. What goes wrong is an `Error`: `smtplib`'s exception
//! as its codes, never its words, with the Python app's `permanent` and `sanitize_error` on it. The conversation is
//! `Client`, over any stream, so it can be tested over a script. Only a loopback server is reached for now: its
//! certificate is pinned rather than verified, as Proton Bridge's must be.

mod client;
mod error;
mod server;
mod wire;

pub use client::Client;
pub use error::Error;
pub use server::{Endpoint, Server, Session};

//! The network: an account's IMAP server, as the Mailbox's sessions reach it (`mailmind.mailbox.adapters.imap` and
//! the connection `mailmind.accounts.adapters.imap_connection` makes).
//!
//! `Server::connect` opens a connection through `Outside`, starts TLS (STARTTLS, or TLS from the first byte), and
//! hands back the server's certificate before anything is sent, so the caller can check its pin; `login` then gives
//! a `Session`, which implements the Mailbox's `MailSession` and `MailServer`: the folder rail, and the commands a
//! sync, a filing and an undo send (`commands`), their answers read by imap-proto (`codec`). Only a loopback server
//! is reached for now: its certificate is pinned rather than verified, as Proton Bridge's must be. A server off this
//! machine needs a verified certificate, which is still to come.

mod codec;
mod commands;
mod list;
mod names;
mod session;
mod tls;
mod wire;

pub use list::{mailbox_arg, parse_list};
pub use names::{gmail_labels, imap_quote, label_name};
pub use session::{Endpoint, Error, Server, Session};

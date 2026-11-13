//! The network: an account's IMAP server, as the Mailbox's sessions reach it (`mailmind.mailbox.adapters.imap` and
//! the connection `mailmind.accounts.adapters.imap_connection` makes).
//!
//! `Server::connect` opens a connection through `Outside` and starts TLS on it (STARTTLS, or TLS from the first byte),
//! both `mm-net`'s, and
//! hands back the server's certificate before anything is sent, so the caller can check its pin; `login` then gives
//! a `Session`, which implements the Mailbox's `MailSession` and `MailServer`: the folder rail, and the commands a
//! sync, a filing and an undo send (`commands`), their answers read by imap-proto (`codec`). A server off this
//! machine presents a certificate verified on the roots the system trusts; a loopback one's is pinned rather than
//! verified, as Proton Bridge's must be.

mod codec;
mod commands;
mod list;
mod names;
mod session;
mod wire;

pub use list::{mailbox_arg, parse_list};
pub use names::{gmail_labels, imap_quote, label_name};
pub use session::{Endpoint, Error, Server, Session};

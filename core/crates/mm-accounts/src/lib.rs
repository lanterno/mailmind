//! The mail accounts and how MailMind reaches each: the Python app's `mailmind.accounts`, as far as the Rust server
//! has come.
//!
//! The domain is `account` (an entry of `mailmind.config.json`, its type's defaults and what the API may show of
//! it), `credential` (where a secret is), `draft` (an account tried before it is saved), `tls_pin` (a loopback
//! server's certificate) and `login_hold` (a login its server refused, held back). The use cases are `directory`'s,
//! `connections`', `logins`' and `one_password`'s (a secret in 1Password, read once a process). What they need of
//! the world is in `ports`, which the store and the program implement; nothing here reads the disk, the network or
//! the clock.

pub mod account;
pub mod connections;
pub mod credential;
pub mod directory;
pub mod draft;
pub mod health;
pub mod login_hold;
pub mod logins;
pub mod one_password;
pub mod ports;
pub mod preferences;
pub mod tls_pin;

pub use account::Account;
pub use directory::Directory;

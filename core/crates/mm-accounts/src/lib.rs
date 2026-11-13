//! The mail accounts and how MailMind reaches each: the Python app's `mailmind.accounts`, as far as the Rust server
//! has come.
//!
//! The domain is `account` (an entry of `mailmind.config.json`, its type's defaults and what the API may show of
//! it), `credential` (where a secret is) and `tls_pin` (a loopback server's certificate). The use cases are
//! `directory`'s. What they need of the world is in `ports`, which the store and the program implement; nothing
//! here reads the disk, the network or the clock.

pub mod account;
pub mod credential;
pub mod directory;
pub mod health;
pub mod ports;
pub mod preferences;
pub mod tls_pin;

pub use account::Account;
pub use directory::Directory;

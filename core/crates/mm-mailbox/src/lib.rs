//! The mail on each server (`mailmind.mailbox`), as far as the Rust core has come: how its copies thread into
//! conversations, which folder plays which part, and the folder rail.
//!
//! The domain is `threading`, which the Python app also calls through `mm-py`, and `roles`. The use cases
//! are `folders`'. What they need of the world is in `ports`, and what they ask of a mail server in `session`, which
//! the store, the network and the program implement; nothing here reads the disk, the network or the clock.

pub mod folders;
pub mod ports;
pub mod roles;
pub mod session;
pub mod threading;

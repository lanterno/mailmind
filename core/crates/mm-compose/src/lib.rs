//! Drafts, building a message, the outbox and delivery (`mailmind.compose`), as far as the Rust core has come: what
//! the outbox tray lists, the sends and the saves that haven't reached their server.
//!
//! What it needs of the world is in `ports`, which the store implements; nothing here reads the disk, the network or
//! the clock.

pub mod outbox;
pub mod ports;

//! Drafts, building a message, the outbox and delivery (`mailmind.compose`), as far as the Rust core has come: what
//! the outbox tray lists, the sends and the saves that haven't reached their server, a draft as it opens, and the
//! snippets a person keeps for the composer.
//!
//! What it needs of the world is in `ports`, which the store implements; nothing here reads the disk, the network or
//! the clock.

pub mod attachments;
pub mod builder;
pub mod composition;
pub mod draft;
pub mod draft_journal;
pub mod drafts;
pub mod markdown;
pub mod outbox;
pub mod outbox_item;
pub mod ports;
pub mod quote;
pub mod replies;
pub mod resume;
pub mod sending_ports;
pub mod snippets;
pub mod spec;

//! The mail on each server (`mailmind.mailbox`), as far as the Rust core has come: how its copies thread into
//! conversations, which folder plays which part, the folder rail, and the sync.
//!
//! The domain is `threading`, which the Python app also calls through `mm-py`, `roles` and `sync_plan`. The use
//! cases are `folders`' and `ingest`'s. What they need of the world is in `ports` and `sync_ports`, and what they ask
//! of a mail server in `session`, which the store, the network and the program implement; nothing here reads the
//! disk, the network or the clock.

pub mod folders;
pub mod ingest;
pub mod labels;
pub mod ports;
pub mod roles;
pub mod session;
pub mod stats;
pub mod sync_plan;
pub mod sync_ports;
pub mod sync_record;
pub mod threading;

// The reads a person waits on: the lists and what their rows say, the tray of changes, the digest, and what a
// folder's opening does until the sync is ported; a message opened, and the frame it shows in.
pub mod bodies;
pub mod digest;
pub mod inline_images;
pub mod listing;
pub mod lists;
pub mod opening;
pub mod tray;
// Tidy's runs, as the brief reads the newest.
pub mod tidy;

//! The mail on each server (`mailmind.mailbox`), as far as the Rust core has come: how its copies thread into
//! conversations, which folder plays which part, the folder rail, and the sync.
//!
//! The domain is `threading`, which the Python app also calls through `mm-py`, `roles` and `sync_plan`. The use
//! cases are `folders`' and `ingest`'s. What they need of the world is in `ports` and `sync_ports`, and what they ask
//! of a mail server in `session`, which the store, the network and the program implement; nothing here reads the
//! disk, the network or the clock.

pub mod command;
pub mod command_ports;
pub mod commands;
pub mod destination;
pub mod filing;
pub mod folders;
pub mod gates;
pub mod ingest;
pub mod labels;
pub mod ports;
pub mod reconcile;
pub mod reversal;
pub mod roles;
pub mod server_ops;
pub mod session;
pub mod stats;
pub mod sync_plan;
pub mod sync_ports;
pub mod sync_record;
pub mod syncing;
pub mod threading;

// The reads a person waits on: the lists and what their rows say, the digest, and a folder's
// sync as it opens; a message opened, and the frame it shows in.
pub mod bodies;
pub mod digest;
pub mod inline_images;
pub mod listing;
pub mod lists;
pub mod opening;
// Tidy's runs, as the brief reads the newest, and a run by itself.
pub mod tidy;
pub mod tidying;
pub mod undo;
// What the background workers wait on (a kick, and the stop that ends them), and the worker a queue is drained by.
pub mod workers;
// IDLE: one worker for each account, kept in line with the configuration by a supervisor that knows nothing of what
// it watches, and what the workers need of the world.
pub mod idle_supervisor;
pub mod realtime;
pub mod realtime_ports;
// A message's body from its server where it isn't stored, and what keeping it records.
pub mod fetch_ports;
pub mod fetching;
// A folder's sync as it opens: what it needs of the world.
pub mod opening_ports;
// What a sync asks of a person's filters as new mail arrives.
pub mod arrival_ports;
// What is kept of mail that has gone, let go once a month has passed: the daily forget round.
pub mod forgetting;
// A plan a bulk command's preview froze, carried out.
pub mod plans;

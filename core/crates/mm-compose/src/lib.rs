//! Drafts, building a message, the outbox and delivery (`mailmind.compose`): a send built and queued, delivered over
//! SMTP with its copy in Sent, a draft saved locally first and on its server after, and opened back into the composer
//! from its own bytes; what the outbox tray lists, a draft as it opens in the reader, and the snippets a person keeps
//! for the composer.
//!
//! What it needs of the world is in `ports` and `sending_ports`, which the store and the server implement; nothing
//! here reads the disk, the network or the clock.

pub mod attachments;
pub mod builder;
pub mod composing;
pub mod composition;
pub mod delivery;
pub mod draft;
pub mod draft_journal;
pub mod drafting;
pub mod drafts;
pub mod events;
pub mod markdown;
pub mod outbox;
pub mod outbox_item;
pub mod ports;
pub mod quote;
pub mod replies;
pub mod resume;
pub mod sending;
pub mod sending_ports;
pub mod snippets;
pub mod spec;

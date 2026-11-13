//! What stays in the inbox: the classifier, which reads a sender and a subject into a bucket, the person's keep and
//! block rules, which it asks first, and the use cases over them (`mailmind.curation`): the rule book, the
//! Screener, filters and the audit trail.
//!
//! Nothing here reads the disk, the network or the clock: the use cases reach the world through `ports`, which the
//! store and the program implement. The Python app calls the classifier through `mm-py` while it is still the
//! server, and its tests hold each to the answers it gave.

pub mod classifier;
pub mod cleanup;
pub mod filters;
pub mod ports;
pub mod rulebook;
pub mod rules;
pub mod screener;
pub mod sweep_plan;
mod text;

//! Finding mail by what a person types: the grammar of a search, read into what it asks for, and the use cases that
//! answer it (`mailmind.discovery`): search, saved views, contacts, what the box suggests, who is known, the Document Hub
//! and what is said of a sender.
//!
//! Nothing here reads the disk, the network or the clock: the use cases reach the world through `ports`, which the
//! store and the program implement. The Python app calls the grammar through `mm-py` while it is still the server,
//! and its tests hold each to the answers it gave.

pub mod arrival;
pub mod contacts;
// The Document Hub, and what is said of a sender as a message opens.
pub mod documents;
pub mod guard;
pub mod known;
pub mod ports;
pub mod query;
pub mod search;
pub mod senders;
pub mod suggest;
mod text;
pub mod views;
mod words;

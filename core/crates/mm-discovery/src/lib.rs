//! Finding mail by what a person types: the grammar of a search, read into what it asks for.
//!
//! Pure functions: nothing here reads the disk, the network or the clock. The Python app calls them through
//! `mm-py` while it is still the server, and its tests hold each to the answers it gave.

// The Document Hub, and what is said of a sender as a message opens (area B).
pub mod documents;
pub mod ports;
pub mod query;
pub mod senders;
mod text;
mod words;

//! What a message holds: its MIME parts, and its HTML made safe to show.
//!
//! Pure functions: nothing here reads the disk, the network or the clock. The Python app calls them through
//! `mm-py` while it is still the server.

pub mod mime;
pub mod sanitize;

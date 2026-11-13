//! Everything asked of Claude (`mailmind.assistant`), as far as the Rust core has come: how AI stands with no call
//! made, which the digest says.
//!
//! What it needs of the world is in `ports`, which the store and the program implement; nothing here reads the disk,
//! the network or the clock.

pub mod limits;
pub mod ports;
pub mod status;

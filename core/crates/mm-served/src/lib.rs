//! What the test launcher hands the Rust server in place of the world (`main.rs` is the launcher): a frozen clock, a
//! tripwire for its files, hosts and programs, and the fake Keychain. Never shipped.

pub mod clock;
pub mod keychain;
pub mod tripwire;

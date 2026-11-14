//! What the test launcher hands the Rust server in place of the world (`main.rs` is the launcher): a frozen clock, a
//! tripwire for its files, hosts and programs, the fake Keychain, and a sanitizer a test can take away. Never
//! shipped.

pub mod clock;
// The background jobs this server runs.
pub mod jobs;
pub mod keychain;
pub mod tripwire;
// The sanitizer the reader's bodies go through, which a test can take away.
pub mod unsanitized;

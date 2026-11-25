//! What the test launcher hands the Rust server in place of the world (`main.rs` is the launcher): a tripwire for its
//! files, hosts and programs, the fake Keychain, and a sanitizer a test can take away. Its frozen clock and its paused
//! jobs are `mm-testworld`'s, which the Mac app's host shares. Never shipped.

pub mod keychain;
pub mod tripwire;
// The sanitizer the reader's bodies go through, which a test can take away.
pub mod unsanitized;

//! What asks for you later (`mailmind.attention`): a snooze hides a message until it wakes, a mute silences a thread,
//! a follow-up says a thread waits on someone, Reply Later is the stack a person pins, and a task is what they
//! confirmed they have to do.
//!
//! Nothing here reads the disk, the network or the clock: the use cases reach the world through `ports`, which the
//! store and the program implement.

pub mod events;
pub mod followups;
pub mod muting;
pub mod ports;
pub mod snoozing;
pub mod tasks;
pub mod threads;

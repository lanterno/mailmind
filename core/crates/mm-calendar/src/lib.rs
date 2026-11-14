//! Invites, recurring series, Today & next, an answer to an invite and an event file of a person's own
//! (`mailmind.calendar`).
//!
//! Nothing here reads the disk, the network or the clock: the use cases reach the world through `ports`, which the
//! store and the server implement. An invite is a stranger's text, so its reader (`ics`, `invite`) and the expansion
//! of its rule (`rrule`) are written here for what MailMind reads of one, and nothing in them fails or runs long on
//! any text.

pub mod agenda;
pub mod ics;
pub mod invite;
pub mod invites;
pub mod ports;
pub mod publish;
pub mod rrule;
pub mod rsvp;
pub mod time;
mod windows_zones;

//! The Rust server: the Python app's HTTP API and its page, on the core's use cases.
//!
//! A request passes the perimeter (`perimeter`) before any route runs, and every answer carries its headers. A route
//! (`routes`) decodes the request, calls one use case of a context crate, and encodes what it answers: it holds no
//! SQL and no rules of its own. `app` binds the use cases to their adapters, `serve` is the accept loop, and
//! `environment` is the one place the environment is read.
//!
//! The app holds no test hooks. What a test must control comes in through `Seams`: the clock, the Keychain and
//! `Outside`, asked before a file is opened or a host reached. `mailmind-server` passes the real ones; the test
//! launcher (`mm-served`) passes a frozen clock, a Keychain of its own and a tripwire.

pub mod answer;
pub mod app;
pub mod body;
mod command_wiring;
pub mod environment;
pub mod events;
pub mod gates;
pub mod json;
pub mod page;
pub mod perimeter;
pub mod problem;
pub mod query;
pub mod reading;
pub mod real;
pub mod routes;
pub mod schema;
pub mod serve;
mod sync_wiring;
mod wiring;
// Discovery's, Curation's and Attention's use cases bound, and a POST's body as their routes read it.
pub mod bound;
pub mod posted;
// What the contexts ask of the Mailbox's filing and say happened, and the background work.
mod filing_wiring;
mod heard;
pub mod workers;
// The login Keychain, through /usr/bin/security; and the accounts' changes and their test login.
mod account_wiring;
pub mod login_keychain;

pub use app::{App, Seams};
pub use serve::{Server, Work};

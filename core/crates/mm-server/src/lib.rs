//! The Rust server: the Python app's HTTP API and its page, on the core's use cases.
//!
//! A request passes the perimeter (`perimeter`) before any route runs, and every answer carries its headers. A route
//! (`routes`) decodes the request, calls one use case of a context crate, and encodes what it answers: it holds no
//! SQL and no rules of its own. `app` binds the use cases to their adapters, `serve` is the accept loop, and
//! `environment` is the one place the environment is read.
//!
//! The app holds no test hooks. What a test must control comes in through `Seams`: the clock, the Keychain,
//! `Outside`, asked before a file is opened or a host reached, and the sanitizer a body's HTML goes through.
//! `mailmind-server` passes the real ones (`real::seams`); the test launcher (`mm-served`) passes a frozen clock, a
//! Keychain of its own, a tripwire and a sanitizer it can take away.

pub mod answer;
pub mod app;
pub mod body;
// The calendar's use cases, bound.
pub mod calendar_wiring;
// A sender's cleanup, its unsubscribe and a sweep, bound.
pub mod cleanup_wiring;
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
// Compose's use cases bound: the sends, the drafts and the outbox that delivers them, and the outbox's worker.
pub mod compose_wiring;
mod outbox_worker;

// The database's key, and the database made if there is none, at the version this server reads; and `db
// check-upgrade`, what an upgrade would do to it.
mod database_version;
pub mod db_command;
// Every session's login, which a login its server refused waits out.
mod login_wiring;
// IDLE's workers bound: the accounts, their sessions, and their news syncing those accounts.
mod idle_wiring;
// A person's filters, as a sync stores new mail.
mod arrival_wiring;

// The Assistant's use cases bound: the language model for the key in force, the cost governor and every AI call.
pub mod assistant_wiring;

pub use app::{App, Seams};
pub use serve::{Server, Work};

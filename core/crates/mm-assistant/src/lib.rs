//! Everything asked of Claude (`mailmind.assistant`): the gateway every call goes through, the cost governor that
//! decides whether it may go now, the ledger of what each cost, and how AI stands with no call made.
//!
//! What it needs of the world is in `ports`, which the store, the Claude API's crate (`mm-claude`) and the program
//! implement; nothing here reads the disk, the network or the clock.

pub mod calls;
pub mod context;
pub mod digest;
pub mod extract;
pub mod extraction;
pub mod features;
pub mod flights;
pub mod gateway;
pub mod governor;
pub mod limits;
pub mod models;
pub mod ports;
pub mod prepared_replies;
pub mod prepared_reply;
pub mod pricing;
pub mod prompts;
pub mod refusals;
pub mod reply_draft;
pub mod rewrite;
pub mod schema;
pub mod status;
pub mod text;
pub mod thread_digest;

//! What every part of the core shares: how a message is named, how a refusal and a problem are said, and the seams
//! through which a program meets the world.
//!
//! Nothing here reads the disk, the network or the clock.

use std::fmt;

use serde::{Deserialize, Serialize};

pub mod ago;
pub mod errors;
pub mod events;
pub mod identity;
pub mod isoformat;
pub mod log;
pub mod relative;
pub mod seams;
pub mod text;
pub mod when;
pub mod zone;

/// A message's durable name, `message_identity.key`. It survives a move and a resync, which a row number does
/// not, so a row number never leaves the core.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(transparent)]
#[cfg_attr(feature = "ts", derive(ts_rs::TS), ts(export, type = "number"))]
pub struct MessageKey(pub i64);

/// Why the core did not do what it was asked. A refusal is a value: a shell never sees a panic. Its words name
/// what failed and never hold a person's data.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
#[cfg_attr(feature = "ts", derive(ts_rs::TS), ts(export))]
pub enum Refusal {
    /// The database key opens nothing in this file.
    KeyDoesNotFit,
    /// What was named is not there: a view already closed, or a message gone.
    NotFound { what: String },
    /// The request is malformed.
    Invalid { detail: String },
    /// The store failed.
    Storage { detail: String },
}

impl Refusal {
    pub fn invalid(detail: impl Into<String>) -> Refusal {
        Refusal::Invalid { detail: detail.into() }
    }

    pub fn not_found(what: impl Into<String>) -> Refusal {
        Refusal::NotFound { what: what.into() }
    }

    pub fn storage(detail: impl Into<String>) -> Refusal {
        Refusal::Storage { detail: detail.into() }
    }
}

impl fmt::Display for Refusal {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Refusal::KeyDoesNotFit => f.write_str("the database key opens nothing in this file"),
            Refusal::NotFound { what } => write!(f, "{what} is not there"),
            Refusal::Invalid { detail } => write!(f, "invalid request: {detail}"),
            Refusal::Storage { detail } => write!(f, "the store failed: {detail}"),
        }
    }
}

impl std::error::Error for Refusal {}

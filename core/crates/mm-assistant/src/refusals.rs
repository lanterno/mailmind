//! How a call can fail to be an answer (`application.ports`' `NotSent`, `ModelError`), and what an AI request can
//! answer instead of one (`AiFailure`).
//!
//! A call that was never sent is `NotSent`: AI is off, or the cost governor held it back. It isn't a failure, the ledger
//! counts nothing for it, and every caller lets it through ahead of its own handling, so the route can say why: an
//! over-limit call as a 402 the page asks about, AI off as a 409 the page shows as "AI is off".

use mm_kernel::errors::{Failure, Fault, Problem};

use crate::ports::ModelError;

/// Nothing was sent, so no call was made.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum NotSent {
    /// There is no API key.
    AiOff,
    /// An interactive call at the daily limit, which the person hasn't said may go over: what today's calls cost, and
    /// the limit, in micro-dollars.
    OverLimit { spent: i64, limit: i64 },
    /// Background or speculative work that may not start now: "yielding", "away", "limit" or "breaker".
    Deferred(&'static str),
}

/// Why a call came back with no answer.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum CallError {
    NotSent(NotSent),
    /// No reply came: the caller retries later, and nothing is cached.
    Model(ModelError),
}

impl From<NotSent> for CallError {
    fn from(not: NotSent) -> CallError {
        CallError::NotSent(not)
    }
}

/// What an AI request answers when it can't give what was asked: a call never sent, or a use case's failure.
#[derive(Clone, Debug, PartialEq)]
pub enum AiFailure {
    NotSent(NotSent),
    Failure(Failure),
}

impl From<NotSent> for AiFailure {
    fn from(not: NotSent) -> AiFailure {
        AiFailure::NotSent(not)
    }
}

impl From<Failure> for AiFailure {
    fn from(failure: Failure) -> AiFailure {
        AiFailure::Failure(failure)
    }
}

impl From<Problem> for AiFailure {
    fn from(problem: Problem) -> AiFailure {
        AiFailure::Failure(Failure::Problem(problem))
    }
}

impl From<Fault> for AiFailure {
    fn from(fault: Fault) -> AiFailure {
        AiFailure::Failure(Failure::Fault(fault))
    }
}

/// `Unreachable(said, retry=True)`: the model didn't answer, and asking again can help.
pub fn unreachable(said: &str) -> Problem {
    Problem::new(mm_kernel::errors::Kind::Unreachable, said).with("retry", serde_json::Value::Bool(true))
}

/// What a call's failure becomes for a caller that answers a person: a call never sent goes through, and anything else
/// is `unreachable(said)`, logged by `what`.
pub fn answered<T>(made: Result<T, CallError>, what: &'static str, said: &str) -> Result<T, AiFailure> {
    match made {
        Ok(value) => Ok(value),
        Err(CallError::NotSent(not)) => Err(not.into()),
        Err(CallError::Model(_)) => {
            mm_kernel::log::warning("ai", what, &[], Some("ModelError"));
            Err(unreachable(said).into())
        }
    }
}
